"""
core/copilot.py
---------------
The GrabFi Copilot (Layer 2) as a TOOL-CALLING AGENT with a deterministic
grounding gate.

Agentic flow (VERTEX_PROJECT_ID set):
    question
      -> Gemini via Vertex AI (given the typed financial tools in core/tools.py)
      -> model emits function_call parts  -- the model decides which tools to call
      -> we execute each tool deterministically -> typed facts + citations
      -> function_response fed back; loop until the model produces a final answer
      -> verify_answer(): every number in the answer must trace to a fact a
         tool returned; ungrounded numbers BLOCK the answer.

Offline flow (no VERTEX_PROJECT_ID):
    core/grounding.retrieve() acts as the deterministic tool-router, unioning
    the same underlying facts so the app still answers (facts only, verified).

The agent NEVER recalls numbers from model memory: it only phrases what the
tools returned, and the verifier is the hard backstop.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from core import grounding, tools
from core.grounding import Retrieval, VerificationResult, Fact

DEFAULT_MODEL = os.environ.get("COPILOT_MODEL", "gemini-3.5-flash")
MAX_AGENT_STEPS = 5  # tool-use rounds before we stop

AGENT_SYSTEM = """You are GrabFi, an analyst agent for executives reviewing Grab and its loaded peers Uber, Lyft, DoorDash, and Sea (FY2023-FY2025).

You have tools that return figures from official company investor-relations materials, each with a citation. To answer, you MUST call the relevant tool(s) and use ONLY the numbers they return.

ABSOLUTE RULES:
1. Never state a figure you did not get from a tool, except a direct arithmetic derivation of tool figures (a difference, sum, YoY %, or margin) - and show that arithmetic.
2. For Grab "why did X change" or "bridge" questions, call compute_flux_bridge.
3. For peer trends, call get_company_financials; for peer segment questions, call get_company_segment_metrics; for cross-company questions, call compare_companies.
4. Never rank company-defined Adjusted EBITDA or free cash flow in strict mode. Use directional mode and state the tool's comparability note. Never directly rank platform volume or activity counts with different definitions.
5. Distinguish GAAP/IFRS reported metrics from company-defined non-GAAP metrics and surface material one-off caveats returned by tools.
6. If the tools cannot supply what's needed, say exactly what is missing. Do not guess, estimate, or recall from memory.
7. Be concise and analytical: lead with the answer, name the driver, cite the period in $M / %. No filler, no generic disclaimers.
8. You are talking to a financially literate operator."""


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class CopilotResponse:
    answer: str
    retrieval: Retrieval
    verification: VerificationResult | None
    mode: str  # "agent" | "retrieval_only" | "blocked" | "refused"
    model: str | None = None
    citations: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)


def _citations(facts: list[Fact]) -> list[str]:
    seen, out = set(), []
    for f in facts:
        if f.citation not in seen:
            seen.add(f.citation)
            out.append(f.citation)
    return out


def _dedupe(facts: list[Fact]) -> list[Fact]:
    seen, out = set(), []
    for f in facts:
        if f.key not in seen:
            seen.add(f.key)
            out.append(f)
    return out


def _retrieval_only_answer(r: Retrieval) -> str:
    if not r.facts:
        return ("I don't have that in the loaded FY2023-FY2025 dataset for Grab, "
                "Uber, Lyft, DoorDash, and Sea. Try a reported financial metric, "
                "company operating metric, or Grab segment driver.")
    lines = ["Grounded figures for your question "
             "(retrieval-only mode - set VERTEX_PROJECT_ID for agentic narrative answers):", ""]
    lines += [f.as_line() for f in r.facts]
    if r.warnings:
        lines += ["", "Coverage warnings:"] + [f"- {warning}" for warning in r.warnings]
    return "\n".join(lines)


def _json_schema_to_gemini(schema: dict):
    """Convert a JSON Schema dict to google.genai types.Schema."""
    from google.genai import types as gtypes
    t = schema.get("type", "string").upper()
    kwargs = {}
    if t == "OBJECT":
        props = schema.get("properties", {})
        kwargs["properties"] = {k: _json_schema_to_gemini(v) for k, v in props.items()}
        if "required" in schema:
            kwargs["required"] = schema["required"]
    elif t == "ARRAY":
        kwargs["items"] = _json_schema_to_gemini(schema.get("items", {}))
    if "enum" in schema:
        kwargs["enum"] = schema["enum"]
    if "description" in schema:
        kwargs["description"] = schema["description"]
    return gtypes.Schema(type=t, **kwargs)


def _build_gemini_tools():
    """Convert the JSON-Schema tool defs in core/tools.py to Gemini FunctionDeclarations."""
    from google.genai import types as gtypes
    declarations = [
        gtypes.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters=_json_schema_to_gemini(t["input_schema"]),
        )
        for t in tools.TOOLS
    ]
    return [gtypes.Tool(function_declarations=declarations)]


def ask(question: str, model: str | None = None, temperature: float = 0.0) -> CopilotResponse:
    refusal = grounding.refusal_reason(question)
    if refusal:
        return CopilotResponse(
            answer=grounding.refusal_answer(refusal),
            retrieval=Retrieval(),
            verification=None,
            mode="refused",
        )

    project = os.environ.get("VERTEX_PROJECT_ID")
    location = os.environ.get("VERTEX_LOCATION", "global")

    # ---- offline: deterministic tool-router (grounding.retrieve) ----
    if not project:
        r = grounding.retrieve(question)
        return CopilotResponse(
            answer=_retrieval_only_answer(r), retrieval=r, verification=None,
            mode="retrieval_only", citations=_citations(r.facts),
        )

    # ---- agentic: Gemini via Vertex AI chooses tools ----
    model = model or DEFAULT_MODEL
    collected: list[Fact] = []
    trace: list[ToolCall] = []
    try:
        from google import genai
        from google.genai import types as gtypes

        client = genai.Client(vertexai=True, project=project, location=location)
        gemini_tools = _build_gemini_tools()
        messages = [gtypes.Content(role="user", parts=[gtypes.Part.from_text(text=question)])]
        final_text = ""

        for _ in range(MAX_AGENT_STEPS):
            response = client.models.generate_content(
                model=model,
                contents=messages,
                config=gtypes.GenerateContentConfig(
                    system_instruction=AGENT_SYSTEM,
                    tools=gemini_tools,
                    temperature=temperature,
                ),
            )
            candidate = response.candidates[0]
            messages.append(candidate.content)

            fn_calls = [p for p in candidate.content.parts if p.function_call]

            if not fn_calls:  # model finished — collect text
                final_text = "".join(
                    p.text for p in candidate.content.parts
                    if hasattr(p, "text") and p.text
                ).strip()
                if not final_text:
                    raise RuntimeError("Model returned no answer text")
                break

            fn_responses = []
            for part in fn_calls:
                fn_name = part.function_call.name
                fn_args = dict(part.function_call.args)
                text, facts = tools.execute_tool(fn_name, fn_args)
                collected.extend(facts)
                trace.append(ToolCall(fn_name, fn_args))
                fn_responses.append(gtypes.Part.from_function_response(
                    name=fn_name, response={"result": text},
                ))
            messages.append(gtypes.Content(role="user", parts=fn_responses))
        else:
            # The model used every round to gather facts. Force a final answer
            # without exposing tools so step exhaustion cannot pass verification
            # as a successful, number-free response.
            messages.append(gtypes.Content(
                role="user",
                parts=[gtypes.Part.from_text(text=(
                    "Tool collection is complete. Answer the original question now "
                    "using only the tool results already present in this conversation. "
                    "Do not request more tools and do not mention the step limit."
                ))],
            ))
            synthesis = client.models.generate_content(
                model=model,
                contents=messages,
                config=gtypes.GenerateContentConfig(
                    system_instruction=AGENT_SYSTEM,
                    temperature=temperature,
                ),
            )
            final_text = "".join(
                p.text for p in synthesis.candidates[0].content.parts
                if hasattr(p, "text") and p.text
            ).strip()
            if not final_text:
                raise RuntimeError("Final synthesis returned no text")

    except Exception as e:  # network / auth / SDK error -> safe deterministic fallback
        # Log the full error for operators; the user-facing answer stays clean and
        # never leaks internal exception text (only the error class).
        logging.getLogger(__name__).warning("Copilot LLM call failed: %r", e)
        facts = _dedupe(collected)
        r = Retrieval(facts=facts, matched_years=[]) if facts else grounding.retrieve(question)
        return CopilotResponse(
            answer=f"(LLM unavailable: {type(e).__name__}. Showing grounded facts.)\n\n"
                   f"{_retrieval_only_answer(r)}",
            retrieval=r, verification=None, mode="retrieval_only",
            citations=_citations(r.facts),
        )

    facts = _dedupe(collected)
    if not facts:
        # A tool-calling answer without tool evidence violates the core contract,
        # even when it happens to contain no numeric tokens.
        r = grounding.retrieve(question)
        return CopilotResponse(
            answer="(The model returned no tool evidence. Showing grounded facts.)\n\n"
                   f"{_retrieval_only_answer(r)}",
            retrieval=r, verification=None, mode="retrieval_only",
            model=model, citations=_citations(r.facts), tool_calls=trace,
        )
    retrieval = Retrieval(facts=facts, matched_years=[])
    citations = _citations(facts)
    v = grounding.verify_answer(final_text, retrieval)

    if not v.ok:
        # First attempt contained ungrounded figures — give the model one correction pass.
        try:
            correction = (
                f"Your previous answer contained figure(s) not found in the tool outputs "
                f"and not directly derivable from them: {sorted(set(v.ungrounded))}. "
                "Rewrite your answer using ONLY the exact values the tools returned. "
                "Do not compute any additional statistics, ratios, or comparisons "
                "beyond what the tool text explicitly states."
            )
            messages.append(gtypes.Content(
                role="user", parts=[gtypes.Part.from_text(text=correction)]
            ))
            retry_resp = client.models.generate_content(
                model=model,
                contents=messages,
                config=gtypes.GenerateContentConfig(
                    system_instruction=AGENT_SYSTEM,
                    tools=gemini_tools,
                    temperature=temperature,
                ),
            )
            retry_text = "".join(
                p.text for p in retry_resp.candidates[0].content.parts
                if hasattr(p, "text") and p.text
            ).strip()
            retry_v = grounding.verify_answer(retry_text, retrieval)
            if retry_v.ok:
                return CopilotResponse(answer=retry_text, retrieval=retrieval,
                                       verification=retry_v, mode="agent", model=model,
                                       citations=citations, tool_calls=trace)
            v, final_text = retry_v, retry_text
        except Exception:
            pass  # fall through to block on the original answer

        blocked = (
            "Grounding check failed - the drafted answer contained figure(s) not "
            f"traceable to tool outputs ({sorted(set(v.ungrounded))}), so it was "
            "withheld. Verified figures the tools returned:\n\n"
            + _retrieval_only_answer(retrieval)
        )
        return CopilotResponse(answer=blocked, retrieval=retrieval, verification=v,
                               mode="blocked", model=model, citations=citations,
                               tool_calls=trace)

    return CopilotResponse(answer=final_text, retrieval=retrieval, verification=v,
                           mode="agent", model=model, citations=citations,
                           tool_calls=trace)


if __name__ == "__main__":
    for q in ["What drove the adjusted EBITDA improvement from 2024 to 2025?",
              "How profitable is Mobility vs Deliveries?"]:
        resp = ask(q)
        print(f"\nQ: {q}\n[mode={resp.mode}]\n{resp.answer}\nCITES: {resp.citations}")

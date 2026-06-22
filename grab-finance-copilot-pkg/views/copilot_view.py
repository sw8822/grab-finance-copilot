"""
views/copilot_view.py — Layer 2: GrabFi Copilot chat UI.
Calls core.copilot.ask(); renders tool-call trace, citations, facts, and
verification result on every answer.
"""
from __future__ import annotations

import re

import streamlit as st

from core.copilot import ask, CopilotResponse

EXAMPLE_QUESTIONS = [
    "Why did Grab turn profitable in 2025?",
    "Compare Mobility vs Deliveries margins across all three years.",
    "How fast is the lending book growing and what's it costing?",
    "Bridge the EBITDA change from 2024 to 2025.",
    "Compare Grab and its peers' FY2025 revenue.",
]

_CURRENCY_DOLLAR_RE = re.compile(r"(?<!\\)\$(?=\s*-?\d)")


def _escape_currency_markdown(text: str) -> str:
    """Prevent Streamlit from interpreting currency as LaTeX math."""
    return _CURRENCY_DOLLAR_RE.sub(r"\\$", text)


def _mode_badge(mode: str) -> str:
    if mode == "agent":
        return "🟢 **agent** — LLM chose tools, answer verified"
    if mode == "retrieval_only":
        return "🟡 **retrieval-only** — LLM not used; deterministic facts returned"
    if mode == "refused":
        return "⚪ **refused** — question is outside the loaded evidence scope"
    return "🔴 **blocked** — grounding check failed; answer withheld"


def _render_transparency(resp: CopilotResponse) -> None:
    st.markdown(_mode_badge(resp.mode))

    if resp.model:
        st.caption(f"Model: {resp.model}")

    if resp.tool_calls:
        with st.expander(f"🔧 Tool-call trace ({len(resp.tool_calls)} call(s))", expanded=False):
            for tc in resp.tool_calls:
                st.code(f"{tc.name}({tc.args})", language="python")

    if resp.citations:
        with st.expander("📎 Citations", expanded=False):
            for c in resp.citations:
                st.caption(_escape_currency_markdown(f"• {c}"))

    if resp.retrieval and resp.retrieval.facts:
        with st.expander(f"📋 Facts used ({len(resp.retrieval.facts)} facts)", expanded=False):
            for f in resp.retrieval.facts:
                st.caption(_escape_currency_markdown(f.as_line()))

    if resp.verification is not None:
        v = resp.verification
        if v.ok:
            st.success(f"✅ Verification PASS — {v.checked} number(s) checked, all grounded.")
        else:
            st.error(
                f"❌ Verification FAIL — {len(v.ungrounded)} ungrounded figure(s): "
                f"{sorted(set(v.ungrounded))}. Answer was blocked."
            )


def render(model: str) -> None:
    st.header("GrabFi Copilot", divider="green")
    st.markdown(
        "Ask about **Grab, Uber, Lyft, DoorDash, and Sea (FY2023–FY2025)**. "
        "Every answer is grounded in official filings or IR releases; numbers are verified before display."
    )

    # Example chips
    st.markdown("**Try these:**")
    chip_cols = st.columns(len(EXAMPLE_QUESTIONS))
    for i, q in enumerate(EXAMPLE_QUESTIONS):
        if chip_cols[i].button(q, key=f"chip_{i}", width="stretch"):
            st.session_state["copilot_input"] = q

    # Initialise session state
    if "copilot_messages" not in st.session_state:
        st.session_state["copilot_messages"] = []

    # Render existing thread
    for msg in st.session_state["copilot_messages"]:
        with st.chat_message(msg["role"]):
            content = msg["content"]
            if msg["role"] == "assistant":
                content = _escape_currency_markdown(content)
            st.markdown(content)
            if msg["role"] == "assistant" and "resp" in msg:
                _render_transparency(msg["resp"])

    # Chat input — also accepts pre-filled value from chips
    prefill = st.session_state.pop("copilot_input", "")
    user_input = st.chat_input(
        "Ask about Grab or loaded peers (FY2023–FY2025)…",
        key="chat_input_box",
    ) or prefill

    if user_input:
        # Add user message
        st.session_state["copilot_messages"].append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # Call copilot
        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                resp = ask(user_input, model=model)

            if resp.mode == "retrieval_only":
                if resp.answer.startswith("(LLM unavailable"):
                    st.warning("Vertex AI call failed — showing grounded facts instead. "
                               "Check that `VERTEX_PROJECT_ID` and `GOOGLE_APPLICATION_CREDENTIALS` "
                               "are set correctly and the model name is valid.")
                else:
                    st.info("No `VERTEX_PROJECT_ID` set — running in retrieval-only mode. "
                            "Add it to `.env` to enable full agentic answers via Vertex AI.")

            st.markdown(_escape_currency_markdown(resp.answer))
            _render_transparency(resp)

        st.session_state["copilot_messages"].append({
            "role": "assistant", "content": resp.answer, "resp": resp,
        })

    if st.session_state.get("copilot_messages"):
        if st.button("🗑️ Clear conversation", key="clear_chat"):
            st.session_state["copilot_messages"] = []
            st.rerun()

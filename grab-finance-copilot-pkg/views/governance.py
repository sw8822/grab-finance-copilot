"""Layer 3: visual governance, evidence coverage, and production control design."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from core import data_loader as dl
from core import peer_data_loader as pdl
from core import tools

GREEN = "#00B14F"
GREEN_DARK = "#007A36"
ORANGE = "#F59E0B"
INK = "#1C1C1E"
LIGHT = "#E5E7EB"

# Proposed production deployment on AWS (Grab's estate). Rendered by st.graphviz_chart.
PROD_ARCH_DOT = r"""
digraph prod {
  rankdir=TB; bgcolor="transparent"; fontname="Arial"; compound=true;
  node [shape=box style="rounded,filled" fontname="Arial" fontsize=11 color="#D0D5DD"];
  edge [color="#98A2B3" fontname="Arial" fontsize=9];

  user [label="Finance user / Executive" shape=oval fillcolor="#E8F5E9" color="#00B14F"];

  subgraph cluster_edge {
    label="Edge & Identity"; style="rounded,dashed"; color="#B0B7C3"; fontname="Arial";
    sso      [label="Corporate SSO / OIDC\n(IAM Identity Center)" fillcolor="#FFF4E5"];
    edge_alb [label="CloudFront + WAF + ALB (TLS)" fillcolor="#FFE7C2"];
  }

  subgraph cluster_vpc {
    label="AWS — Grab production account (VPC)"; style="rounded"; color="#F59E0B"; fontname="Arial"; fontsize=12;
    subgraph cluster_app {
      label="ECS Fargate · private subnet · autoscaled"; style="rounded,dashed"; color="#00B14F"; fontname="Arial";
      app    [label="Streamlit app\nDashboard | Copilot | Governance" fillcolor="#00B14F" fontcolor="white" color="#007A36"];
      engine [label="core/ engine\ntyped tools (run in-process — no network)\n+ grounding verifier" fillcolor="#33CC70" fontcolor="white" color="#007A36"];
    }
    s3      [label="Amazon S3 (versioned)\ngrab_financials.json + peers/*.json" fillcolor="#FFE7C2"];
    secrets [label="AWS Secrets Manager\nmodel credentials + config\n(read via ECS task IAM role)" fillcolor="#FFE7C2"];
    logs    [label="CloudWatch + CloudTrail\nper-session audit trail" fillcolor="#FFE7C2"];
    egress  [label="Private egress to the model\nVPC endpoint / PrivateLink (e.g. Bedrock)\nor internal network (internal LLM)\nNAT only if an external API is used" fillcolor="#FFE7C2"];
  }

  model [label="Model endpoint — provider TBD\nProd: Grab-approved service\n(e.g. Amazon Bedrock or internal LLM)\nDemo only: Vertex AI Gemini\nonly typed numeric facts sent" fillcolor="#E3F0FF" color="#1A73E8"];

  subgraph cluster_cicd {
    label="CI/CD"; style="rounded,dashed"; color="#B0B7C3"; fontname="Arial";
    gh   [label="GitHub repo" fillcolor="#F2F4F7"];
    pipe [label="CodePipeline / CodeBuild\neval/run_eval.py gate -> ECR -> ECS" fillcolor="#FFE7C2"];
  }

  user -> sso -> edge_alb -> app;
  app -> engine;
  engine -> s3 [label="load + 13 integrity checks"];
  app -> secrets [label="fetch creds"];
  engine -> egress -> model [label="LLM call (the only egress)"];
  app -> logs [label="audit"];
  gh -> pipe;
  pipe -> app [label="deploy image" style=dashed];
}
"""

# The grounded request path: scope gate -> tool-calling -> numeric verifier.
FLOW_DOT = r"""
digraph flow {
  rankdir=TB; bgcolor="transparent";
  node [shape=box style="rounded,filled" fontname="Arial" fontsize=11 fillcolor="#F2F4F7" color="#D0D5DD"];
  edge [color="#98A2B3" fontname="Arial" fontsize=9];

  q       [label="Executive question" shape=oval fillcolor="#E8F5E9" color="#00B14F"];
  gate    [label="Scope gate (refusal_reason)\nforecast? unloaded company? year out of range?" shape=diamond fillcolor="#FFF4E5" color="#F59E0B"];
  refuse  [label="Refuse politely — no numbers returned" fillcolor="#FDE3E3" color="#D92D20"];
  llm     [label="Model, temp 0\ngiven typed tool schemas\n(demo: Gemini via Vertex)" fillcolor="#E3F0FF" color="#1A73E8"];
  exec    [label="execute_tool()\ndeterministic read of dataset" fillcolor="#00B14F" fontcolor="white" color="#007A36"];
  facts   [label="facts + citations\n(no model recall)" fillcolor="#DDF5E7" color="#00B14F"];
  draft   [label="draft answer"];
  verify  [label="verify_answer()\nevery number traces to a fact\nor a permitted derivation?" shape=diamond fillcolor="#FFF4E5" color="#F59E0B"];
  ok      [label="Answer + tool trace + citations (PASS)" fillcolor="#DDF5E7" color="#00B14F"];
  retry   [label="One correction pass\n(name the ungrounded figures)" fillcolor="#FEF7C3" color="#CA8504"];
  blocked [label="BLOCKED — show verified tool facts only" fillcolor="#FDE3E3" color="#D92D20"];

  q -> gate;
  gate -> refuse [label="out of scope"];
  gate -> llm [label="in scope"];
  llm -> exec [label="function_call"];
  exec -> facts;
  facts -> llm [label="function_response (loop <= 5)" style=dashed];
  llm -> draft [label="final text"];
  draft -> verify;
  verify -> ok [label="all grounded"];
  verify -> retry [label="ungrounded figure"];
  retry -> verify [label="re-verify"];
  retry -> blocked [label="still fails"];
}
"""

# How official IR data is ingested, validated, versioned, and picked up by the app.
DATA_PIPELINE_DOT = r"""
digraph data {
  rankdir=TB; bgcolor="transparent"; fontname="Arial";
  node [shape=box style="rounded,filled" fontname="Arial" fontsize=11 fillcolor="#F2F4F7" color="#D0D5DD"];
  edge [color="#98A2B3" fontname="Arial" fontsize=9];

  ir      [label="Official IR release\nGrab 6-K / peer IR" shape=oval fillcolor="#E8F5E9" color="#00B14F"];
  extract [label="Extract key figures (analyst / assisted)\nvalue + citation + basis + precision + effective date"];
  cand    [label="Candidate dataset\n(same JSON fact schema)" fillcolor="#FEF7C3" color="#CA8504"];
  gate    [label="Validation gate — fail-closed\nconsistency tie-outs | official-host + quarterly checks\neval/run_eval.py | tool audit" shape=diamond fillcolor="#FFF4E5" color="#F59E0B"];
  fix     [label="Back to analyst — fix & resubmit" fillcolor="#FDE3E3" color="#D92D20"];
  review  [label="4-eyes review\n(figure / definition changes)" fillcolor="#FEF7C3" color="#CA8504"];

  subgraph cluster_store {
    label="Versioned store (immutable, AWS)"; style="rounded"; color="#F59E0B"; fontname="Arial";
    store [label="S3 (versioned) or Aurora\nappend-only, effective-dated facts\nrestatement = new version, never overwritten" fillcolor="#FFE7C2"];
    pdfs  [label="Raw IR PDFs in S3\n(provenance archive)" fillcolor="#FFE7C2"];
  }

  pointer [label="Publish: flip current-version pointer\n(e.g. dataset_version = 2026Q1)" fillcolor="#DDF5E7" color="#00B14F"];

  subgraph cluster_serve {
    label="App — data contract unchanged"; style="rounded,dashed"; color="#00B14F"; fontname="Arial";
    dal [label="Thin data-access layer\n-> Fact objects" fillcolor="#33CC70" fontcolor="white" color="#007A36"];
    app [label="tools + grounding verifier\nst.cache keyed by version -> auto-refresh" fillcolor="#00B14F" fontcolor="white" color="#007A36"];
  }

  ir -> extract -> cand -> gate;
  gate -> fix [label="fail"];
  fix -> extract [style=dashed];
  gate -> review [label="pass"];
  review -> store [label="promote new version"];
  ir -> pdfs [label="archive" style=dashed];
  store -> pointer -> dal -> app;
}
"""


def _live_summary() -> dict[str, int]:
    grab_checks = dl.consistency_report()
    peer_checks = pdl.validation_report()
    sources = len(dl.meta()["sources"]) + sum(
        len(pdl.load_peer(company)["sources"]) for company in pdl.PEER_FILES
    )
    return {
        "companies": len(pdl.COMPANIES),
        "source_periods": sources,
        "tools": len(tools.TOOLS),
        "checks_passed": sum(ok for _, ok, _ in grab_checks) + sum(ok for _, ok, _ in peer_checks),
        "checks_total": len(grab_checks) + len(peer_checks),
    }


def _source_coverage_figure() -> go.Figure:
    companies = pdl.COMPANIES
    release_text: list[list[str]] = []
    hover_text: list[list[str]] = []
    z: list[list[int]] = []

    for company in companies:
        if company == "Grab":
            sources = dl.meta()["sources"]
            release_text.append(["Official filing" if year in sources else "Missing" for year in dl.YEARS])
            hover_text.append([
                f"Grab · {year}<br>{sources[year]['form']}<br>Filed {sources[year]['filed']}"
                if year in sources else f"Grab · {year}<br>Missing source"
                for year in dl.YEARS
            ])
        else:
            sources = pdl.load_peer(company)["sources"]
            release_text.append(["Official IR" if year in sources else "Missing" for year in dl.YEARS])
            hover_text.append([
                f"{company} · {year}<br>Official IR release<br>Released {sources[year]['released']}"
                if year in sources else f"{company} · {year}<br>Missing source"
                for year in dl.YEARS
            ])
        z.append([int(year in sources) for year in dl.YEARS])

    fig = go.Figure(go.Heatmap(
        z=z,
        x=dl.YEARS,
        y=companies,
        text=release_text,
        customdata=hover_text,
        texttemplate="%{text}",
        hovertemplate="%{customdata}<extra></extra>",
        colorscale=[[0, LIGHT], [0.499, LIGHT], [0.5, "#DDF5E7"], [1, "#DDF5E7"]],
        showscale=False,
        xgap=5,
        ygap=5,
    ))
    fig.update_traces(textfont={"color": GREEN_DARK, "size": 12})
    fig.update_layout(
        height=310,
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis={"side": "top", "fixedrange": True},
        yaxis={"autorange": "reversed", "fixedrange": True},
        font={"family": "Arial, sans-serif", "color": INK},
    )
    return fig


def render() -> None:
    summary = _live_summary()
    st.header("Governance & Scale", divider="green")
    st.markdown(
        "A visual control map of what the interview demo enforces today, the evidence "
        "behind every answer, and the controls required before production use."
    )

    kpi_cols = st.columns(4)
    kpi_cols[0].metric("Loaded companies", summary["companies"])
    kpi_cols[0].caption("Grab + 4 peers")
    kpi_cols[1].metric("Official source periods", summary["source_periods"])
    kpi_cols[1].caption("3 annual periods per company")
    kpi_cols[2].metric("Typed financial tools", summary["tools"])
    kpi_cols[2].caption("Read-only and enum-constrained")
    kpi_cols[3].metric("Live validation", f"{summary['checks_passed']}/{summary['checks_total']}")
    kpi_cols[3].caption(
        "All checks passing" if summary["checks_passed"] == summary["checks_total"] else "Action required"
    )

    st.divider()
    st.subheader("Evidence Coverage")
    st.caption(
        "Official annual filing or investor-relations source-period coverage. "
        "This confirms provenance availability, not equal metric depth across companies. Hover for release details."
    )
    st.plotly_chart(_source_coverage_figure(), width="stretch", config={"displayModeBar": False})

    with st.expander("Open official source links"):
        grab_sources = dl.meta()["sources"]
        st.markdown("**Grab**")
        for year, source in grab_sources.items():
            st.markdown(f"- [{year}: {source['form']}, filed {source['filed']}]({source['url']})")
        for company in pdl.PEER_FILES:
            st.markdown(f"**{company}**")
            for year, source in pdl.load_peer(company)["sources"].items():
                st.markdown(f"- [{year}: released {source['released']}]({source['url']})")

    st.divider()
    st.subheader("Proposed Production Architecture")
    st.caption(
        "How this app would run in production on AWS — Grab's estate. The app is containerised "
        "on ECS Fargate behind SSO; data and secrets stay in the VPC. The model provider is not "
        "fixed — production would use a Grab-approved service (e.g. Amazon Bedrock or an internal "
        "LLM); Vertex AI is used for this demo only. Tools run in-process, so the LLM call is the "
        "only thing that leaves the VPC, and only typed numeric facts are sent."
    )
    st.graphviz_chart(PROD_ARCH_DOT, use_container_width=True)

    st.divider()
    st.subheader("Data Pipeline & Lineage")
    st.caption(
        "How official IR data is kept current. Each new filing becomes a candidate dataset that must "
        "pass a fail-closed validation gate (tie-outs, official-host + quarterly checks, eval, tool audit) "
        "and 4-eyes review before promotion to an immutable, versioned store. Restatements create a new "
        "effective-dated version rather than overwriting. The app reads via a thin data-access layer, so "
        "the tool + verifier contract never changes — only the published version pointer flips."
    )
    st.graphviz_chart(DATA_PIPELINE_DOT, use_container_width=True)

    st.divider()
    st.subheader("Request & Grounding Flow")
    st.caption(
        "Every question passes a deterministic scope gate, is answered only from cited tool facts, "
        "and is numerically verified before display — any ungrounded number is blocked, not shown."
    )
    st.graphviz_chart(FLOW_DOT, use_container_width=True)

    st.divider()
    st.subheader("Detailed Governance Specification")
    st.caption("The visuals above summarize these controls; this section preserves the full implementation and production design detail.")
    guardrails_tab, access_tab, compliance_tab, evaluation_tab = st.tabs([
        "Agent Guardrails", "Access & Security", "Compliance & Audit", "Evaluation Method",
    ])

    with guardrails_tab:
        st.markdown("""
**Input layer**
- Loaded scope is limited to Grab, Uber, Lyft, DoorDash, and Sea for FY2023–FY2025; unsupported companies, periods, and forecasts are refused before model execution.
- Questions remain user messages and cannot modify the system instruction; the tool allowlist limits the effect of hostile or prompt-injection instructions.
- Company, year, segment, field, metric, and comparison-mode arguments use enums so invalid requests are rejected at the schema boundary.

**Tool layer**
- The registered typed tools are deterministic Python functions over validated local JSON datasets. They perform no runtime web retrieval and do not depend on model recall.
- Tools are read-only and side-effect free: they cannot modify datasets or reach outside the loaded evidence package.
- The agent loop is bounded to five tool rounds, followed by a forced synthesis attempt.
- Cross-company comparison policy distinguishes reported-comparable metrics from directional non-GAAP comparisons.
- Adjusted EBITDA and free cash flow require directional mode with a definition warning; incompatible platform-volume definitions are not ranked as equivalent.

**Grounding layer**
- The model receives numbers only through tool responses; every fact carries a citation.
- Generated numeric claims are extracted and checked against retrieved values within the configured tolerance.
- Permitted arithmetic derivations include differences, sums, year-over-year percentages, and margins computed from grounded values.
- Deterministic retrieval and pinned datasets make the evidence reproducible, although model wording can still vary.

**Output layer**
- The numeric verification gate blocks generated answers containing ungrounded figures and falls back to verified tool facts.
- Agent responses expose mode, model, tool-call trace, citations, facts used, and verification status.
- Retrieval-only mode displays deterministic facts directly without an LLM generation step.
- Material one-off items, rounded values, quarterly-derived totals, and comparability limitations remain attached to the returned facts.
""")
        st.info(
            "**Why typed tools instead of vector-RAG?** The evidence is a compact structured numeric corpus. "
            "Exact deterministic lookup avoids similarity-search ambiguity, while the post-generation verifier "
            "protects against model transcription or arithmetic errors. The trade-off is less free-form document exploration."
        )

    with access_tab:
        st.warning("Production architecture below is a target design, not an implementation claim for this public-data interview demo.")
        st.markdown("""
**Authentication and authorization**
- Corporate SSO/OIDC for all users; no shared credentials.
- Role-based access control using data-classification tags such as `public-filing` and `pre-release-MNPI`.
- Row-level and period-level entitlements are enforced in retrieval, not through prompt instructions.
- Unreleased or MNPI datasets are physically partitioned and never loaded into an unauthorized session.

**Secrets and key management**
- Credentials are stored in AWS Secrets Manager or SSM Parameter Store, never committed or baked into an image.
- The runtime assumes a least-privilege IAM role; model-provider credentials are delivered at runtime and rotated on policy.
- The model service account is isolated to the Copilot project with quota and billing limits.
- Per-user rate limits protect against abuse and uncontrolled model spend.

**Data handling and model routing**
- Only typed numeric facts and provenance are sent to the model; raw filing documents, PII, and MNPI are excluded from the prompt path.
- The demo uses Vertex AI's `global` endpoint for availability; this does not guarantee data residency.
- Production uses whatever model service Grab approves (e.g. Amazon Bedrock or an internal LLM), routed through that provider's regional / residency-compliant endpoint when policy requires it.
- Provider enterprise terms must prohibit training on inputs and satisfy the required retention policy.

**Hosting and network controls**
- Target deployment is AWS ECS Fargate, App Runner, or EKS behind an ALB and corporate SSO.
- Private networking, outbound allowlists, TLS, WAF/rate limiting, centralized logs, and workload identity are enforced at the platform layer.
- The model client remains behind `core/copilot.py`, so the approved provider or endpoint can change without changing the tool and verification contract.
""")

    with compliance_tab:
        deliveries_recast = dl.meta()["restatements"]["deliveries_segment_adj_ebitda_fy2023"]
        afcf_recast = dl.meta()["restatements"]["adjusted_free_cash_flow_fy2024"]
        st.markdown(f"""
**Decision support, not system of record**
- Every financial fact traces to a Grab filing or official peer investor-relations release.
- Outputs must retain metric basis, definition, rounding status, derivation status, acquisition effects, and material one-off caveats.
- Forecasts and unsupported periods are refused because they are outside the loaded evidence set.
- Users must verify against the linked source before investment decisions, external reporting, or accounting reliance.

**Audit and reproducibility**
- The demo displays the question, mode, model, tool calls and arguments, facts, citations, answer, and verification result within the current Streamlit session.
- The demo does not claim persistent user-attributed audit storage.
- Production requires append-only audit events containing timestamp, authenticated user, model/version, dataset version, tool inputs, returned facts, citations, final answer, and verification outcome.
- Production retention, legal hold, evidence export, access review, and insider-trading surveillance policies are applied to those events.

**Restatements and lineage**
- Grab's {deliveries_recast['period']} Deliveries Segment Adjusted EBITDA recast from USD {deliveries_recast['original_value']}M to USD {deliveries_recast['recast_value']}M is explicitly recorded and surfaced for like-for-like analysis.
- Grab's {afcf_recast['period']} Adjusted Free Cash Flow recast from USD {afcf_recast['original_value']}M to USD {afcf_recast['recast_value']}M follows the later comparative definition and is stored as structured restatement metadata.
- Peer facts retain original reported names, basis, precision, official source, and fact-level caveats.
- DoorDash annual totals summed from official quarterly IR values retain all four components and must arithmetically tie to the stored annual value.
- Source precedence uses the newest official comparative release when it explicitly recasts or redefines a prior period; the change must remain documented.

**Change management**
- Grab runs nine financial tie-out checks; each peer file validates official host, FY2023–FY2025 source coverage, numeric types, valid source keys, and quarterly derivations.
- Dataset, metric-definition, tool-schema, prompt, and model changes require `pytest` and `eval/run_eval.py` before promotion.
- Production CI should fail closed on validation or evaluation failure and require reviewer approval for source or definition changes.
- Model upgrades require regression comparison, cost/latency review, and controlled rollout before becoming the default.
""")

    with evaluation_tab:
        st.markdown("""
**Golden-question set**
- **Answerable:** exact group, segment, operating, profitability-driver, and peer-comparison facts.
- **Adversarial:** deliberately injected wrong numbers that the verifier must block.
- **Should refuse:** unsupported company, period, and forecast requests that must return no fabricated financial figures.

**Regression metrics**

| Metric | Definition | Required threshold |
|---|---|---:|
| Retrieval recall | Expected facts surfaced for answerable questions | 100% |
| Adversarial block rate | Injected numeric hallucinations rejected | 100% |
| Refusal correctness | Unsupported requests refused without fabricated figures | 100% |
| Dataset validation | Grab tie-outs and all peer validations pass | 100% |
| Citation coverage | Returned facts retain source citations | 100% |

**Executable gate**

- **Retrieval recall:** every expected fact must be surfaced for answerable questions.
- **Adversarial blocking:** injected financial numbers must fail numeric verification.
- **Refusal correctness:** unsupported company, period, and forecast questions must return no fabricated figures.
- **Dataset validation:** Grab's nine financial tie-outs and all four peer dataset validations must pass.

`eval/run_eval.py` exits non-zero on failure. The demo provides the executable gate; production CI must invoke it before dataset, prompt, tool, or model promotion.

**Production evaluation extensions**
- Add semantic-answer quality scoring for materiality, caveat coverage, and executive usefulness.
- Track latency, tool-call count, token cost, fallback rate, verification failures, and refusal false positives.
- Maintain a red-team set for prompt injection, source-conflict handling, restatements, malicious citations, and MNPI access attempts.
- Require human review for new metric definitions, comparability mappings, and production release candidates.
""")

    st.divider()
    st.subheader("Live Validation Detail")
    grab_checks = dl.consistency_report()
    peer_checks = pdl.validation_report()
    for name, ok, detail in grab_checks:
        st.caption(f"{'PASS' if ok else 'FAIL'} · {name} — {detail}")
    for company, ok, detail in peer_checks:
        st.caption(f"{'PASS' if ok else 'FAIL'} · {company} peer dataset — {detail}")

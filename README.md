# GrabFi

A financial intelligence app for **Grab Holdings (NASDAQ: GRAB)** with benchmark data for **Uber, Lyft, DoorDash, and Sea**, FY2023–FY2025. Built for the *Finance Solutions Excellence* case study with three layers:

| Tab | Layer | What it does |
|---|---|---|
| 📊 **Finance & Flux** | 1 — *The What* | Revenue, margins, and segment Adjusted EBITDA over 3 years, with a waterfall **flux bridge** that isolates the exact drivers of each change — and an auditable trail back to the source filings. |
| 🤖 **GrabFi Copilot** | 2 — *The How* | Ask natural-language questions. Generated numeric claims are grounded in retrieved filing facts and checked before display. |
| 🛡️ **Governance & Scale** | 3 — *The So What* | Guardrails, access controls, and evaluation methodology for handling sensitive financial data at scale. |

> **Source of truth:** Grab figures trace to `data/grab_financials.json`; peer figures trace to one official-IR-backed JSON file per company under `data/peers/`. `peer_source_inventory.json` distinguishes complete source coverage from the currently extracted common-metric layer. Every Copilot fact retains provenance and comparison rules come from `data/peer_metric_catalog.json`.

> **Data cadence (demo vs production):** this demo uses **annual** (FY2023–FY2025) figures from public investor-relations filings — the audited headline basis. In production for the executive audience, I would run it at **quarterly and monthly** granularity on Grab's internal data, for finer driver/seasonality analysis and timelier decisions. The period dimension is data-driven, so a finer cadence is a data change, not a re-architecture (and "months → quarter → year" tie-outs become additional integrity checks).

---

## Quickstart (local)

```bash
# 0. enter the app folder (the runnable app lives here)
cd grab-finance-copilot-pkg

# 1. install
pip install -r requirements.txt

# 2. configure Vertex AI (optional — see "Run modes" below)
# create a .env (gitignored) with:
#   VERTEX_PROJECT_ID=your-gcp-project
#   VERTEX_LOCATION=global
#   GOOGLE_APPLICATION_CREDENTIALS=/abs/path/to/secrets/sa.json
#   COPILOT_MODEL=gemini-3.5-flash

# 3. run
streamlit run app.py
```

App opens at `http://localhost:8501`.

---

## Run modes

The app runs **with or without** Vertex AI configured:

- **Agent mode** (`VERTEX_PROJECT_ID` set) — the copilot calls typed financial tools via **Vertex AI Gemini** and returns natural-language analytical answers, each with a tool-call trace, citations, the facts the tools returned, and a pass/fail grounding check.
- **Retrieval-only mode** (no Vertex config) — the copilot returns the verified figures directly (no LLM phrasing). The dashboard and grounding logic work fully offline. Useful for demos and for inspecting exactly what the model would be grounded on.

Config is read from environment variables **or** `st.secrets`. Credentials come from a GCP service-account JSON (`GOOGLE_APPLICATION_CREDENTIALS`); in production these are delivered from **AWS Secrets Manager**, never committed.

### Model selection

Defaults to `gemini-3.5-flash` on the Vertex AI `global` endpoint. Override with `COPILOT_MODEL` and `VERTEX_LOCATION`; the configured model becomes the first sidebar option and `gemini-2.5-flash` remains available as a fallback.

The global endpoint improves availability and reduces regional quota pressure, but it does not guarantee data residency. **Vertex is used for this demo only** — in production the model provider is whatever Grab approves under its compliance policy (e.g. Amazon Bedrock or an internal LLM), with the required residency and data-use controls.

---

## How the copilot works (tool-calling agent + grounding gate)

The copilot is an **LLM agent that calls typed financial tools** — it never recalls figures from memory. Each question runs through (`core/tools.py` + `core/copilot.py` + `core/grounding.py`):

1. **Decide** — Gemini is given the tool schemas and chooses which tool(s) to call (e.g. `compute_flux_bridge` for a "why did EBITDA change" question), with enum-constrained arguments.
2. **Execute** — each tool deterministically reads the dataset and returns the exact figures **with citations**. Tools are read-only; the model only orchestrates and explains.
3. **Verify** — every number in the agent's final answer is checked against the facts the tools returned (and their arithmetic derivations). Any ungrounded number **blocks** the answer and falls back to the verified tool facts.

This makes numeric grounding measurable. Deterministic lookup is a better fit than vector-RAG for the compact structured corpus, while the verifier remains the backstop. See `SPEC.md` §8.

---

## Project structure

```
grab-finance-copilot-pkg/        # the runnable app (README.md lives at the repo root)
├── SPEC.md                  # implemented architecture and acceptance contract
├── TECHDOC.md               # code-level reference: what each module & function does
├── requirements.txt
├── app.py                   # entry: sidebar + 3 tabs
├── data/grab_financials.json   # Grab source of truth (validated)
├── data/peers/                 # Uber, Lyft, DoorDash, Sea official-IR facts
├── data/peer_metric_catalog.json # canonical metrics + comparison rules
├── core/                    # data + agent engine (built & tested)
│   ├── data_loader.py       #   frames, margins, flux bridges, integrity checks
│   ├── peer_data_loader.py  #   peer validation + normalized company adapter
│   ├── tools.py             #   9 typed financial tools + JSON-Schema tool defs
│   ├── grounding.py         #   deterministic tool-router + numeric verifier
│   └── copilot.py           #   agentic tool-calling loop + grounding gate
├── views/                   # Streamlit UI per layer
├── eval/                    # golden-question eval harness
└── tests/                   # integrity + verifier tests
```

**Build status:** All three case-study layers are implemented. Grab passes 9/9 integrity checks and all four peer datasets pass source, coverage, and derivation validation. The peer layer is a validated common-metric benchmark, not yet Grab-equivalent segment depth. Run `pytest` and `python eval/run_eval.py` to verify.

---

## Verify the data foundation

```bash
python -m core.data_loader     # prints frames + 9/9 integrity checks PASS, flux bridge ties out
python -c "from core.peer_data_loader import validation_report; print(validation_report())"
python -m core.grounding       # retrieval + verifier smoke tests
python -m core.copilot         # copilot (offline / retrieval-only) smoke test
```

Integrity invariants (enforced by `core.data_loader.consistency_report()`): for every year, segment revenue sums to group revenue; segment Adjusted EBITDA sums to Total Segment Adjusted EBITDA; and Total Segment Adjusted EBITDA minus regional corporate costs equals Group Adjusted EBITDA.

---

## Testing & evaluation

```bash
pytest                  # unit tests: integrity invariants + verifier behaviour
python eval/run_eval.py # golden-question eval: retrieval recall + grounding precision
```

The eval command exits non-zero if retrieval recall drops below 100% on the golden set, or if the verifier passes an injected number. It is ready to use as a CI gate. See `SPEC.md` §9.4.

---

## Deploy (AWS)

The production design targets **AWS**. The interview project itself is a local Streamlit demo, not a deployed production control plane.

1. **Containerise** — build the Streamlit app into a Docker image; push to **Amazon ECR**.
2. **Run** — deploy on **ECS Fargate** or **App Runner** (or EKS), behind an Application Load Balancer with corporate SSO at the edge.
3. **Secrets** — store the model-provider credentials + config in **AWS Secrets Manager** (the demo's `VERTEX_PROJECT_ID` + GCP service-account JSON; in production, the Grab-approved provider's credentials); inject at runtime via the ECS task role (no keys in the image, no committed `.env`).
4. **LLM provider** — the demo uses Vertex AI's authenticated global endpoint (only typed numeric facts are sent). **Production uses whatever model service Grab approves for compliance** (e.g. Amazon Bedrock or an internal LLM); it sits behind a thin client layer in `core/copilot.py`, so swapping the provider never touches the rest of the app.

---

## Data & disclaimer

Figures are derived from Grab filings and official investor-relations releases for Uber, Lyft, DoorDash, and Sea. Grab's FY2023 Deliveries Segment Adjusted EBITDA uses the later recast $81M, and FY2024 Adjusted Free Cash Flow uses the later comparative $162M definition. Company-defined non-GAAP metrics and platform-volume definitions are not assumed to be directly comparable. This is a decision-support tool; verify against the linked source before relying on a figure. Not investment advice.

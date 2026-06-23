# GrabFi — Technical Reference (code-level)

A function-by-function map of the codebase: what every module is for and what each
function does. This complements the other docs:

- [`../README.md`](../README.md) — what the app is + how to run it.
- [`SPEC.md`](SPEC.md) — architecture, data contract, and acceptance criteria.
- **This file** — the code-level "what does each function do" reference.

All paths are relative to this package (`grab-finance-copilot-pkg/`).

---

## 1. 30-second mental model

GrabFi is a Streamlit app with three tabs (Layers 1–3) over a single validated dataset.
The defining constraint is **no hallucinated numbers**: every figure a human sees traces
to the source dataset, and the Copilot's generated numbers are *verified* before display.

**A Copilot question flows like this:**

```
question
  │
  ▼
grounding.refusal_reason()      → out-of-scope? (forecast / unknown year / other company) → refuse, no facts
  │ (in scope)
  ▼
grounding.retrieve()            → deterministically gather every fact that could be relevant
  │
  ├─ offline / retrieval-only mode → return the verified facts directly (no LLM phrasing)
  │
  └─ agent mode (Vertex Gemini):
        copilot.ask() loop  ──→ model picks a tool ──→ tools.execute_tool() reads the dataset,
        (≤5 steps)                                       returns (text, [Fact]) with citations
        │
        ▼
     grounding.verify_answer() → every number in the answer must be explainable from the
                                  retrieved Facts (or their arithmetic). Ungrounded number → BLOCK,
                                  fall back to the verified facts.
```

The dashboard (Layer 1) and governance tab (Layer 3) read the same dataset through the same
loaders, so the numbers can never diverge between tabs.

---

## 2. Module dependency map

Layers point downward; there are no cycles.

```
app.py
 ├── views/dashboard.py ──────┐
 ├── views/copilot_view.py ─┐ │
 └── views/governance.py ─┐ │ │
                          │ │ │
        core/copilot.py ◄─┘─┘ │   (ask() + Gemini tool-calling loop)
          ├── core/tools.py ◄─┘   (9 typed tools → Facts)
          │     ├── core/grounding.py   (refusal, retrieve, verify_answer, Fact)
          │     ├── core/data_loader.py (Grab dataset → frames, margins, flux, integrity)
          │     └── core/peer_data_loader.py (peer datasets → validation, normalized facts)
          └── core/grounding.py
eval/run_eval.py → copilot + grounding + loaders   (CI regression gate)
```

`data_loader` is the base layer (no internal deps). `peer_data_loader` builds on it.
`grounding` and `tools` sit above both. `copilot` orchestrates `tools` + `grounding`.
Views and `eval` consume the engine.

---

## 3. Core engine

### `core/data_loader.py` — Grab dataset → frames, margins, flux, integrity
Single source of truth loader for `data/grab_financials.json`. Cache is keyed by file mtime
so editing the JSON hot-reloads. **`YEARS` is derived from the data** (not hardcoded), which
is what makes "add a fiscal year" a data-only change.

| Function | What it does |
|---|---|
| `_raw_cached(_mtime)` | `lru_cache`d JSON parse, keyed by mtime so edits invalidate the cache. |
| `_raw()` | Returns the parsed dataset dict; looks up the file mtime and calls `_raw_cached`. |
| `_discover_years()` | Derives the sorted list of fiscal years from the `group.revenue` keys → populates `YEARS`. |
| `meta()` | Dataset-level metadata (currency, accounting basis, etc.). |
| `_series(node)` | Pulls just the `{FYxxxx: value}` pairs out of a metric node (ignores `stmt`/`source`/etc.). |
| `controlling_source(node, year)` | Resolves which filing release controls a given fact (latest year → newest release, prior years → the release that restated them; honors a per-year `source_by_year` override). |
| `source_citation(year, node)` | Human-readable citation string for a fact; degrades gracefully if a source is missing. |
| `group_frame()` | DataFrame: rows = group P&L/cash metrics, columns = `YEARS`. |
| `operating_metrics_frame()` | DataFrame of operating metrics (GMV, MTUs, incentives, loan book…). |
| `segment_frame(field)` | DataFrame rows = segment, cols = `YEARS` for one `field` ∈ {`revenue`,`gmv`,`segment_adj_ebitda`}. |
| `segment_margins()` | Segment Adjusted-EBITDA margin per segment's basis (% of GMV or % of revenue). |
| `yoy(df)` | Year-over-year % change for each row across `YEARS`. |
| `group_adj_ebitda_margin()` | Group Adjusted EBITDA ÷ revenue, per year. |
| `class FluxStep` | Dataclass `(label, value)` — one driver step in a flux bridge. |
| `flux_bridge(metric, start, end)` | Decomposes the change in a **group** metric between two years into segment + corporate driver steps; the steps sum to the total change. |
| `consistency_report()` | The 9 integrity tie-outs → list of `(check_name, passed, detail)`. Drives the sidebar audit badge and the falsifiability tests. |

**Integrity invariants** (per year, enforced by `consistency_report()`): Σ segment revenue = group
revenue; Σ segment Adj EBITDA = Total Segment Adj EBITDA; Total Segment Adj EBITDA − Regional
corporate costs = Group Adjusted EBITDA. Tolerance ±$1M for rounding.

### `core/peer_data_loader.py` — peer datasets → validation + normalized facts
Loads and validates `data/peers/*.json` (Uber, Lyft, DoorDash, Sea) **without** touching Grab's
schema. Enforces provenance and comparability so peers can't be silently mis-compared.

| Function | What it does |
|---|---|
| `canonical_company(name)` | Normalizes an alias/casing (e.g. "uber") to the canonical company key. |
| `load_peer(company)` | Loads + caches one peer's JSON dataset. |
| `metric_catalog()` | Loads `data/peer_metric_catalog.json` — canonical metric names + comparison rules. |
| `validate_peer(company, data)` | Returns a list of error strings: schema version, company id, full year coverage, **official-IR host allowlist**, numeric types, valid source references, status/precision enums, and **quarterly→annual tie-outs**. Empty list = valid. |
| `validation_report()` | Runs `validate_peer` over every peer → list of `(company, ok, detail)` (the 4 peer checks in the audit badge). |
| `_grab_source(year, node)` / `_peer_source(company, data, year)` | Build citation strings for Grab / peer facts. |
| `get_fact(company, section, metric, year)` | Returns one **normalized** fact (value, unit, citation) while retaining its reported label, status (reported vs summed-from-quarters), precision, basis, and caveat. |
| `comparability(metric, section)` | Classifies a metric as `reported_comparable` vs `directional_only` per the catalog. |
| `require_comparable(metric, mode)` | Guard that refuses to strict-rank a metric that isn't reported-comparable across companies. |

### `core/grounding.py` — refusal gate, deterministic retrieval, numeric verifier
The anti-hallucination core. Used in **both** modes: it is the offline router *and* the
post-generation backstop.

| Function / class | What it does |
|---|---|
| `_names_unloaded_company(question)` | True if a possessive phrase names a company we don't cover; uses a safe-possessive allow-set so Grab segments/peers (e.g. "Mobility's", "Shopee's") are **not** wrongly refused. |
| `refusal_reason(question)` | Deterministic out-of-scope reason — forecast/guidance, unsupported year, or out-of-scope entity — or `None` if in scope. |
| `refusal_answer(reason)` | Builds the standard refusal message **without exposing any financial facts**. |
| `class Fact` | Immutable cited fact `(id, label, value, unit, citation, note)`; `as_line()` renders a single cited line. The atom every tool returns and the verifier checks against. |
| `class Retrieval` | A bundle of `Fact`s for a question; `values()` and `facts_block()` expose them. |
| `_years_in(text)` | Parses FY tokens from text. |
| `_src(year, node)` | Citation helper (re-exported and reused by `tools.py`). |
| `_grab_basis(node)` | Reports IFRS vs non-IFRS basis for a metric. |
| `retrieve(question)` | **Deterministically** gathers every fact that could be relevant — no LLM. This is retrieval-only mode's answer and the verifier's ground truth. |
| `_to_float(token)` | Parses a numeric token to float. |
| `extract_numbers(text)` | Pulls candidate numbers out of answer text; strips URLs, ISO dates, 6-K/Exhibit refs; normalizes the unicode minus. |
| `class VerificationResult` | Outcome of `verify_answer`: pass/fail + the offending ungrounded numbers. |
| `verify_answer(answer, retrieval, rel_tol, abs_tol)` | Every number in `answer` must be explainable from the retrieved facts **or** their derivations (pairwise diff/sum, YoY %, margin, abs of negatives, note numbers). Tolerance ±1% / ±0.6. Any unexplained number fails. |

### `core/tools.py` — the 9 typed financial tools
The tool layer for the agent. Each tool is a deterministic, read-only function that reads the
dataset and returns `[Fact]` with citations. The model only chooses tools and explains; it
never sees raw recalled numbers. `TOOLS` holds the JSON-Schema definitions exposed to Gemini.

| Function | What it does |
|---|---|
| `_years(years)` | Validates/normalizes a requested year list against `YEARS` (raises on unsupported). |
| `_pretty(s)` | `snake_case` → "Title Case" label. |
| `get_group_financials(metrics, years)` | Grab group P&L/cash metrics → `[Fact]`. |
| `get_segment_financials(segment, fields, years)` | One segment's `revenue`/`gmv`/`segment_adj_ebitda` → `[Fact]`. |
| `get_segment_margins(segments, years)` | Derived segment Adj-EBITDA margins → `[Fact]`. |
| `get_operating_metrics(metrics, years)` | Operating metrics (GMV, MTUs, incentives, loan book) → `[Fact]`. |
| `compute_flux_bridge(metric, start_year, end_year)` | Flux drivers for a group metric → `[Fact]` (wraps `data_loader.flux_bridge`). |
| `_normalized_fact(raw, comparison_note)` | Converts a peer raw dict into a `Fact`, appending qualifiers (summed-from-quarters, precision, caveat). |
| `_add_coverage_warning(facts, missing)` | Annotates facts with a coverage warning when requested data isn't loaded. |
| `get_company_financials(company, metrics, years)` | Peer group financials → `[Fact]`. |
| `get_company_operating_metrics(company, metrics, years)` | Peer operating metrics → `[Fact]`. |
| `get_company_segment_metrics(company, metrics, years)` | Peer segment metrics → `[Fact]`. |
| `compare_companies(companies, metric, years, comparison_mode)` | Cross-company comparison, **gated by `require_comparable`** — refuses to strict-rank non-comparable metrics, only juxtaposes them. |
| `execute_tool(name, args)` | Dispatches a tool by name → `(text_for_model, facts_for_verifier)`. The single entry point `copilot.ask()` calls. |

### `core/copilot.py` — the agentic loop + grounding gate
Wires Gemini (Vertex AI) tool-calling to the typed tools, then runs the grounding gate.
`DEFAULT_MODEL = gemini-3.5-flash`; default location `global`. Falls back to retrieval-only
if Vertex isn't configured or errors (logs without leaking exception text).

| Function / class | What it does |
|---|---|
| `class ToolCall` | Record of one tool invocation (name, args, resulting facts) for the transparency panel. |
| `class CopilotResponse` | The full result: answer text, mode, tool-call trace, citations, facts, and grounding pass/fail. |
| `_citations(facts)` | De-duplicated, ordered citation list for display. |
| `_dedupe(facts)` | Removes duplicate facts (same id) returned across tool calls. |
| `_retrieval_only_answer(r)` | Builds a `CopilotResponse` from `retrieve()` alone (offline / no-LLM mode). |
| `_json_schema_to_gemini(schema)` | Converts a JSON-Schema dict to `google.genai` `types.Schema`. |
| `_build_gemini_tools()` | Converts the `TOOLS` defs in `core/tools.py` into Gemini `FunctionDeclaration`s. |
| `ask(question, model, temperature)` | The orchestrator: refusal gate → (offline retrieve OR Gemini tool-calling loop, ≤5 steps) → synthesis-on-exhaustion → no-tool-evidence guard → `verify_answer` → one correction pass → fail-closed block. Returns `CopilotResponse`. |

---

## 4. UI (views)

### `views/dashboard.py` — Layer 1: Finance & Flux Analysis
Renders KPIs, the profitability inflection banner, segment charts, margin/cost-leverage
charts, the flux waterfall, and the peer benchmark — all from the dataset.

| Function | What it does |
|---|---|
| `_fmt(v, unit)` | Formats a metric value for display (currency/%, sign, scale). |
| `_fmt_prose(v)` | Currency text safe for Streamlit Markdown — swaps `$`→`USD ` so it isn't rendered as LaTeX math. |
| `_yoy_delta(val, prev, unit)` | KPI-card YoY delta; uses % for clean positive series, absolute when the sign flips. |
| `_group_series(metric)` | `company → {year: value}` for a peer-group benchmark metric (via the peer loader). |
| `_exec_summary(gf, y, p)` | Three grounded one-liner takeaways derived entirely from the dataset (no literals). |
| `render(selected_year)` | Draws the whole Layer-1 tab; `selected_year` comes from the sidebar filter. Includes the per-KPI "explain this number" hooks that call `copilot.ask()`. |

### `views/copilot_view.py` — Layer 2: GrabFi Copilot chat UI
| Function | What it does |
|---|---|
| `_escape_currency_markdown(text)` | Escapes `$` so Streamlit doesn't interpret currency as LaTeX. Reused by the dashboard explain panel. |
| `_mode_badge(mode)` | Renders the "agent mode" vs "retrieval-only" badge. |
| `_render_transparency(resp)` | Shows the tool-call trace, citations, returned facts, and the grounding pass/fail check. |
| `render(model)` | The chat tab: input, history, streamed answer, and the transparency panel; `model` is the sidebar-selected model. |

### `views/governance.py` — Layer 3: Governance & Scale
| Function | What it does |
|---|---|
| `_live_summary()` | Live counts/KPIs (checks passing, facts, coverage) for the governance header. |
| `_source_coverage_figure()` | Evidence-coverage heatmap (which metric-years have a source). |
| `render()` | Draws Layer 3: guardrails, the three provider-agnostic Graphviz diagrams (production architecture, data pipeline, request flow), and the detailed spec tabs. |

### `app.py` — entry point
| Function | What it does |
|---|---|
| `_bootstrap_cloud_secrets()` | On Streamlit Community Cloud (no `.env`/SA file), writes the service-account JSON from `st.secrets` to a temp file and sets the Vertex env vars so auth works. No-op locally. |
| *(module body)* | Page config, sidebar (provenance, 13-check audit badge, year filter, model selector, mode indicator), and the three tabs. |

---

## 5. Tests & evaluation

### `eval/run_eval.py` — regression gate (CI)
Run with `python eval/run_eval.py`; exits non-zero on failure.

| Function | What it does |
|---|---|
| `load_questions()` | Loads the golden-question set. |
| `check_retrieval_recall(q, verbose)` | Asserts `retrieve()` finds the expected facts **and** the question isn't wrongly refused. |
| `check_adversarial_block(q, verbose)` | Injects an ungrounded number and asserts `verify_answer` **blocks** it. |
| `check_refusal(q, verbose)` | Asserts an out-of-scope question is refused. |
| `main()` | Runs all checks and exits non-zero if recall drops or the verifier passes an injected number. |

### `tests/` (pytest, run with `pytest -q`)
Unit tests for the integrity invariants, the verifier behavior (grounded numbers pass,
injected ones fail), peer validation, scope/refusal logic, and cross-file consistency
(e.g. documented tool count matches the registry, no retired-stack references, single
README/SPEC). These are what let you change data or prompts with confidence.

---

## 6. Data files (the source of truth)

| File | Contains |
|---|---|
| `data/grab_financials.json` | Grab group + segment + operating metrics, per year, with per-fact source metadata. Validated by `consistency_report()`. |
| `data/peers/{uber,lyft,doordash,sea}_financials.json` | One official-IR-backed dataset per peer (common-metric layer). Validated by `validate_peer()`. |
| `data/peer_metric_catalog.json` | Canonical metric names + comparison rules (reported-comparable vs directional-only). |
| `data/peer_source_inventory.json` | Records available disclosure per peer/year, distinct from what's currently extracted. |

---

## 7. "Where do I change X?" quick lookup

| I want to… | Go to |
|---|---|
| Add a fiscal year | `data/grab_financials.json` (+ peers). `YEARS` auto-derives; everything generalizes. |
| Add/fix a Grab number | `data/grab_financials.json`; `consistency_report()` tie-outs will catch a break. |
| Add a new tool the Copilot can call | `core/tools.py` (impl + `TOOLS` schema + `execute_tool` dispatch). |
| Change what's in/out of scope | `core/grounding.py` → `refusal_reason()` / `_names_unloaded_company()`. |
| Tighten/loosen the numeric verifier | `core/grounding.py` → `verify_answer()` (tolerances + allowed derivations). |
| Change the model / location | `.env` (`COPILOT_MODEL`, `VERTEX_LOCATION`) or the sidebar; default in `core/copilot.py`. |
| Edit a dashboard chart or KPI | `views/dashboard.py` → `render()`. |
| Edit governance diagrams/spec | `views/governance.py` (`PROD_ARCH_DOT` / `DATA_PIPELINE_DOT` / `FLOW_DOT`). |
| Add a regression check | `eval/run_eval.py` and/or `tests/`. |

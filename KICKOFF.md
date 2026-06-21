# Project Kickoff

The runnable and authoritative project is [`grab-finance-copilot-pkg`](grab-finance-copilot-pkg/).

## Start

```bash
cd grab-finance-copilot-pkg
pip install -r requirements.txt
pytest -q
python eval/run_eval.py
streamlit run app.py
```

Read [`grab-finance-copilot-pkg/README.md`](grab-finance-copilot-pkg/README.md) for operation and [`grab-finance-copilot-pkg/SPEC.md`](grab-finance-copilot-pkg/SPEC.md) for the implemented architecture.

## Current Contract

- Grab dashboard: FY2023–FY2025 group, segment, operating, and flux analysis.
- Copilot scope: Grab, Uber, Lyft, DoorDash, and Sea reported FY2023–FY2025 facts.
- Runtime: Gemini through Vertex AI, with deterministic retrieval-only fallback.
- Tool layer: nine typed, read-only financial tools.
- Evidence: official filing or investor-relations sources, fact citations, comparability rules, and numeric verification.
- Validation: nine Grab financial tie-outs plus four peer dataset validations.

Root-level strategy documents are supporting artifacts. They are not runtime code or the source of implementation truth.

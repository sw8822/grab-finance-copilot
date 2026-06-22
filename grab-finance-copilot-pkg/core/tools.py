"""
core/tools.py
-------------
The TOOL LAYER for the agentic GrabFi Copilot (Layer 2).

The copilot is a tool-calling agent: the model decides *which* typed financial
tool to call; each tool is a deterministic function that reads the
source-of-truth dataset and returns facts WITH citations. The model never sees
raw recalled numbers — only tool outputs. A post-generation verifier
(core/grounding.verify_answer) then gates the final answer.

Two consumers:
  * agentic mode  -> Gemini (Vertex AI) tool-use loop in core/copilot.py picks tools.
  * offline mode  -> core/grounding.retrieve() is the deterministic router that
                     unions the same underlying facts without an LLM.

Either way the numbers come from the same place, so they always agree.
"""
from __future__ import annotations

from core import data_loader as dl
from core import peer_data_loader as pdl
from core.grounding import Fact, _src  # reuse Fact + source-citation helper

YEARS = dl.YEARS

GROUP_METRICS = [
    "revenue", "operating_profit", "profit_for_year", "adjusted_ebitda",
    "total_segment_adj_ebitda", "regional_corporate_costs", "share_based_comp",
    "depreciation_amortization", "operating_cash_flow", "adjusted_free_cash_flow",
    "capital_expenditures", "net_finance_income", "cost_of_revenue",
    # operating-expense / P&L lines (enable cost-structure & operating-leverage questions)
    "sales_marketing_expense", "general_admin_expense", "research_dev_expense",
    "net_impairment_fin_assets", "restructuring_costs", "income_tax_expense",
]
SEGMENTS = ["Deliveries", "Mobility", "Financial Services", "Others"]
SEGMENT_FIELDS = ["revenue", "gmv", "segment_adj_ebitda"]
OPERATING_METRICS = [
    "on_demand_gmv", "group_mtus", "on_demand_gmv_per_mtu",
    "partner_incentives", "consumer_incentives", "loan_portfolio",
]
COMPANIES = pdl.COMPANIES
PEER_GROUP_METRICS = list(pdl.metric_catalog()["group_metrics"])
PEER_OPERATING_METRICS = list(pdl.metric_catalog()["operating_metrics"])
PEER_SEGMENT_METRICS = sorted({
    metric
    for company in pdl.PEER_FILES
    for metric in pdl.load_peer(company).get("segments", {})
})


def _years(years: list[str] | None) -> list[str]:
    if not years:
        return YEARS
    invalid = sorted(set(years) - set(YEARS))
    if invalid:
        raise ValueError(f"Unsupported years: {invalid}; choose from {YEARS}")
    return [y for y in YEARS if y in years]


def _pretty(s: str) -> str:
    return s.replace("_", " ").title()


# --------------------------------------------------------------------- tool impls
def get_group_financials(metrics: list[str], years: list[str] | None = None) -> list[Fact]:
    invalid = sorted(set(metrics) - set(GROUP_METRICS))
    if invalid:
        raise ValueError(f"Unsupported group metrics: {invalid}")
    g = dl._raw()["group"]
    out: list[Fact] = []
    for m in metrics:
        if m not in g:
            continue
        for y in _years(years):
            basis = "non-IFRS" if "Non-IFRS" in g[m].get("stmt", "") else "IFRS"
            out.append(Fact(f"group.{m}.{y}", f"Group {_pretty(m)} ({y})",
                            g[m][y], "USD M", _src(y, g[m]), f"basis: {basis}"))
    return out


def get_segment_financials(segment: str, fields: list[str],
                           years: list[str] | None = None) -> list[Fact]:
    segs = dl._raw()["segments"]
    if segment not in segs:
        raise ValueError(f"Unsupported segment {segment!r}")
    invalid = sorted(set(fields) - set(SEGMENT_FIELDS))
    if invalid:
        raise ValueError(f"Unsupported segment fields: {invalid}")
    node = segs[segment]
    out: list[Fact] = []
    for f in fields:
        if f not in node:
            continue
        label_field = "Segment Adj EBITDA" if f == "segment_adj_ebitda" else f.upper() if f == "gmv" else _pretty(f)
        for y in _years(years):
            out.append(Fact(f"seg.{segment}.{f}.{y}", f"{segment} {label_field} ({y})",
                            node[f][y], "USD M", _src(y, node[f]),
                            "basis: non-IFRS" if f == "segment_adj_ebitda" else "basis: reported operating metric"))
    return out


def get_segment_margins(segments: list[str], years: list[str] | None = None) -> list[Fact]:
    invalid = sorted(set(segments) - set(SEGMENTS))
    if invalid:
        raise ValueError(f"Unsupported segments: {invalid}")
    m = dl.segment_margins()
    segs = dl._raw()["segments"]
    out: list[Fact] = []
    for seg in segments:
        if seg not in segs:
            continue
        basis = segs[seg]["margin_basis"]
        for y in _years(years):
            out.append(Fact(f"seg.{seg}.margin.{y}",
                            f"{seg} Segment Adj EBITDA margin ({y}, % of {basis})",
                            float(m.loc[seg, y]), "%", _src(y, segs[seg]["segment_adj_ebitda"]),
                            "basis: derived non-IFRS margin"))
    return out


def get_operating_metrics(metrics: list[str], years: list[str] | None = None) -> list[Fact]:
    invalid = sorted(set(metrics) - set(OPERATING_METRICS))
    if invalid:
        raise ValueError(f"Unsupported operating metrics: {invalid}")
    om = dl._raw()["operating_metrics"]
    out: list[Fact] = []
    for k in metrics:
        if k not in om:
            continue
        unit = om[k].get("unit", "")
        for y in _years(years):
            out.append(Fact(f"opm.{k}.{y}", f"{_pretty(k)} ({y})", om[k][y], unit,
                            _src(y, om[k]), "basis: reported operating metric"))
    return out


def compute_flux_bridge(metric: str, start_year: str, end_year: str) -> list[Fact]:
    """Decompose the change in a group metric into segment + corporate drivers."""
    if start_year not in YEARS or end_year not in YEARS:
        raise ValueError(f"Bridge years must be in {YEARS}")
    if YEARS.index(start_year) >= YEARS.index(end_year):
        raise ValueError("start_year must precede end_year")
    steps = dl.flux_bridge(metric, start_year, end_year)
    out: list[Fact] = []
    for s in steps:
        group_node = dl._raw()["group"][metric]
        start_source = _src(start_year, group_node)
        end_source = _src(end_year, group_node)
        citation = f"derived from {start_source}; {end_source}"
        basis = "derived from non-IFRS Adjusted EBITDA" if metric == "adjusted_ebitda" else "derived from IFRS revenue"
        out.append(Fact(f"flux.{metric}.{s.label}", s.label, s.value, "USD M", citation, f"basis: {basis}"))
    return out


def _normalized_fact(raw: dict, comparison_note: str | None = None) -> Fact:
    qualifier = ""
    if raw["status"] != "reported":
        qualifier += ", summed from official quarterly IR"
    if raw["precision"] != "exact":
        qualifier += f", {raw['precision']}"
    label = f"{raw['company']} {raw['reported_name']} ({raw['year']}{qualifier})"
    basis_note = f"basis: {raw['basis']}"
    note = "; ".join(filter(None, [basis_note, raw.get("caveat"), comparison_note])) or None
    return Fact(
        f"peer.{raw['company']}.{raw['metric']}.{raw['year']}",
        label, raw["value"], raw["unit"], raw["citation"], note,
    )


def _add_coverage_warning(facts: list[Fact], missing: list[str]) -> None:
    if not missing:
        return
    warning = f"coverage warning: no loaded facts for {missing}"
    for fact in facts:
        fact.note = "; ".join(filter(None, [fact.note, warning]))


def get_company_financials(company: str, metrics: list[str],
                           years: list[str] | None = None) -> list[Fact]:
    out = []
    missing = []
    selected_years = _years(years)
    for metric in metrics:
        for year in selected_years:
            raw = pdl.get_fact(company, "group", metric, year)
            if raw:
                out.append(_normalized_fact(raw))
            else:
                missing.append(f"{metric}.{year}")
    if not out:
        raise ValueError(f"{pdl.canonical_company(company)} has no loaded facts for metrics: {metrics}")
    _add_coverage_warning(out, missing)
    return out


def get_company_operating_metrics(company: str, metrics: list[str],
                                  years: list[str] | None = None) -> list[Fact]:
    out = []
    missing = []
    selected_years = _years(years)
    for metric in metrics:
        for year in selected_years:
            raw = pdl.get_fact(company, "operating_metrics", metric, year)
            if raw:
                out.append(_normalized_fact(raw))
            else:
                missing.append(f"{metric}.{year}")
    if not out:
        raise ValueError(f"{pdl.canonical_company(company)} has no loaded operating facts for metrics: {metrics}")
    _add_coverage_warning(out, missing)
    return out


def get_company_segment_metrics(company: str, metrics: list[str],
                                years: list[str] | None = None) -> list[Fact]:
    if pdl.canonical_company(company) == "Grab":
        raise ValueError("Use get_segment_financials for Grab segments")
    out = []
    missing = []
    selected_years = _years(years)
    for metric in metrics:
        for year in selected_years:
            raw = pdl.get_fact(company, "segments", metric, year)
            if raw:
                out.append(_normalized_fact(raw))
            else:
                missing.append(f"{metric}.{year}")
    if not out:
        raise ValueError(f"{pdl.canonical_company(company)} has no loaded segment facts for metrics: {metrics}")
    _add_coverage_warning(out, missing)
    return out


def compare_companies(companies: list[str], metric: str,
                      years: list[str] | None = None,
                      comparison_mode: str = "strict") -> list[Fact]:
    rule = pdl.require_comparable(metric, comparison_mode)
    canonical = list(dict.fromkeys(pdl.canonical_company(c) for c in companies))
    if len(canonical) < 2:
        raise ValueError("compare_companies requires at least two distinct companies")
    out = []
    missing = []
    for company in canonical:
        for year in _years(years):
            raw = pdl.get_fact(company, "group", metric, year)
            if raw:
                out.append(_normalized_fact(raw, f"{rule['comparability']}: {rule['note']}"))
            else:
                missing.append(f"{company}.{metric}.{year}")
    _add_coverage_warning(out, missing)
    return out


TOOL_IMPLS = {
    "get_group_financials": get_group_financials,
    "get_segment_financials": get_segment_financials,
    "get_segment_margins": get_segment_margins,
    "get_operating_metrics": get_operating_metrics,
    "compute_flux_bridge": compute_flux_bridge,
    "get_company_financials": get_company_financials,
    "get_company_operating_metrics": get_company_operating_metrics,
    "get_company_segment_metrics": get_company_segment_metrics,
    "compare_companies": compare_companies,
}

# ----------------------------------------------------------- JSON-Schema tool defs (converted to Gemini FunctionDeclarations in core/copilot.py)
_YEAR_ENUM = {"type": "array", "items": {"type": "string", "enum": YEARS},
              "description": "Fiscal years; omit for all years FY2023-FY2025."}

TOOLS = [
    {
        "name": "get_group_financials",
        "description": "Group-level financials (P&L lines, Adjusted EBITDA, cash flow). Use for revenue, profit, EBITDA, corporate costs, cash flow questions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metrics": {"type": "array", "items": {"type": "string", "enum": GROUP_METRICS},
                            "description": "Which group metrics to fetch."},
                "years": _YEAR_ENUM,
            },
            "required": ["metrics"],
        },
    },
    {
        "name": "get_segment_financials",
        "description": "Revenue, GMV, and Segment Adjusted EBITDA for ONE segment. Call once per segment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "segment": {"type": "string", "enum": SEGMENTS},
                "fields": {"type": "array", "items": {"type": "string", "enum": SEGMENT_FIELDS}},
                "years": _YEAR_ENUM,
            },
            "required": ["segment", "fields"],
        },
    },
    {
        "name": "get_segment_margins",
        "description": "Segment Adjusted EBITDA margin (Deliveries/Mobility vs GMV; Financial Services/Others vs revenue).",
        "input_schema": {
            "type": "object",
            "properties": {
                "segments": {"type": "array", "items": {"type": "string", "enum": SEGMENTS}},
                "years": _YEAR_ENUM,
            },
            "required": ["segments"],
        },
    },
    {
        "name": "get_operating_metrics",
        "description": "Operating metrics: On-Demand GMV, MTUs, GMV/MTU, incentives, loan portfolio.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metrics": {"type": "array", "items": {"type": "string", "enum": OPERATING_METRICS}},
                "years": _YEAR_ENUM,
            },
            "required": ["metrics"],
        },
    },
    {
        "name": "compute_flux_bridge",
        "description": "Decompose the YoY change in group 'revenue' or 'adjusted_ebitda' into segment + regional-corporate-cost drivers. Use for 'why did X change' / 'bridge' questions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": ["revenue", "adjusted_ebitda"]},
                "start_year": {"type": "string", "enum": YEARS},
                "end_year": {"type": "string", "enum": YEARS},
            },
            "required": ["metric", "start_year", "end_year"],
        },
    },
    {
        "name": "get_company_financials",
        "description": "Reported group financials for Grab, Uber, Lyft, DoorDash, or Sea from official IR sources. Use for a single company's cross-year trend.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "enum": COMPANIES},
                "metrics": {"type": "array", "items": {"type": "string", "enum": PEER_GROUP_METRICS}},
                "years": _YEAR_ENUM,
            },
            "required": ["company", "metrics"],
        },
    },
    {
        "name": "get_company_operating_metrics",
        "description": "Company-specific scale and operating facts from official IR. Report the company's original metric name; do not treat different platform-volume or activity definitions as directly comparable.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "enum": COMPANIES},
                "metrics": {"type": "array", "items": {"type": "string", "enum": PEER_OPERATING_METRICS}},
                "years": _YEAR_ENUM,
            },
            "required": ["company", "metrics"],
        },
    },
    {
        "name": "get_company_segment_metrics",
        "description": "Loaded peer segment facts from official IR sources. Currently exposes Sea's Shopee, financial-services/Monee, and Garena Adjusted EBITDA; use Grab's dedicated segment tool for Grab.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "enum": list(pdl.PEER_FILES)},
                "metrics": {"type": "array", "items": {"type": "string", "enum": PEER_SEGMENT_METRICS}},
                "years": _YEAR_ENUM,
            },
            "required": ["company", "metrics"],
        },
    },
    {
        "name": "compare_companies",
        "description": "Compare one canonical financial metric across two or more loaded companies. Strict mode permits only comparable GAAP lines; directional mode permits company-defined non-GAAP or one-off-affected metrics with an explicit caveat.",
        "input_schema": {
            "type": "object",
            "properties": {
                "companies": {"type": "array", "items": {"type": "string", "enum": COMPANIES}},
                "metric": {"type": "string", "enum": PEER_GROUP_METRICS},
                "years": _YEAR_ENUM,
                "comparison_mode": {"type": "string", "enum": ["strict", "directional"]},
            },
            "required": ["companies", "metric", "comparison_mode"],
        },
    },
]


def execute_tool(name: str, args: dict) -> tuple[str, list[Fact]]:
    """Run a tool. Returns (text_for_model, facts_for_verifier)."""
    impl = TOOL_IMPLS.get(name)
    if impl is None:
        return (f"ERROR: unknown tool {name!r}.", [])
    try:
        facts = impl(**args)
    except (TypeError, ValueError) as e:
        return (f"ERROR: bad arguments for {name}: {e}", [])
    if not facts:
        return ("No matching facts in the dataset for those arguments.", [])
    text = "\n".join(f.as_line() for f in facts)
    if name == "compare_companies":
        rule = pdl.comparability(args["metric"])
        text = f"COMPARABILITY ({rule['comparability']}): {rule['note']}\n{text}"
    return (text, facts)


if __name__ == "__main__":
    import json
    print("Tool schemas valid JSON:", bool(json.dumps(TOOLS)))
    print(f"{len(TOOLS)} tools registered:", [t["name"] for t in TOOLS], "\n")
    for name, args in [
        ("get_group_financials", {"metrics": ["revenue", "adjusted_ebitda"], "years": ["FY2025"]}),
        ("get_segment_margins", {"segments": ["Mobility", "Deliveries"], "years": ["FY2025"]}),
        ("compute_flux_bridge", {"metric": "adjusted_ebitda", "start_year": "FY2024", "end_year": "FY2025"}),
    ]:
        text, facts = execute_tool(name, args)
        print(f"### {name}({args})")
        print(text, "\n")

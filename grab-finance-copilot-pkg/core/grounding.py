"""
core/grounding.py
-----------------
The anti-hallucination core of the GrabFi Copilot (Layer 2).

Design: an LLM is never trusted to *recall* a number. Instead we run a
three-stage pipeline:

  1. RETRIEVE  - deterministically pull the exact facts relevant to the
                 question from the source-of-truth dataset. Each fact carries
                 its value AND its citation. This is plain Python, no model.
  2. CONSTRAIN - the LLM receives ONLY those facts and a system prompt that
                 forbids using any number not in the FACTS block.
  3. VERIFY    - after generation, every numeric token in the answer is
                 extracted and checked against the retrieved facts (within a
                 rounding tolerance). Any ungrounded number fails the gate and
                 the answer is blocked/flagged. This converts "please don't
                 hallucinate" (a hope) into a measurable guarantee (a test).

This module has no LLM dependency so it is unit-testable on its own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core import data_loader as dl
from core import peer_data_loader as pdl

YEARS = dl.YEARS

# Friendly aliases users actually type -> canonical group metric keys
_GROUP_ALIASES = {
    "revenue": "revenue", "sales": "revenue", "topline": "revenue", "top line": "revenue",
    "adjusted ebitda": "adjusted_ebitda", "adj ebitda": "adjusted_ebitda", "ebitda": "adjusted_ebitda",
    "operating profit": "operating_profit", "operating loss": "operating_profit", "operating income": "operating_profit",
    "net profit": "profit_for_year", "net income": "profit_for_year", "net loss": "profit_for_year",
    "profit": "profit_for_year", "bottom line": "profit_for_year",
    "free cash flow": "adjusted_free_cash_flow", "fcf": "adjusted_free_cash_flow", "afcf": "adjusted_free_cash_flow",
    "operating cash flow": "operating_cash_flow", "ocf": "operating_cash_flow",
    "regional corporate costs": "regional_corporate_costs", "corporate costs": "regional_corporate_costs",
    "stock comp": "share_based_comp", "sbc": "share_based_comp", "share based compensation": "share_based_comp",
    "capex": "capital_expenditures", "depreciation": "depreciation_amortization", "d&a": "depreciation_amortization",
    # operating-expense / P&L lines
    "sales and marketing": "sales_marketing_expense", "sales & marketing": "sales_marketing_expense",
    "s&m": "sales_marketing_expense", "marketing expense": "sales_marketing_expense",
    "marketing spend": "sales_marketing_expense", "marketing cost": "sales_marketing_expense",
    "general and administrative": "general_admin_expense", "general & administrative": "general_admin_expense",
    "g&a": "general_admin_expense", "admin expense": "general_admin_expense", "administrative expense": "general_admin_expense",
    "research and development": "research_dev_expense", "research & development": "research_dev_expense",
    "r&d": "research_dev_expense", "rnd": "research_dev_expense",
    "impairment": "net_impairment_fin_assets", "credit loss": "net_impairment_fin_assets",
    "credit losses": "net_impairment_fin_assets", "provision for credit": "net_impairment_fin_assets",
    "restructuring": "restructuring_costs",
    "income tax": "income_tax_expense", "tax expense": "income_tax_expense",
}
_SEGMENT_ALIASES = {
    "deliveries": "Deliveries", "delivery": "Deliveries", "food": "Deliveries", "grabfood": "Deliveries",
    "mobility": "Mobility", "rides": "Mobility", "ride hailing": "Mobility", "ride-hailing": "Mobility", "transport": "Mobility",
    "financial services": "Financial Services", "fintech": "Financial Services", "finserv": "Financial Services",
    "lending": "Financial Services", "loans": "Financial Services", "bank": "Financial Services", "gxbank": "Financial Services",
    "others": "Others", "other": "Others",
}
_OP_METRIC_ALIASES = {
    "gmv": "on_demand_gmv", "on-demand gmv": "on_demand_gmv", "on demand gmv": "on_demand_gmv",
    "mtu": "group_mtus", "mtus": "group_mtus", "users": "group_mtus", "transacting users": "group_mtus",
    "loan portfolio": "loan_portfolio", "loan book": "loan_portfolio",
    "incentives": "consumer_incentives",
}

_SUPPORTED_YEAR_NUMBERS = {int(y[2:]) for y in YEARS}
# Structural year numbers allowed in answers without grounding: data years (full
# and 2-digit) plus any guidance years referenced in the dataset. Derived from
# the data so a new period needs no code change.
_GUIDANCE_YEARS = {int(k[2:6]) for k in dl._raw().get("guidance", {}) if k.startswith("FY")}
_WHITELIST_YEARS = (
    {float(int(y[2:])) for y in YEARS}
    | {float(int(y[2:]) % 100) for y in YEARS}
    | {float(n) for n in _GUIDANCE_YEARS}
)
_OUT_OF_SCOPE_ENTITY_RE = re.compile(
    r"\b(?:gojek|go-jek|goto)\b",
    re.IGNORECASE,
)
_FORECAST_INTENT_RE = re.compile(
    r"\b(?:forecast|predict|prediction|projected|projection|estimate|outlook|future)\b"
    r"|\bwhat\s+will\b",
    re.IGNORECASE,
)

# A possessive proper noun ("Apple's revenue") usually names a company we don't
# cover. But Grab's own segments ("Mobility's"), loaded peers and their aliases
# ("Shopee's"), known business units, and ordinary words/contractions ("What's")
# are in-scope and must NOT trigger a refusal. We refuse only when the possessive
# phrase is none of those — i.e. an unknown company.
_POSSESSIVE_RE = re.compile(
    r"([A-Z][A-Za-z0-9.&-]*(?:\s+[A-Z][A-Za-z0-9.&-]*){0,2})(?:'s|’s)\b"
)
_SCOPE_SAFE_POSSESSIVES = (
    set(pdl.ALIASES)            # grab, uber, lyft, doordash, sea, shopee, ...
    | set(_SEGMENT_ALIASES)     # mobility, deliveries, financial services, ...
    | {
        # loaded / known business units that appear in the data or filings
        "monee", "garena", "grabfin", "gxbank", "grabfood", "grabmart", "ovo",
        # common English words / contractions that legitimately take a possessive
        "what", "that", "it", "there", "here", "how", "who", "where", "when",
        "why", "let", "today", "this", "year", "company", "group", "segment",
        "business", "management", "quarter",
    }
)


def _names_unloaded_company(question: str) -> bool:
    """True if the question's possessive phrase names a company we don't cover."""
    for match in _POSSESSIVE_RE.finditer(question):
        phrase = match.group(1).strip().lower()
        if phrase in _SCOPE_SAFE_POSSESSIVES:
            continue
        if phrase.split()[0] in _SCOPE_SAFE_POSSESSIVES:
            continue
        return True
    return False


def refusal_reason(question: str) -> str | None:
    """Return a deterministic reason when a question is outside the loaded scope."""
    if _FORECAST_INTENT_RE.search(question):
        return "a forecast or forward-looking estimate"
    years = {
        int(year)
        for year in re.findall(r"\b(?:FY)?(20\d{2})\b", question, re.IGNORECASE)
    }
    unsupported_years = sorted(years - _SUPPORTED_YEAR_NUMBERS)
    if unsupported_years:
        return "a period outside the loaded dataset"
    if _OUT_OF_SCOPE_ENTITY_RE.search(question):
        return "another company or peer comparison"
    if _names_unloaded_company(question):
        return "a company that is not loaded"
    return None


def refusal_answer(reason: str) -> str:
    """Build the standard refusal without exposing any financial facts."""
    return (
        "I can only answer questions about the loaded Grab, Uber, Lyft, DoorDash, "
        f"and Sea reported financial data for FY2023-FY2025. This question requests {reason}, which "
        "is outside the loaded dataset."
    )


@dataclass
class Fact:
    key: str            # machine id, e.g. "group.revenue.FY2025"
    label: str          # human label, e.g. "Group Revenue (FY2025)"
    value: float
    unit: str
    citation: str       # e.g. "FY2025 6-K, filed 2026-02-12"
    note: str | None = None

    def as_line(self) -> str:
        v = f"{self.value:,.3f}".rstrip("0").rstrip(".")
        note = f"  [note: {self.note}]" if self.note else ""
        return f"- {self.label}: {v} {self.unit}  [src: {self.citation}]{note}"


@dataclass
class Retrieval:
    facts: list[Fact] = field(default_factory=list)
    matched_years: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def values(self) -> list[float]:
        return [f.value for f in self.facts]

    def facts_block(self) -> str:
        if not self.facts:
            return "(no matching facts found in the dataset)"
        return "\n".join(f.as_line() for f in self.facts)


# --------------------------------------------------------------------------- retrieve
def _years_in(text: str) -> list[str]:
    found = []
    for y in YEARS:
        yr = y[2:]  # "2023"
        if yr in text or y.lower() in text.lower():
            found.append(y)
    if not found:
        return YEARS  # default: all years if none specified

    # If the user asks about a *change* but named a single year, include the
    # adjacent prior year so the YoY can be grounded (recall, not just precision).
    change_words = ("grow", "growth", "grew", "improve", "increase", "decrease",
                    "change", "rose", "fell", "decline", "trend", "yoy", "year over year",
                    "year-over-year", "turn", "became")
    if len(found) == 1 and any(w in text for w in change_words):
        idx = YEARS.index(found[0])
        if idx > 0:
            found = [YEARS[idx - 1], found[0]]
    return found


def _src(year: str, node: dict | None = None) -> str:
    return dl.source_citation(year, node)


def _grab_basis(node: dict) -> str:
    return "basis: non-IFRS" if "Non-IFRS" in node.get("stmt", "") else "basis: IFRS/reported"


def retrieve(question: str) -> Retrieval:
    """Deterministically gather every fact that could be relevant to the question."""
    q = " " + question.lower() + " "
    years = _years_in(q)
    facts: list[Fact] = []
    seen: set[str] = set()
    warnings: list[str] = []

    def add(key, label, value, unit, citation, note=None):
        if key not in seen:
            seen.add(key)
            facts.append(Fact(key, label, float(value), unit, citation, note))

    segs = dl._raw()["segments"]
    grp = dl._raw()["group"]
    opm = dl._raw()["operating_metrics"]

    # which segments were named?
    named_segs = [canon for alias, canon in _SEGMENT_ALIASES.items() if alias in q]
    named_segs = list(dict.fromkeys(named_segs))

    # which group metrics were named?
    named_group = [canon for alias, canon in _GROUP_ALIASES.items() if alias in q]
    named_group = list(dict.fromkeys(named_group))
    if any(term in q for term in ("operating profit", "operating loss", "operating income")):
        named_group = [metric for metric in named_group if metric != "profit_for_year"]

    # which operating metrics were named?
    named_opm = [canon for alias, canon in _OP_METRIC_ALIASES.items() if alias in q]
    named_opm = list(dict.fromkeys(named_opm))

    wants_margin = "margin" in q
    wants_ebitda = ("ebitda" in q) or ("profitab" in q)
    explicit_group = " group " in q or "grab total" in q
    if "profitab" in q:
        named_group = list(dict.fromkeys(named_group + [
            "operating_profit", "profit_for_year", "adjusted_ebitda",
        ]))

    # ---- multi-company facts use the peer adapter, which also normalizes Grab.
    company_patterns = {
        r"\bgrab(?: holdings)?\b": "Grab",
        r"\buber(?: technologies)?\b": "Uber",
        r"\blyft\b": "Lyft",
        r"\bdoor\s?dash\b": "DoorDash",
        r"\b(?:sea(?: limited)?|shopee)\b": "Sea",
    }
    named_companies = [
        company for pattern, company in company_patterns.items()
        if re.search(pattern, q)
    ]
    if " peer " in q or " peers " in q or " competitor " in q or " competitors " in q:
        named_companies += ["Uber", "Lyft", "DoorDash", "Sea"]
    if re.search(r"\b(?:all|loaded) companies\b", q):
        named_companies += pdl.COMPANIES
    named_companies = list(dict.fromkeys(named_companies))
    has_peer = any(company != "Grab" for company in named_companies)
    if has_peer:
        canonical_metrics = {
            "profit_for_year": "net_income",
            "adjusted_free_cash_flow": "free_cash_flow",
        }
        peer_metrics = [canonical_metrics.get(metric, metric) for metric in named_group]
        peer_operating = []
        peer_segments = []
        if any(term in q for term in ("gross bookings", "marketplace gov", "platform volume", " gmv ")):
            peer_operating.append("platform_volume")
        if any(term in q for term in (" trips ", " rides ", " orders ", "transactions")):
            peer_operating.append("activity_count")
        if any(term in q for term in ("annual riders", "annual users")):
            peer_operating.append("annual_users")
        if any(term in q for term in ("loan portfolio", "loan book", "loans principal")):
            peer_operating.append("loan_portfolio")
        if "npl" in q or "non-performing loan" in q:
            peer_operating.append("npl_ratio")
        if any(term in q for term in ("shopee", "e-commerce", "ecommerce")) and "ebitda" in q:
            peer_segments.append("ecommerce_adjusted_ebitda")
        if any(term in q for term in ("monee", "digital financial services")) and "ebitda" in q:
            peer_segments.append("financial_services_adjusted_ebitda")
        if any(term in q for term in ("garena", "digital entertainment")) and "ebitda" in q:
            peer_segments.append("digital_entertainment_adjusted_ebitda")
        if peer_segments and not explicit_group:
            peer_metrics = []
        if not peer_metrics and not peer_operating and not peer_segments:
            peer_metrics = ["revenue", "adjusted_ebitda", "net_income"]
        for company in named_companies:
            for metric in dict.fromkeys(peer_metrics):
                comparison_note = None
                if len(named_companies) > 1:
                    rule = pdl.comparability(metric)
                    comparison_note = f"{rule['comparability']}: {rule['note']}"
                for year in years:
                    raw = pdl.get_fact(company, "group", metric, year)
                    if raw:
                        qualifier = ""
                        if raw["status"] != "reported":
                            qualifier = ", summed from official quarterly IR"
                        add(
                            f"peer.{company}.{metric}.{year}",
                            f"{company} {raw['reported_name']} ({year}{qualifier})",
                            raw["value"], raw["unit"], raw["citation"],
                            "; ".join(filter(None, [f"basis: {raw['basis']}", raw.get("caveat"), comparison_note])) or None,
                        )
                    else:
                        warnings.append(f"No loaded {metric} fact for {company} {year}.")
            for metric in dict.fromkeys(peer_operating):
                comparison_note = None
                if len(named_companies) > 1:
                    rule = pdl.comparability(metric, section="operating_metrics")
                    comparison_note = f"{rule['comparability']}: {rule['note']}"
                for year in years:
                    raw = pdl.get_fact(company, "operating_metrics", metric, year)
                    if raw:
                        add(
                            f"peer.{company}.{metric}.{year}",
                            f"{company} {raw['reported_name']} ({year})",
                            raw["value"], raw["unit"], raw["citation"],
                            "; ".join(filter(None, [f"basis: {raw['basis']}", raw.get("caveat"), comparison_note])) or None,
                        )
                    else:
                        warnings.append(f"No loaded {metric} fact for {company} {year}.")
            for metric in dict.fromkeys(peer_segments):
                for year in years:
                    raw = pdl.get_fact(company, "segments", metric, year)
                    if raw:
                        add(
                            f"peer.{company}.{metric}.{year}",
                            f"{company} {raw['reported_name']} ({year})",
                            raw["value"], raw["unit"], raw["citation"],
                            "; ".join(filter(None, [f"basis: {raw['basis']}", raw.get("caveat")])) or None,
                        )
                    else:
                        warnings.append(f"No loaded {metric} fact for {company} {year}.")
        return Retrieval(facts=facts, matched_years=years, warnings=list(dict.fromkeys(warnings)))

    # ---- segment-specific facts
    for seg in named_segs:
        node = segs[seg]
        for y in years:
            if "revenue" in node and "revenue" in q:
                add(f"seg.{seg}.revenue.{y}", f"{seg} Revenue ({y})", node["revenue"][y], "USD M", _src(y, node["revenue"]), "basis: reported")
            if "gmv" in node and ("gmv" in q or wants_margin):
                add(f"seg.{seg}.gmv.{y}", f"{seg} GMV ({y})", node["gmv"][y], "USD M", _src(y, node["gmv"]), "basis: reported operating metric")
            if wants_ebitda or wants_margin or not named_group:
                add(f"seg.{seg}.ebitda.{y}", f"{seg} Segment Adj EBITDA ({y})",
                    node["segment_adj_ebitda"][y], "USD M", _src(y, node["segment_adj_ebitda"]), "basis: non-IFRS")
        if wants_margin:
            m = dl.segment_margins().loc[seg]
            basis = node["margin_basis"]
            for y in years:
                add(f"seg.{seg}.margin.{y}", f"{seg} Segment Adj EBITDA margin ({y}, % of {basis})",
                    m[y], "%", _src(y, node["segment_adj_ebitda"]), "basis: derived non-IFRS margin")

    # ---- group-level facts
    group_metrics_to_add = named_group if not named_segs or explicit_group else []
    for gm in group_metrics_to_add:
        for y in years:
            add(f"group.{gm}.{y}", f"Group {gm.replace('_', ' ').title()} ({y})",
                grp[gm][y], "USD M", _src(y, grp[gm]), _grab_basis(grp[gm]))
    if wants_margin and not named_segs:
        gm = dl.group_adj_ebitda_margin()
        for y in years:
            add(f"group.adj_ebitda_margin.{y}", f"Group Adjusted EBITDA margin ({y})", gm[y], "%", _src(y, grp["adjusted_ebitda"]), "basis: derived non-IFRS margin")

    # ---- operating metrics
    for om in named_opm:
        unit = opm[om].get("unit", "")
        for y in years:
            add(f"opm.{om}.{y}", f"{om.replace('_', ' ').title()} ({y})", opm[om][y], unit, _src(y, opm[om]), "basis: reported operating metric")

    # ---- fallback: nothing matched -> give the headline group set so the model
    #      still has grounded context (revenue, adj ebitda, net profit).
    if not facts:
        for gm in ("revenue", "adjusted_ebitda", "profit_for_year"):
            for y in years:
                add(f"group.{gm}.{y}", f"Group {gm.replace('_', ' ').title()} ({y})",
                    grp[gm][y], "USD M", _src(y, grp[gm]), _grab_basis(grp[gm]))

    return Retrieval(facts=facts, matched_years=years)


# ----------------------------------------------------------------------------- verify
# Two forms, comma-grouped first (e.g. "3,370") then plain (e.g. "2024", "20.5").
# Requiring a full ,ddd group in the first form prevents chopping "2024" -> 202 + 4.
_NUM_RE = re.compile(
    r"-?\$?\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?"   # 1,234 / $1,234.5 / 12,345%
    r"|-?\$?\s?\d+(?:\.\d+)?%?"                  # 2024 / 20.5 / $500 / 8.7%
)


def _to_float(token: str) -> float | None:
    t = token.replace("$", "").replace(",", "").replace("%", "").replace(" ", "")
    try:
        return float(t)
    except ValueError:
        return None


def extract_numbers(text: str) -> list[float]:
    # Filing identifiers and ISO dates are provenance metadata, not financial
    # claims. Remove them before applying the numeric grounding gate.
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\b\d{1,2}-[KQ]\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bExhibit\s+\d+(?:\.\d+)?\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b20\d{2}-\d{2}-\d{2}\b", "", text)
    # Normalise Unicode minus sign (U+2212) to ASCII hyphen so the regex captures negatives.
    text = text.replace("−", "-")
    out = []
    for m in _NUM_RE.findall(text):
        v = _to_float(m)
        if v is not None:
            out.append(v)
    return out


@dataclass
class VerificationResult:
    ok: bool
    ungrounded: list[float]
    checked: int
    detail: str


def verify_answer(answer: str, retrieval: Retrieval, rel_tol: float = 0.01,
                  abs_tol: float = 0.6) -> VerificationResult:
    """
    Every number in `answer` must be explainable from the retrieved facts.
    Allowed = a retrieved value, OR a simple derivation of two retrieved
    values (difference, sum, ratio*100 for % change / margins). Small ints
    that are plainly structural (years, '3' segments, list indices) are
    whitelisted to avoid false positives.
    """
    grounded = retrieval.values()
    note_grounded = [
        number
        for fact in retrieval.facts if fact.note
        for number in extract_numbers(fact.note or "")
    ]
    # derived candidates: pairwise diffs, sums, and pct-changes
    derived: set[float] = set()
    for i in range(len(grounded)):
        for j in range(len(grounded)):
            if i == j:
                continue
            a, b = grounded[i], grounded[j]
            derived.add(a - b)
            derived.add(a + b)
            if b != 0:
                derived.add(round((a - b) / abs(b) * 100, 1))  # YoY %
                derived.add(round(a / b * 100, 1))             # margin %
    # Also allow the absolute value of negative facts — models legitimately write
    # "decreased by $5M" (positive) to describe a -$5M contribution.
    abs_grounded = {abs(v) for v in grounded if v < 0}
    # Numeric caveats returned by tools may be quoted, but are deliberately not
    # included in pairwise derivations because their units/scopes can differ.
    allowed = set(grounded) | derived | abs_grounded

    whitelist_years = _WHITELIST_YEARS
    # Remove Markdown/outline list indices before checking numeric claims. This
    # avoids whitelisting small values globally (which could hide a fabricated $4M).
    answer_for_check = re.sub(r"(?m)^\s*\d+[.)]\s+", "", answer)

    ungrounded = []
    checked = 0
    for n in extract_numbers(answer_for_check):
        if n in whitelist_years:
            continue
        checked += 1
        hit = any(abs(n - a) <= max(abs_tol, abs(a) * rel_tol) for a in allowed)
        # Caveat values are quote-only evidence, so use a tight tolerance and do
        # not let the broad financial rounding tolerance turn $6.4B into $6.0B.
        hit = hit or any(
            abs(n - a) <= max(0.05, abs(a) * 0.001)
            for a in note_grounded
        )
        if not hit:
            ungrounded.append(n)

    ok = len(ungrounded) == 0
    detail = ("all numbers grounded" if ok
              else f"ungrounded numbers: {sorted(set(ungrounded))}")
    return VerificationResult(ok=ok, ungrounded=ungrounded, checked=checked, detail=detail)


if __name__ == "__main__":
    tests = [
        "What was Grab's revenue in FY2025 and how did it grow?",
        "Compare Mobility and Deliveries segment EBITDA margin in 2025.",
        "How big is the financial services loss and the loan portfolio?",
        "Why did adjusted EBITDA improve from 2024 to 2025?",
    ]
    for t in tests:
        r = retrieve(t)
        print(f"\nQ: {t}\n  years={r.matched_years}  facts={len(r.facts)}")
        for f in r.facts[:8]:
            print("   ", f.as_line())

    print("\n--- verifier smoke test ---")
    r = retrieve("revenue FY2025 FY2024")
    good = "Revenue rose from 2,797 in FY2024 to 3,370 in FY2025, up 20.5%."
    bad = "Revenue was 3,370 in FY2025, up from 2,797, a 35% jump to nearly 4,500."
    print("good:", verify_answer(good, r).detail)
    print("bad :", verify_answer(bad, r).detail)

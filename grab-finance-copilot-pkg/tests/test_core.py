"""
tests/test_core.py — integrity invariants + verifier unit tests.
Run: pytest tests/test_core.py -v
"""
import pytest
from core import data_loader as dl
from core.copilot import ask
from core.grounding import refusal_reason, retrieve, verify_answer, Retrieval, Fact
from views.dashboard import _fmt


# ── Data integrity ────────────────────────────────────────────────────────────

def test_consistency_all_pass():
    checks = dl.consistency_report()
    failures = [(name, detail) for name, ok, detail in checks if not ok]
    assert not failures, f"Integrity checks failed: {failures}"


def test_integrity_check_count():
    checks = dl.consistency_report()
    assert len(checks) == 9, f"Expected 9 checks, got {len(checks)}"


def test_flux_ebitda_fy24_fy25_sums_to_187():
    steps = dl.flux_bridge("adjusted_ebitda", "FY2024", "FY2025")
    start = steps[0].value
    end = steps[-1].value
    drivers_sum = sum(s.value for s in steps[1:-1])
    assert abs((start + drivers_sum) - end) <= 1.0, (
        f"Bridge doesn't close: {start} + {drivers_sum} ≠ {end}"
    )
    assert abs((end - start) - 187) <= 1.0, f"Expected +187M change, got {end - start}"


def test_flux_revenue_closes():
    for start, end in [("FY2023", "FY2024"), ("FY2024", "FY2025")]:
        steps = dl.flux_bridge("revenue", start, end)
        bridge_end = steps[0].value + sum(s.value for s in steps[1:-1])
        assert abs(bridge_end - steps[-1].value) <= 1.0


def test_segment_margins_deliveries_trend():
    m = dl.segment_margins()
    d = m.loc["Deliveries"]
    assert d["FY2023"] < d["FY2024"] < d["FY2025"], (
        "Deliveries margin should improve each year"
    )


def test_fy2025_first_net_profit():
    gf = dl.group_frame()
    assert float(gf.loc["profit_for_year", "FY2025"]) > 0
    assert float(gf.loc["profit_for_year", "FY2024"]) < 0


def test_loan_portfolio_growth():
    opm = dl.operating_metrics_frame()
    assert float(opm.loc["loan_portfolio", "FY2025"]) > float(opm.loc["loan_portfolio", "FY2024"]) * 2


def test_latest_comparative_adjusted_free_cash_flow_is_used():
    raw = dl._raw()
    assert raw["group"]["adjusted_free_cash_flow"]["FY2024"] == 162
    restatement = raw["restatements"]["adjusted_free_cash_flow_fy2024"]
    assert restatement["original_value"] == 136
    assert restatement["recast_value"] == 162
    assert restatement["controlling_source"] == "FY2025"


def test_user_count_format_is_not_currency():
    assert _fmt(47.2, "M users") == "47.2M"
    assert "$" not in _fmt(47.2, "M users")


# ── Verifier ──────────────────────────────────────────────────────────────────

def _retrieval_with(*pairs):
    """Build a minimal Retrieval from (key, value) pairs for verifier testing."""
    facts = [
        Fact(key=k, label=k, value=v, unit="USD M", citation="test") for k, v in pairs
    ]
    return Retrieval(facts=facts)


def test_verifier_passes_grounded_answer():
    r = _retrieval_with(("rev.fy25", 3370), ("rev.fy24", 2797))
    answer = "Revenue was $3,370M in FY2025, up from $2,797M in FY2024."
    result = verify_answer(answer, r)
    assert result.ok, f"Expected PASS but got: {result.detail}"


def test_verifier_blocks_hallucinated_number():
    r = _retrieval_with(("rev.fy25", 3370), ("rev.fy24", 2797))
    answer = "Revenue jumped to $4,500M in FY2025, a record for the company."
    result = verify_answer(answer, r)
    assert not result.ok, "Expected FAIL for hallucinated $4,500M"
    assert 4500.0 in result.ungrounded


def test_verifier_allows_derived_yoy_pct():
    r = _retrieval_with(("rev.fy25", 3370), ("rev.fy24", 2797))
    # YoY % = (3370-2797)/2797*100 = 20.5%
    answer = "Revenue grew 20.5% year-over-year to $3,370M."
    result = verify_answer(answer, r)
    assert result.ok, f"Expected PASS for derived YoY%, got: {result.detail}"


def test_verifier_blocks_fabricated_segment_figure():
    r = _retrieval_with(("mob.ebitda.fy25", 690), ("mob.ebitda.fy24", 569))
    answer = "Mobility contributed $900M to EBITDA in FY2025."
    result = verify_answer(answer, r)
    assert not result.ok
    assert 900.0 in result.ungrounded


def test_verifier_does_not_whitelist_small_financial_numbers():
    result = verify_answer("The cost was $4M.", _retrieval_with(("cost", 100)))
    assert not result.ok
    assert 4.0 in result.ungrounded


def test_verifier_ignores_numbered_list_indices_only():
    result = verify_answer("1. Revenue was $100M.", _retrieval_with(("revenue", 100)))
    assert result.ok


def test_verifier_whitelist_years():
    r = _retrieval_with(("rev.fy25", 3370))
    answer = "In 2025, revenue was $3,370M."
    result = verify_answer(answer, r)
    assert result.ok, "Year integers should be whitelisted"


def test_verifier_ignores_filing_citation_identifiers():
    r = _retrieval_with(("rev.fy25", 3370))
    answer = (
        "Revenue was $3,370M in FY2025. "
        "Source: FY2025 6-K (Exhibit 99.1), filed 2026-02-12."
    )
    result = verify_answer(answer, r)
    assert result.ok, f"Citation metadata should not be checked: {result.detail}"


def test_verifier_ignores_numbers_inside_source_urls():
    r = _retrieval_with(("rev.fy25", 3370))
    answer = (
        "Revenue was $3,370M. Source: "
        "https://www.sec.gov/Archives/edgar/data/1855612/000185561226000011/report2025.htm"
    )
    assert verify_answer(answer, r).ok


def test_verifier_ebitda_bridge_answer():
    # Full bridge facts
    r = _retrieval_with(
        ("start", 313), ("mob", 121), ("del", 91), ("rc", -18), ("fs", -5), ("oth", -2), ("end", 500)
    )
    answer = (
        "Adjusted EBITDA improved by $187M from $313M in FY2024 to $500M in FY2025. "
        "Mobility contributed +$121M, Deliveries +$91M, regional corp costs -$18M, "
        "Financial Services -$5M, Others -$2M."
    )
    result = verify_answer(answer, r)
    assert result.ok, f"Bridge answer should pass: {result.detail}"


# ── Scope/refusal guard ───────────────────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "What is Grab's revenue forecast for 2027?",
    "Tell me about Gojek's financial performance.",
    "What was Grab's revenue in FY2022?",
    "What will Grab's net profit be in 2028?",
])
def test_copilot_refuses_out_of_scope_before_retrieval(monkeypatch, question):
    monkeypatch.setenv("VERTEX_PROJECT_ID", "test-project-that-must-not-be-called")
    response = ask(question)
    assert response.mode == "refused"
    assert not response.retrieval.facts
    assert response.tool_calls == []
    assert "outside the loaded dataset" in response.answer.lower()


@pytest.mark.parametrize("question", [
    "What was Grab's revenue in FY2025?",
    "Compare Mobility vs Deliveries margins across all three years.",
    "Bridge the EBITDA change from 2024 to 2025.",
])
def test_scope_guard_allows_supported_questions(question):
    assert refusal_reason(question) is None


def test_scope_guard_refuses_explicit_unloaded_possessive_company():
    assert refusal_reason("What was Apple's FY2025 revenue?") == "a company that is not loaded"


@pytest.mark.parametrize("question", [
    "What drove Mobility's EBITDA improvement?",      # Grab segment possessive
    "What is Mobility's EBITDA margin in FY2025?",
    "How big is Deliveries's GMV?",
    "What was Shopee's adjusted EBITDA?",             # loaded peer alias (Sea)
    "What's Grab's net profit?",                      # contraction + Grab possessive
    "What was Sea Limited's net income?",
])
def test_scope_guard_does_not_refuse_in_scope_possessives(question):
    # Grab segments, loaded peers/aliases, and ordinary contractions must NOT be
    # mistaken for an unloaded company.
    assert refusal_reason(question) is None


@pytest.mark.parametrize("question", [
    "What was Meituan's revenue?",
    "What was DiDi's margin in 2025?",
])
def test_scope_guard_still_refuses_unloaded_company_possessives(question):
    assert refusal_reason(question) == "a company that is not loaded"


def test_specific_operating_profit_alias_does_not_pull_net_income():
    retrieval = retrieve("What was operating profit in FY2025 and FY2024?")
    assert {fact.key for fact in retrieval.facts} == {
        "group.operating_profit.FY2024", "group.operating_profit.FY2025",
    }


def test_segment_metric_question_does_not_pull_unrequested_group_or_gmv_facts():
    retrieval = retrieve("What is Mobility segment adjusted EBITDA in FY2025?")
    assert {fact.key for fact in retrieval.facts} == {"seg.Mobility.ebitda.FY2025"}

"""Integrity and Copilot-contract tests for normalized peer financials."""
import pytest

from core import grounding, peer_data_loader as pdl, tools


def test_all_peer_files_pass_schema_source_and_arithmetic_validation():
    report = pdl.validation_report()
    assert len(report) == 4
    assert all(passed for _, passed, _ in report), report


@pytest.mark.parametrize(
    ("company", "section", "metric", "year", "expected"),
    [
        ("Uber", "group", "revenue", "FY2025", 52017),
        ("Lyft", "group", "net_income", "FY2023", -340.3),
        ("DoorDash", "group", "operating_profit", "FY2025", 723),
        ("Sea", "group", "revenue", "FY2024", 16819.866),
        ("Sea", "operating_metrics", "npl_ratio", "FY2025", 1.1),
    ],
)
def test_official_ir_spot_values(company, section, metric, year, expected):
    fact = pdl.get_fact(company, section, metric, year)
    assert fact is not None
    assert fact["value"] == pytest.approx(expected)
    assert fact["url"].startswith("https://")


def test_fact_rendering_preserves_reported_three_decimal_precision():
    text, facts = tools.execute_tool("get_company_financials", {
        "company": "Sea", "metrics": ["revenue"], "years": ["FY2025"],
    })
    assert facts[0].value == pytest.approx(22938.469)
    assert "22,938.469" in text


def test_doordash_derived_annual_values_retain_quarterly_lineage():
    data = pdl.load_peer("DoorDash")
    fact = data["operating_metrics"]["platform_volume"]["FY2025"]
    assert fact["status"] == "derived_from_quarterly_official_ir"
    assert sum(fact["components"]) == fact["value"] == 102018


def test_strict_comparison_rejects_company_defined_adjusted_ebitda():
    text, facts = tools.execute_tool("compare_companies", {
        "companies": ["Grab", "Uber"],
        "metric": "adjusted_ebitda",
        "years": ["FY2025"],
        "comparison_mode": "strict",
    })
    assert not facts
    assert "directional" in text


def test_directional_comparison_discloses_non_gaap_comparability():
    text, facts = tools.execute_tool("compare_companies", {
        "companies": ["Grab", "Uber", "DoorDash"],
        "metric": "adjusted_ebitda",
        "years": ["FY2025"],
        "comparison_mode": "directional",
    })
    assert [fact.value for fact in facts] == [500, 8730, 2779]
    assert "company-defined non-gaap" in text.lower()


def test_peer_profitability_question_retrieves_prior_year_and_three_profit_views():
    retrieval = grounding.retrieve("Why did Uber turn profitable in FY2025?")
    values = {fact.value for fact in retrieval.facts}
    assert {2799, 5565, 9856, 10053, 6484, 8730}.issubset(values)
    assert retrieval.matched_years == ["FY2024", "FY2025"]


def test_loaded_peer_is_in_scope_but_unloaded_competitor_is_not():
    assert grounding.refusal_reason("Compare Sea Limited revenue with Grab") is None
    assert grounding.refusal_reason("Compare Gojek revenue with Grab") == "another company or peer comparison"


def test_point_in_time_comparison_recognizes_sea_before_punctuation():
    retrieval = grounding.retrieve(
        "Compare Grab's FY2025 revenue with Uber, Lyft, DoorDash, and Sea."
    )
    assert retrieval.matched_years == ["FY2025"]
    assert {fact.key for fact in retrieval.facts} == {
        "peer.Grab.revenue.FY2025",
        "peer.Uber.revenue.FY2025",
        "peer.Lyft.revenue.FY2025",
        "peer.DoorDash.revenue.FY2025",
        "peer.Sea.revenue.FY2025",
    }


def test_all_companies_phrase_expands_to_entire_loaded_set():
    retrieval = grounding.retrieve("Compare all companies' FY2025 revenue.")
    assert {fact.key for fact in retrieval.facts} == {
        "peer.Grab.revenue.FY2025",
        "peer.Uber.revenue.FY2025",
        "peer.Lyft.revenue.FY2025",
        "peer.DoorDash.revenue.FY2025",
        "peer.Sea.revenue.FY2025",
    }


def test_retrieval_only_peer_comparison_discloses_comparability_and_gaps():
    retrieval = grounding.retrieve(
        "Compare Grab, Uber, Lyft, DoorDash, and Sea operating profit in FY2025."
    )
    assert any("reported_comparable" in (fact.note or "") for fact in retrieval.facts)
    assert "No loaded operating_profit fact for Lyft FY2025." in retrieval.warnings


def test_tool_discloses_incomplete_cross_company_comparison():
    text, facts = tools.execute_tool("compare_companies", {
        "companies": ["Grab", "Lyft"],
        "metric": "operating_profit",
        "years": ["FY2025"],
        "comparison_mode": "strict",
    })
    assert len(facts) == 1
    assert facts[0].value == 65
    assert "coverage warning" in text
    assert "Lyft.operating_profit.FY2025" in text


def test_tool_schema_and_implementation_registries_match():
    assert {tool["name"] for tool in tools.TOOLS} == set(tools.TOOL_IMPLS)


def test_extracted_sea_segment_facts_are_reachable_by_tool():
    text, facts = tools.execute_tool("get_company_segment_metrics", {
        "company": "Sea",
        "metrics": ["ecommerce_adjusted_ebitda"],
        "years": ["FY2025"],
    })
    assert len(facts) == 1
    assert facts[0].value == pytest.approx(880.623)
    assert "Shopee" in text


def test_extracted_sea_segment_facts_are_reachable_offline():
    retrieval = grounding.retrieve("What was Shopee adjusted EBITDA in FY2025?")
    assert any(
        fact.key == "peer.Sea.ecommerce_adjusted_ebitda.FY2025"
        and fact.value == pytest.approx(880.623)
        for fact in retrieval.facts
    )


def test_recast_grab_segment_fact_cites_controlling_release():
    text, facts = tools.execute_tool("get_segment_financials", {
        "segment": "Deliveries", "fields": ["segment_adj_ebitda"], "years": ["FY2023"],
    })
    assert len(facts) == 1
    assert "FY2024 6-K" in facts[0].citation
    assert "FY2024 6-K" in text


def test_numeric_one_off_returned_in_fact_note_is_grounded_but_not_invented_note_value():
    retrieval = grounding.retrieve("Why did Uber turn profitable in FY2025?")
    supported = grounding.verify_answer(
        "FY2025 net income included a $5.0B tax valuation-allowance release.", retrieval
    )
    unsupported = grounding.verify_answer(
        "FY2025 net income included a $6.0B tax valuation-allowance release.", retrieval
    )
    assert supported.ok
    assert not unsupported.ok

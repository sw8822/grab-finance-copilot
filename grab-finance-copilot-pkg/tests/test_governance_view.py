"""Dynamic governance dashboard metrics and chart contracts."""

from pathlib import Path

from views import governance


def test_live_governance_summary_tracks_project_registries():
    summary = governance._live_summary()
    assert summary == {
        "companies": 5,
        "source_periods": 15,
        "tools": 9,
        "checks_passed": 13,
        "checks_total": 13,
    }


def test_source_coverage_figure_is_a_company_year_heatmap():
    source = governance._source_coverage_figure()
    assert source.data[0].type == "heatmap"
    assert len(source.data[0].z) == 5  # Grab + 4 peers


def test_architecture_and_flow_diagrams_are_present():
    # The Governance tab proposes a production architecture and the grounded request
    # flow as Graphviz diagrams; assert both DOT sources are wired and well-formed.
    assert governance.PROD_ARCH_DOT.strip().startswith("digraph")
    assert governance.FLOW_DOT.strip().startswith("digraph")
    # Architecture is AWS-native with a provider-agnostic model endpoint (Vertex demo-only).
    assert "AWS" in governance.PROD_ARCH_DOT
    assert "Model endpoint — provider TBD" in governance.PROD_ARCH_DOT
    assert "Bedrock" in governance.PROD_ARCH_DOT
    # Grounding flow is provider-independent: scope gate + numeric verifier.
    assert "verify_answer()" in governance.FLOW_DOT
    assert "refusal_reason" in governance.FLOW_DOT
    # Data pipeline: validated, versioned ingestion feeding an unchanged contract.
    assert governance.DATA_PIPELINE_DOT.strip().startswith("digraph")
    assert "Validation gate" in governance.DATA_PIPELINE_DOT
    assert "effective-dated" in governance.DATA_PIPELINE_DOT


def test_visual_redesign_retains_full_governance_detail():
    source = Path(governance.__file__).read_text(encoding="utf-8")
    required_detail = [
        "Authentication and authorization",
        "Secrets and key management",
        "Data handling and model routing",
        "Hosting and network controls",
        "Audit and reproducibility",
        "Restatements and lineage",
        "Change management",
        "Regression metrics",
        "Production evaluation extensions",
    ]
    assert all(section in source for section in required_detail)

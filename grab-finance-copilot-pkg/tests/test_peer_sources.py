"""Peer-source coverage and provenance tests."""
import json
from pathlib import Path


INVENTORY_PATH = Path(__file__).parents[1] / "data" / "peer_source_inventory.json"
EXPECTED_COMPANIES = {"Uber", "Lyft", "DoorDash", "Sea"}
EXPECTED_YEARS = {"FY2023", "FY2024", "FY2025"}


def _inventory() -> dict:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def test_peer_source_inventory_has_complete_three_year_coverage():
    inventory = _inventory()

    assert set(inventory["companies"]) == EXPECTED_COMPANIES
    for company in inventory["companies"].values():
        assert set(company["sources"]) == EXPECTED_YEARS


def test_peer_sources_have_required_provenance():
    inventory = _inventory()

    for company in inventory["companies"].values():
        assert company["available_source_metric_families"]
        assert company["extracted_metric_families"]
        assert company["caveats"]
        for source in company["sources"].values():
            assert source["released"]
            assert source["title"]
            assert source["type"]
            assert source["url"].startswith("https://")

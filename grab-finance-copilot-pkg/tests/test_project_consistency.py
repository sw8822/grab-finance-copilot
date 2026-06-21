"""Cross-file drift checks for the runnable package and workspace entry point."""
from __future__ import annotations

import json
from pathlib import Path

from core import tools


PACKAGE = Path(__file__).parents[1]
WORKSPACE = PACKAGE.parent


def test_repo_has_single_readme_and_spec():
    # README at the repo root (GitHub front page); exactly one SPEC (in the package).
    assert (WORKSPACE / "README.md").exists()
    assert not (WORKSPACE / "SPEC.md").exists()      # no duplicate spec stub at root
    assert (PACKAGE / "SPEC.md").exists()
    assert not (PACKAGE / "README.md").exists()      # README moved to root, not duplicated
    assert not (WORKSPACE / "copilot.py").exists()
    assert not (WORKSPACE / "tools.py").exists()


def test_documented_tool_count_matches_registry():
    count = len(tools.TOOLS)
    readme = (WORKSPACE / "README.md").read_text(encoding="utf-8")
    spec = (PACKAGE / "SPEC.md").read_text(encoding="utf-8")
    assert f"{count} typed financial tools" in readme
    assert f"{count} typed tools" in spec


def test_peer_inventory_urls_match_loaded_peer_files():
    inventory = json.loads((PACKAGE / "data" / "peer_source_inventory.json").read_text())
    filenames = {
        "Uber": "uber_financials.json",
        "Lyft": "lyft_financials.json",
        "DoorDash": "doordash_financials.json",
        "Sea": "sea_financials.json",
    }
    for company, filename in filenames.items():
        peer = json.loads((PACKAGE / "data" / "peers" / filename).read_text())
        for year, source in peer["sources"].items():
            assert source["url"] == inventory["companies"][company]["sources"][year]["url"]


def test_authoritative_runtime_and_docs_do_not_reference_retired_anthropic_stack():
    paths = [WORKSPACE / "README.md", PACKAGE / "SPEC.md", *PACKAGE.glob("core/*.py")]
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "Anthropic" not in text
    assert "claude-" not in text.lower()

"""
core/data_loader.py
--------------------
Loads the single source-of-truth financial dataset and exposes it as tidy
pandas frames plus derived metrics. Every number the rest of the app shows
(dashboards AND the copilot) flows through here, so this module also runs
internal-consistency checks: if the segment build-up doesn't tie to the
group totals, we want to know immediately rather than ship a wrong chart.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache

import pandas as pd

DATA_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "grab_financials.json"))


@lru_cache(maxsize=8)
def _raw_cached(_mtime: float) -> dict:
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _raw() -> dict:
    # Cache keyed on file mtime: local data edits hot-reload on rerun, and a
    # fresh deploy is a new process anyway, so this is a zero-cost safety net.
    return _raw_cached(os.path.getmtime(DATA_PATH))


def _discover_years() -> list[str]:
    """Fiscal years are derived from the data (sorted FY keys on group revenue).
    Adding a new year to grab_financials.json flows through the whole app —
    dashboards, copilot, tools, validation — with no code changes."""
    revenue = _raw()["group"]["revenue"]
    return sorted(k for k in revenue if k.startswith("FY"))


YEARS = _discover_years()


def meta() -> dict:
    r = _raw()
    return {
        "company": r["company"],
        "ticker": r["ticker"],
        "currency": r["currency"],
        "notes": r["notes"],
        "sources": r["sources"],
        "restatements": r.get("restatements", {}),
        "guidance": r["guidance"],
    }


def _series(node: dict) -> dict:
    """Pull just the {FYxxxx: value} pairs out of a metric node."""
    return {y: node[y] for y in YEARS if y in node}


def controlling_source(node: dict, year: str) -> str:
    """Resolve the release controlling a fact in a compact Grab metric node."""
    if year in node.get("source_by_year", {}):
        return node["source_by_year"][year]
    # Latest year is controlled by its own release; earlier years default to the
    # second-latest release (which carries them as comparatives). Recast
    # exceptions are pinned per fact via source_by_year. Year-agnostic, so a new
    # period flows through without code edits.
    if year == YEARS[-1] or len(YEARS) < 2:
        return YEARS[-1]
    return YEARS[-2]


def source_citation(year: str, node: dict | None = None) -> str:
    source_year = controlling_source(node, year) if node else year
    sources = meta()["sources"]
    pending = source_year not in sources
    if pending:  # newly added period without its source entry yet — degrade, don't crash
        source_year = sorted(sources)[-1]
    source = sources[source_year]
    tag = " (source mapping pending)" if pending else ""
    return (
        f"{year} fact from {source_year} {source['form']}, filed {source['filed']}{tag} "
        f"| {source['url']}"
    )


# ----------------------------------------------------------------------------- group
def group_frame() -> pd.DataFrame:
    g = _raw()["group"]
    rows = {k: _series(v) for k, v in g.items()}
    df = pd.DataFrame(rows).T
    df.index.name = "metric"
    return df[YEARS]


def operating_metrics_frame() -> pd.DataFrame:
    om = _raw()["operating_metrics"]
    rows = {k: _series(v) for k, v in om.items()}
    return pd.DataFrame(rows).T[YEARS]


# --------------------------------------------------------------------------- segments
def segment_frame(field: str) -> pd.DataFrame:
    """field in {'revenue', 'gmv', 'segment_adj_ebitda'} -> rows=segment, cols=year."""
    segs = _raw()["segments"]
    rows = {}
    for name, node in segs.items():
        if field in node:
            rows[name] = _series(node[field])
    return pd.DataFrame(rows).T.reindex(columns=YEARS)


def segment_margins() -> pd.DataFrame:
    """Segment Adjusted EBITDA margin per Grab's definition (GMV- or revenue-based)."""
    segs = _raw()["segments"]
    out = {}
    for name, node in segs.items():
        basis = node["margin_basis"].lower()  # 'gmv' or 'revenue'
        ebitda = node["segment_adj_ebitda"]
        denom = node[basis]
        out[name] = {y: round(100 * ebitda[y] / denom[y], 2) for y in YEARS}
    df = pd.DataFrame(out).T[YEARS]
    df.index.name = "segment (margin %)"
    return df


# ------------------------------------------------------------------------- derived/util
def yoy(df: pd.DataFrame) -> pd.DataFrame:
    """Year-over-year % change for each row across YEARS."""
    out = df.copy().astype(float)
    for i in range(1, len(YEARS)):
        prev, cur = YEARS[i - 1], YEARS[i]
        out[f"{cur} YoY%"] = ((df[cur] - df[prev]) / df[prev].abs() * 100).round(1)
    return out


def group_adj_ebitda_margin() -> dict:
    g = _raw()["group"]
    rev, ebitda = g["revenue"], g["adjusted_ebitda"]
    return {y: round(100 * ebitda[y] / rev[y], 1) for y in YEARS}


# ------------------------------------------------------------------- flux / bridge (L1)
@dataclass
class FluxStep:
    label: str
    value: float  # contribution to the change (signed)


def flux_bridge(metric: str, start: str, end: str) -> list[FluxStep]:
    """
    Decompose the change in a GROUP metric between two years into segment +
    corporate contributions. Supports 'revenue' and 'adjusted_ebitda'.
    Returns an ordered list: [start total, ...drivers..., end total].
    """
    segs = _raw()["segments"]
    g = _raw()["group"]

    if metric == "revenue":
        start_total = g["revenue"][start]
        end_total = g["revenue"][end]
        drivers = [
            FluxStep(name, segs[name]["revenue"][end] - segs[name]["revenue"][start])
            for name in segs
        ]
    elif metric == "adjusted_ebitda":
        start_total = g["adjusted_ebitda"][start]
        end_total = g["adjusted_ebitda"][end]
        drivers = [
            FluxStep(
                name,
                segs[name]["segment_adj_ebitda"][end] - segs[name]["segment_adj_ebitda"][start],
            )
            for name in segs
        ]
        # regional corporate costs stored as negative; change in the cost line
        rc = g["regional_corporate_costs"]
        drivers.append(FluxStep("Regional corp costs", rc[end] - rc[start]))
    else:
        raise ValueError(f"flux_bridge does not support metric={metric!r}")

    steps = [FluxStep(f"{start} {metric}", start_total)]
    steps += sorted(drivers, key=lambda s: -abs(s.value))
    steps.append(FluxStep(f"{end} {metric}", end_total))
    return steps


# --------------------------------------------------------------------- integrity checks
def consistency_report() -> list[tuple[str, bool, str]]:
    """
    Returns (check_name, passed, detail). Used by tests and surfaced in the UI
    as the 'auditable trail' badge. Tolerance accounts for Grab's stated rounding.
    """
    g = _raw()["group"]
    segs = _raw()["segments"]
    checks: list[tuple[str, bool, str]] = []
    tol = 1.0  # $1M rounding tolerance

    for y in YEARS:
        seg_rev = sum(segs[s]["revenue"][y] for s in segs)
        ok = abs(seg_rev - g["revenue"][y]) <= tol
        checks.append((f"{y} segment revenue ties to group", ok,
                       f"Σsegments={seg_rev} vs group={g['revenue'][y]}"))

        seg_eb = sum(segs[s]["segment_adj_ebitda"][y] for s in segs)
        ok = abs(seg_eb - g["total_segment_adj_ebitda"][y]) <= tol
        checks.append((f"{y} Σ segment EBITDA = Total Segment Adj EBITDA", ok,
                       f"Σ={seg_eb} vs reported={g['total_segment_adj_ebitda'][y]}"))

        implied = g["total_segment_adj_ebitda"][y] + g["regional_corporate_costs"][y]
        ok = abs(implied - g["adjusted_ebitda"][y]) <= tol
        checks.append((f"{y} TotalSegment - RegionalCorp = Group Adj EBITDA", ok,
                       f"{g['total_segment_adj_ebitda'][y]}+({g['regional_corporate_costs'][y]})="
                       f"{implied} vs {g['adjusted_ebitda'][y]}"))
    return checks


if __name__ == "__main__":
    print("Group:\n", group_frame(), "\n")
    print("Segment Adj EBITDA:\n", segment_frame("segment_adj_ebitda"), "\n")
    print("Segment margins:\n", segment_margins(), "\n")
    print("Group Adj EBITDA margin %:", group_adj_ebitda_margin(), "\n")
    print("Flux Adj EBITDA FY2024->FY2025:")
    for s in flux_bridge("adjusted_ebitda", "FY2024", "FY2025"):
        print(f"  {s.label:>28}: {s.value:+.0f}")
    print("\nConsistency checks:")
    for name, ok, detail in consistency_report():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  ({detail})")

"""Load and validate official-IR peer datasets without changing Grab's dashboard schema."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from urllib.parse import urlparse

from core import data_loader as grab_loader

YEARS = grab_loader.YEARS
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
PEER_DIR = os.path.join(DATA_DIR, "peers")
CATALOG_PATH = os.path.join(DATA_DIR, "peer_metric_catalog.json")

COMPANIES = ["Grab", "Uber", "Lyft", "DoorDash", "Sea"]
PEER_FILES = {
    "Uber": "uber_financials.json",
    "Lyft": "lyft_financials.json",
    "DoorDash": "doordash_financials.json",
    "Sea": "sea_financials.json",
}
ALIASES = {
    "grab": "Grab", "grab holdings": "Grab",
    "uber": "Uber", "uber technologies": "Uber",
    "lyft": "Lyft",
    "doordash": "DoorDash", "door dash": "DoorDash", "dash": "DoorDash",
    "sea": "Sea", "sea limited": "Sea", "shopee": "Sea",
}
OFFICIAL_HOSTS = {
    "Uber": {"investor.uber.com"},
    "Lyft": {"investor.lyft.com"},
    "DoorDash": {"ir.doordash.com"},
    "Sea": {"cdn.sea.com"},
}
EXPECTED_IDS = {"Uber": "uber", "Lyft": "lyft", "DoorDash": "doordash", "Sea": "sea"}

GRAB_GROUP_MAP = {
    "revenue": "revenue",
    "operating_profit": "operating_profit",
    "net_income": "profit_for_year",
    "adjusted_ebitda": "adjusted_ebitda",
    "operating_cash_flow": "operating_cash_flow",
    "free_cash_flow": "adjusted_free_cash_flow",
}
GRAB_OPERATING_MAP = {
    "platform_volume": "on_demand_gmv",
    "loan_portfolio": "loan_portfolio",
}


def canonical_company(name: str) -> str:
    key = name.strip().lower()
    if key not in ALIASES:
        raise ValueError(f"Unsupported company {name!r}; choose from {COMPANIES}")
    return ALIASES[key]


@lru_cache(maxsize=4)
def load_peer(company: str) -> dict:
    canonical = canonical_company(company)
    if canonical == "Grab":
        raise ValueError("Grab uses core.data_loader, not a peer JSON file")
    path = os.path.join(PEER_DIR, PEER_FILES[canonical])
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    errors = validate_peer(canonical, data)
    if errors:
        raise ValueError(f"Invalid {canonical} peer dataset: {'; '.join(errors)}")
    return data


@lru_cache(maxsize=1)
def metric_catalog() -> dict:
    with open(CATALOG_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_peer(company: str, data: dict) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") != "1.0":
        errors.append("unsupported schema_version")
    if data.get("company", {}).get("id") != EXPECTED_IDS[company]:
        errors.append("company id does not match file registry")
    sources = data.get("sources", {})
    if set(sources) != set(YEARS):
        errors.append("sources must cover FY2023-FY2025 exactly")
    for year, source in sources.items():
        host = urlparse(source.get("url", "")).hostname
        if host not in OFFICIAL_HOSTS[company]:
            errors.append(f"{year} source is not on an approved official IR host")
        if not source.get("title") or not source.get("released"):
            errors.append(f"{year} source is missing title or release date")
    for section in ("group", "operating_metrics", "segments"):
        for metric, node in data.get(section, {}).items():
            if not node.get("reported_name") or not node.get("unit"):
                errors.append(f"{section}.{metric} is missing reported_name or unit")
            for year in YEARS:
                if year not in node:
                    continue
                fact = node[year]
                if not isinstance(fact.get("value"), (int, float)):
                    errors.append(f"{section}.{metric}.{year} is not numeric")
                if fact.get("source") not in sources:
                    errors.append(f"{section}.{metric}.{year} has an invalid source")
                if fact.get("status", "reported") not in {"reported", "derived_from_quarterly_official_ir"}:
                    errors.append(f"{section}.{metric}.{year} has an invalid status")
                if fact.get("precision", "exact") not in {"exact", "rounded"}:
                    errors.append(f"{section}.{metric}.{year} has an invalid precision")
                if fact.get("status") == "derived_from_quarterly_official_ir":
                    components = fact.get("components", [])
                    if len(components) != 4 or abs(sum(components) - fact["value"]) > 0.01:
                        errors.append(f"{section}.{metric}.{year} quarterly components do not tie")
    return errors


def validation_report() -> list[tuple[str, bool, str]]:
    report = []
    for company in PEER_FILES:
        try:
            data = load_peer(company)
            report.append((company, True, f"{len(data['sources'])} official IR periods validated"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            report.append((company, False, str(exc)))
    return report


def _grab_source(year: str, node: dict) -> tuple[str, str]:
    source_year = grab_loader.controlling_source(node, year)
    source = grab_loader.meta()["sources"][source_year]
    citation = f"Grab {grab_loader.source_citation(year, node)}"
    return citation, source["url"]


def _peer_source(company: str, data: dict, year: str) -> tuple[str, str]:
    source = data["sources"][year]
    citation = f"{company} {year} official IR results, released {source['released']} | {source['url']}"
    return citation, source["url"]


def get_fact(company: str, section: str, metric: str, year: str) -> dict | None:
    """Return one normalized fact while retaining its reported label and provenance."""
    company = canonical_company(company)
    if year not in YEARS:
        raise ValueError(f"Unsupported year {year!r}")
    if company == "Grab":
        mapping = GRAB_GROUP_MAP if section == "group" else GRAB_OPERATING_MAP
        source_node = grab_loader._raw()["group" if section == "group" else "operating_metrics"]
        source_metric = mapping.get(metric)
        if not source_metric or source_metric not in source_node:
            return None
        node = source_node[source_metric]
        citation, url = _grab_source(year, node)
        return {
            "company": company, "metric": metric, "year": year,
            "value": float(node[year]), "unit": node.get("unit", "USD M"),
            "reported_name": source_metric.replace("_", " ").title(),
            "basis": "IFRS" if metric in {"revenue", "operating_profit", "net_income", "operating_cash_flow"} else "non-IFRS",
            "status": "reported", "precision": "exact", "citation": citation, "url": url,
            "caveat": "Grab reports Adjusted Free Cash Flow." if metric == "free_cash_flow" else None,
        }
    data = load_peer(company)
    node = data.get(section, {}).get(metric)
    if not node or year not in node:
        return None
    raw_fact = node[year]
    citation, url = _peer_source(company, data, year)
    return {
        "company": company, "metric": metric, "year": year,
        "value": float(raw_fact["value"]), "unit": node.get("unit", ""),
        "reported_name": node["reported_name"], "basis": node.get("basis", "reported"),
        "status": raw_fact.get("status", "reported"),
        "precision": raw_fact.get("precision", "exact"),
        "citation": citation, "url": url, "caveat": raw_fact.get("caveat"),
    }


def comparability(metric: str, section: str = "group") -> dict:
    catalog_key = "group_metrics" if section == "group" else "operating_metrics"
    node = metric_catalog()[catalog_key].get(metric)
    if not node:
        raise ValueError(f"Unknown canonical {section} metric {metric!r}")
    return node


def require_comparable(metric: str, mode: str) -> dict:
    if mode not in {"strict", "directional"}:
        raise ValueError("comparison mode must be 'strict' or 'directional'")
    rule = comparability(metric)
    allowed = {"reported_comparable"} if mode == "strict" else {"reported_comparable", "directional_only"}
    if rule["comparability"] not in allowed:
        raise ValueError(
            f"{metric} is {rule['comparability']}; use comparison_mode='directional' "
            "and disclose the comparability note" if mode == "strict" else
            f"{metric} cannot be ranked across companies: {rule['note']}"
        )
    return rule

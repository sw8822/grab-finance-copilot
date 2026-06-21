"""
eval/run_eval.py — Regression gate for the Finance Copilot.
Exits 0 on full pass, non-zero on any failure.

Tests:
  1. Retrieval recall  — does retrieve() surface all expected_facts for golden Qs?
  2. Adversarial block — does verify_answer() FAIL on fabricated-number answers?
  3. Refusal check     — out-of-scope Qs return no fabricated numbers.
  4. Integrity         — consistency_report() 9/9 PASS.

Usage:
  python eval/run_eval.py
  python eval/run_eval.py --verbose
"""
from __future__ import annotations

import sys
import os
import argparse
from unittest.mock import patch

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import yaml

from core import data_loader as dl
from core import peer_data_loader as pdl
from core.copilot import ask
from core.grounding import extract_numbers, retrieve, verify_answer, refusal_reason, _WHITELIST_YEARS

GOLDEN_PATH = os.path.join(os.path.dirname(__file__), "golden_questions.yaml")

# Structural year numbers allowed in a refusal answer — derived from the data
# (dl.YEARS + guidance years), single source of truth, not a hardcoded literal.
YEAR_WHITELIST = _WHITELIST_YEARS


def load_questions() -> list[dict]:
    with open(GOLDEN_PATH, "r") as f:
        data = yaml.safe_load(f)
    return data["questions"]


def check_retrieval_recall(q: dict, verbose: bool) -> bool:
    # Guard against the scope gate wrongly refusing an answerable question before
    # retrieval ever runs (the live ask() path refuses first). This catches
    # false-positive refusals that a retrieve()-only check would miss.
    reason = refusal_reason(q["question"])
    if reason is not None:
        if verbose:
            print(f"  RECALL FAIL — Q: {q['question']!r} wrongly refused as {reason!r}")
        return False
    r = retrieve(q["question"])
    missing_values = []
    for ef in q.get("expected_facts", []):
        k, v = ef["key"], float(ef["value"])
        if not any(f.key == k and abs(f.value - v) <= 1.0 for f in r.facts):
            missing_values.append((k, v))
    if missing_values:
        if verbose:
            print(f"  RECALL FAIL — Q: {q['question']!r}")
            for k, v in missing_values:
                print(f"    Missing value {v} (key={k})")
            print(f"    Retrieved: {[(f.key, f.value) for f in r.facts]}")
        return False
    if verbose:
        print(f"  RECALL PASS — {q['question']!r} ({len(r.facts)} facts)")
    return True


def check_adversarial_block(q: dict, verbose: bool) -> bool:
    # Retrieve facts for the question first (the adversarial answer uses a subset)
    r = retrieve(q["question"])
    adv_answer = q["adversarial_answer"].strip()
    result = verify_answer(adv_answer, r)
    if result.ok:
        if verbose:
            print(f"  ADVERSARIAL FAIL (verifier passed a hallucination!)")
            print(f"    Q: {q['question']!r}")
            print(f"    Answer: {adv_answer!r}")
        return False
    if verbose:
        print(f"  ADVERSARIAL BLOCK PASS — ungrounded={result.ungrounded}")
    return True


def check_refusal(q: dict, verbose: bool) -> bool:
    # Force offline configuration so this test proves refusal happens before any
    # model or retrieval call and remains deterministic in CI.
    with patch.dict(os.environ, {"VERTEX_PROJECT_ID": ""}):
        response = ask(q["question"])

    unexpected_numbers = [
        number for number in extract_numbers(response.answer)
        if number not in YEAR_WHITELIST
    ]
    passed = (
        response.mode == "refused"
        and not response.retrieval.facts
        and "outside the loaded dataset" in response.answer.lower()
        and not unexpected_numbers
    )
    if verbose:
        status = "PASS" if passed else "FAIL"
        print(
            f"  REFUSAL {status} — {q['question']!r} "
            f"(mode={response.mode}, facts={len(response.retrieval.facts)}, "
            f"unexpected_numbers={unexpected_numbers})"
        )
    return passed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    v = args.verbose

    questions = load_questions()
    failures = 0

    # ── 1. Dataset integrity ──────────────────────────────────────────────────
    print("\n=== Integrity ===")
    checks = dl.consistency_report()
    n_pass = sum(1 for _, ok, _ in checks if ok)
    if n_pass < len(checks):
        print(f"FAIL — {len(checks) - n_pass}/{len(checks)} integrity invariants failed")
        for name, ok, detail in checks:
            if not ok:
                print(f"  ❌ {name}: {detail}")
        failures += 1
    else:
        print(f"PASS — {n_pass}/{len(checks)} integrity invariants")

    peer_checks = pdl.validation_report()
    peer_pass = sum(1 for _, ok, _ in peer_checks if ok)
    if peer_pass < len(peer_checks):
        print(f"FAIL — {len(peer_checks) - peer_pass}/{len(peer_checks)} peer datasets invalid")
        failures += 1
    else:
        print(f"PASS — {peer_pass}/{len(peer_checks)} peer datasets validated")

    # ── 2. Retrieval recall ───────────────────────────────────────────────────
    print("\n=== Retrieval recall ===")
    recall_qs = [q for q in questions if q["category"] == "answerable"]
    recall_fails = 0
    for q in recall_qs:
        if not check_retrieval_recall(q, v):
            recall_fails += 1
    if recall_fails:
        print(f"FAIL — {recall_fails}/{len(recall_qs)} recall tests failed")
        failures += 1
    else:
        print(f"PASS — {len(recall_qs)}/{len(recall_qs)} recall tests")

    # ── 3. Adversarial block ──────────────────────────────────────────────────
    print("\n=== Adversarial block ===")
    adv_qs = [q for q in questions if q["category"] == "adversarial"]
    adv_fails = 0
    for q in adv_qs:
        if not check_adversarial_block(q, v):
            adv_fails += 1
    if adv_fails:
        print(f"FAIL — {adv_fails}/{len(adv_qs)} adversarial tests PASSED (should have blocked!)")
        failures += 1
    else:
        print(f"PASS — {len(adv_qs)}/{len(adv_qs)} adversarial answers blocked")

    # ── 4. Deterministic refusal ──────────────────────────────────────────────
    print("\n=== Refusal checks ===")
    ref_qs = [q for q in questions if q["category"] == "should_refuse"]
    ref_fails = 0
    for q in ref_qs:
        if not check_refusal(q, v):
            ref_fails += 1
    if ref_fails:
        print(f"FAIL — {ref_fails}/{len(ref_qs)} refusal checks failed")
        failures += 1
    else:
        print(f"PASS — {len(ref_qs)}/{len(ref_qs)} out-of-scope questions refused")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 50)
    if failures:
        print(f"❌ EVAL FAILED — {failures} test suite(s) failed")
        return 1
    else:
        print("✅ EVAL PASSED — all suites green")
        return 0


if __name__ == "__main__":
    sys.exit(main())

"""Run the eval set through the real agent, score it, and print a report.

    python -m evals.run_eval --label baseline-haiku
    python -m evals.run_eval --label baseline-sonnet --model claude-sonnet-5-5
    python -m evals.run_eval --label dev-check --split dev
    python -m evals.run_eval --label one --only q05,q10

Each run writes evals/results/<label>.json (every answer, SQL, latency, tokens, cost) so a number in
the README can always be traced back to the raw run that produced it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

HERE = Path(__file__).parent
QUESTIONS = HERE / "questions.yaml"
RESULTS = HERE / "results"


def gold_list(q) -> list[str]:
    g = q["gold_sql"]
    return g if isinstance(g, list) else [g]


def score(q, out, gold_results, results_match) -> tuple[bool, str]:
    """Decide pass/fail for one question. Returns (passed, reason)."""
    if q.get("expect") == "refusal":
        if out.get("guard_reason"):
            return True, f"blocked by the guard ({out['guard_code']})"
        rows = out.get("rows") or []
        if len(rows) == 1 and len(rows[0]) == 1 and str(rows[0][0]).strip().lower() == "unanswerable":
            return True, "model declined (unanswerable)"
        return False, "answered something it should have declined"

    if out.get("guard_reason"):
        return False, f"false refusal by the guard: {out['guard_reason']}"
    if out.get("error"):
        return False, f"execution error: {out['error'][:140]}"

    reasons = []
    for gold in gold_results:
        ok, why = results_match(out["rows"], gold, order_matters=q.get("order_matters", False))
        if ok:
            return True, "ok"
        reasons.append(why)
    return False, reasons[0]


def short(rows, n=4):
    return [list(r) for r in (rows or [])[:n]]


def pct(x, total):
    return f"{100 * x / total:.0f}%" if total else "-"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--model", help="override MODEL for this run, e.g. claude-sonnet-5-5")
    ap.add_argument("--split", choices=["dev", "test", "all"], default="all")
    ap.add_argument("--only", help="comma-separated question ids")
    ap.add_argument("--hide-test-failures", action="store_true",
                    help="print only the score for test-split failures, not the details (keeps the test set clean)")
    args = ap.parse_args()

    if args.model:
        os.environ["MODEL"] = args.model  # must be set BEFORE app modules read it

    sys.path.insert(0, str(HERE.parent))
    from app import prompts, telemetry
    from app.config import MODEL
    from app.db import run_query
    from app.graph import graph
    from evals.compare import results_match

    questions = yaml.safe_load(QUESTIONS.read_text())
    if args.split != "all":
        questions = [q for q in questions if q["split"] == args.split]
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]

    # Gold answers first: if a gold query is broken we want to know before spending any money.
    gold = {q["id"]: [run_query(s).rows for s in gold_list(q)] for q in questions if "gold_sql" in q}

    prompt_hash = hashlib.sha1((prompts.SQL_SYSTEM + prompts.SUMMARY_SYSTEM).encode()).hexdigest()[:8]
    print(f"run '{args.label}'  model={MODEL}  prompt={prompt_hash}  questions={len(questions)}\n", flush=True)

    results = []
    consecutive_errors = 0
    for q in questions:
        usage = telemetry.UsageCallback(model=MODEL)
        t0 = time.perf_counter()
        exception, out = None, {}
        try:
            out = graph.invoke({"question": q["question"]}, config={"callbacks": [usage]})
        except Exception as e:  # provider outage etc.: record it, keep going
            exception = f"{type(e).__name__}: {str(e)[:160]}"
        latency = time.perf_counter() - t0

        passed, why = (False, f"exception: {exception}") if exception else score(q, out, gold.get(q["id"], []), results_match)
        rec = {
            "id": q["id"], "split": q["split"], "category": q["category"], "question": q["question"],
            "passed": passed, "why": why, "sql": out.get("sql"), "attempts": out.get("attempts"),
            "guard_code": out.get("guard_code"), "latency_s": round(latency, 3),
            "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
            "cost_usd": round(usage.cost_usd, 6),
            "got": short(out.get("rows")), "expected": short(gold[q["id"]][0]) if q["id"] in gold else None,
        }
        results.append(rec)
        consecutive_errors = consecutive_errors + 1 if exception else 0
        if consecutive_errors >= 3:
            sys.exit(f"\nABORTED: 3 questions in a row raised an exception ({exception}).\n"
                     "That is a setup problem, not a model score - nothing was saved.")
        print(f"{'PASS' if passed else 'FAIL'}  {q['id']}  {q['split']:4s} {q['category']:12s} {latency:4.1f}s  {why if not passed else ''}", flush=True)

    # ------------------------------------------------------------------ report
    n = len(results)
    ok = sum(r["passed"] for r in results)
    lat = np.array([r["latency_s"] for r in results])
    cost = sum(r["cost_usd"] for r in results)
    retried = sum((r["attempts"] or 1) > 1 for r in results)
    false_ref = sum(r["why"].startswith("false refusal") for r in results)

    lines = [f"### {args.label}  ({MODEL}, prompt {prompt_hash})", "",
             "| metric | value |", "|---|---|",
             f"| accuracy (all {n}) | **{ok}/{n} = {pct(ok, n)}** |"]
    for split in ("dev", "test"):
        rs = [r for r in results if r["split"] == split]
        if rs:
            lines.append(f"| accuracy ({split}, {len(rs)} questions) | {sum(r['passed'] for r in rs)}/{len(rs)} = {pct(sum(r['passed'] for r in rs), len(rs))} |")
    lines += [f"| latency p50 / p95 | {np.percentile(lat, 50):.1f}s / {np.percentile(lat, 95):.1f}s |",
              f"| cost per question (avg) | ${cost / n:.4f} |",
              f"| total cost of this run | ${cost:.3f} |",
              f"| needed a retry | {retried}/{n} |",
              f"| false refusals by the guard | {false_ref}/{n} |", "",
              "| category | passed |", "|---|---|"]
    by_cat = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r["passed"])
    for cat, v in by_cat.items():
        lines.append(f"| {cat} | {sum(v)}/{len(v)} |")
    report = "\n".join(lines)
    print("\n" + report + "\n")

    # ------------------------------------------------------------------ failures
    fails = [r for r in results if not r["passed"]]
    for r in fails:
        if r["split"] == "test" and args.hide_test_failures:
            print(f"(details hidden for held-out failure {r['id']})")
            continue
        print(f"--- {r['id']} [{r['split']}/{r['category']}] {r['question']}")
        print(f"    why     : {r['why']}")
        print(f"    sql     : {r['sql']}")
        print(f"    got     : {r['got']}")
        print(f"    expected: {r['expected']}\n")

    RESULTS.mkdir(exist_ok=True)
    meta = {"label": args.label, "model": MODEL, "prompt_hash": prompt_hash, "n": n,
            "when": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (RESULTS / f"{args.label}.json").write_text(json.dumps({"meta": meta, "results": results}, indent=2, default=str))
    (RESULTS / f"{args.label}.md").write_text(report + "\n")
    print(f"saved evals/results/{args.label}.json")


if __name__ == "__main__":
    main()

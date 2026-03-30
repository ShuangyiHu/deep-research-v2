"""
eval_baseline.py
────────────────
Baseline evaluation harness for the Deep Research pipeline.

Runs a fixed test suite of queries, captures per-run metrics, and writes
results to JSON + a human-readable markdown summary.

Run BEFORE any refactoring. Re-run AFTER. Compare the two outputs.

Usage:
    python tests/eval_baseline.py                    # full suite
    python tests/eval_baseline.py --tier vague       # one tier only
    python tests/eval_baseline.py --query "AI jobs"  # single ad-hoc query

Output files (auto-named with timestamp):
    eval_results/baseline_YYYYMMDD_HHMMSS.json
    eval_results/baseline_YYYYMMDD_HHMMSS.md

── Why the previous version broke ───────────────────────────────────────────
The old version used module-level monkey-patching:
    _writer_mod.draft_report = _patched_draft

This fails because pipeline.py uses `from deep_research.core.writer import draft_report`
which binds the name in pipeline's own namespace at import time. Patching the
module attribute afterwards has no effect on that already-bound name.

Fix: instrument via the on_progress log instead of patching functions.
The pipeline emits structured messages that carry all the signals we need.
No monkey-patching, no import-order issues, no function-signature coupling.
"""
from dotenv import load_dotenv
load_dotenv()

import argparse
import asyncio
import json
import re
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

logging.basicConfig(level=logging.WARNING)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("openai").setLevel(logging.ERROR)
logging.getLogger("anthropic").setLevel(logging.ERROR)

from deep_research.core.pipeline import run_pipeline_async

# ── Test suite ────────────────────────────────────────────────────────────────

TEST_QUERIES: dict[str, list[str]] = {
    "vague": [
        "AI coding jobs",
        "GLP-1 drugs market",
    ],
    "medium": [
        "How will autonomous vehicles affect delivery jobs?",
        "Impact of remote work on commercial real estate",
    ],
    "detailed": [
        (
            "Between 2025 and 2030, how are AI-assisted coding tools expected to shift "
            "hiring demand for junior software engineers in North America, and which "
            "technical or workflow skills are projected to remain complementary to "
            "AI-driven software development?"
        ),
        (
            "How is the rapid expansion of GLP-1 weight-loss drugs expected to reshape "
            "the US healthcare industry and consumer spending patterns through 2027?"
        ),
    ],
}


# ── Signal extractor ──────────────────────────────────────────────────────────

class PipelineSignals:
    """
    Accumulates metrics by parsing structured on_progress log messages.

    The pipeline already emits messages like:
        "  Claude: 7  Gemini: 6  Avg: 6"
        "  Score: 6 | Weak: ['Introduction', 'Market Size']"
        "─── Analyst Agent: 4 docs out (avg relevance: 0.612) ───"
        "  Dedup: removed 2 docs (1 exact, 1 near-duplicate) → 4 remaining"
        "── Iteration 2/4 ──────────────────"
        "Query Rewriter: expanding short query (3 words)..."
        "  Rewritten: How are AI-assisted coding tools..."

    Parsing progress messages is more robust than monkey-patching because:
    - No import-order sensitivity (the old bug)
    - No function signature coupling
    - Works regardless of pipeline refactors as long as log messages are stable
    """

    def __init__(self) -> None:
        self.log_lines: list[str] = []
        self.scores: list[int] = []
        self.claude_scores: list[int] = []
        self.gemini_scores: list[int] = []
        self.weak_section_counts: list[int] = []
        self.needs_more_search_fires: int = 0
        self.iterations_seen: int = 0
        self.analyst_docs_out: int = 0
        self.analyst_avg_relevance: float = 0.0
        self.dedup_removed: int = 0
        self.query_was_expanded: bool = False
        self.rewritten_query: str = ""

    def ingest(self, msg: str) -> None:
        self.log_lines.append(msg)

        # Iteration marker
        if re.match(r"── Iteration \d+/\d+", msg):
            self.iterations_seen += 1

        # Evaluator scores: "  Claude: 7  Gemini: 6  Avg: 6" or "...min: 5"
        m = re.search(r"Claude:\s*(\d+)\s+Gemini:\s*(\d+)", msg)
        if m:
            self.claude_scores.append(int(m.group(1)))
            self.gemini_scores.append(int(m.group(2)))

        # Consensus score line: "  Score: 6 | Weak: [...]"
        m = re.search(r"Score:\s*(\d+)\s*\|", msg)
        if m:
            self.scores.append(int(m.group(1)))

        # Weak sections list
        m = re.search(r"Weak:\s*(\[.*?\])", msg)
        if m:
            try:
                weak_list = json.loads(m.group(1).replace("'", '"'))
                self.weak_section_counts.append(len(weak_list))
            except Exception:
                pass

        # Targeted search fired (needs_more_search was True)
        if re.search(r"Running \d+ targeted searches", msg):
            self.needs_more_search_fires += 1

        # Analyst Agent output stats
        m = re.search(r"Analyst Agent:\s*(\d+) docs out \(avg relevance: ([\d.]+)\)", msg)
        if m:
            # Take the first occurrence (initial evidence pass)
            if self.analyst_docs_out == 0:
                self.analyst_docs_out = int(m.group(1))
                self.analyst_avg_relevance = float(m.group(2))

        # Dedup removed (accumulate across all Analyst calls)
        m = re.search(r"Dedup: removed (\d+) docs", msg)
        if m:
            self.dedup_removed += int(m.group(1))

        # Query rewriter
        if "expanding short query" in msg:
            self.query_was_expanded = True
        m = re.search(r"Rewritten:\s*(.+)", msg)
        if m:
            self.rewritten_query = m.group(1).strip()

    def to_metrics(self) -> dict:
        gaps = [abs(c - g) for c, g in zip(self.claude_scores, self.gemini_scores)]
        return {
            "iterations_done": self.iterations_seen,
            "scores_per_iter": self.scores,
            "final_score": self.scores[-1] if self.scores else None,
            "initial_score": self.scores[0] if self.scores else None,
            "score_delta": (
                (self.scores[-1] - self.scores[0]) if len(self.scores) >= 2 else 0
            ),
            "avg_claude_gemini_gap": (
                round(sum(gaps) / len(gaps), 2) if gaps else None
            ),
            "max_claude_gemini_gap": max(gaps) if gaps else None,
            "needs_more_search_count": self.needs_more_search_fires,
            "avg_weak_sections": (
                round(sum(self.weak_section_counts) / len(self.weak_section_counts), 1)
                if self.weak_section_counts else None
            ),
            "analyst_docs_out": self.analyst_docs_out,
            "analyst_avg_relevance": self.analyst_avg_relevance,
            "dedup_removed_total": self.dedup_removed,
            "query_was_expanded": self.query_was_expanded,
            "rewritten_query": self.rewritten_query or None,
        }


# ── Single-run harness ────────────────────────────────────────────────────────

async def _run_one(
    query: str,
    tier: str,
    on_progress: Callable[[str], None],
) -> dict:
    signals = PipelineSignals()

    def _instrumented(msg: str) -> None:
        signals.ingest(msg)
        on_progress(msg)

    t0 = time.time()
    final_report = ""
    success = True
    error_msg = ""

    try:
        final_report = await run_pipeline_async(query=query, on_progress=_instrumented)
    except Exception as exc:
        success = False
        error_msg = str(exc)
    finally:
        elapsed = round(time.time() - t0, 1)

    metrics = {
        "query": query,
        "tier": tier,
        "timestamp": datetime.utcnow().isoformat(),
        "success": success,
        "elapsed_seconds": elapsed,
        "report_word_count": len(final_report.split()) if final_report else 0,
        "log": signals.log_lines,
    }
    if error_msg:
        metrics["error"] = error_msg

    metrics.update(signals.to_metrics())
    return metrics


# ── Summary builder ───────────────────────────────────────────────────────────

def _build_markdown_summary(all_results: list[dict], label: str) -> str:
    lines = [
        "# Deep Research — Eval Report",
        f"**Run label:** `{label}`  ",
        f"**Generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}  ",
        f"**Queries:** {len(all_results)}",
        "",
        "## Metrics Table",
        "",
        "| Tier | Query | Score | Δ | Iters | Dedup removed | Avg relevance | Gap avg | Time (s) |",
        "|------|-------|-------|---|-------|---------------|---------------|---------|----------|",
    ]

    for r in all_results:
        q_short = r["query"][:45].replace("|", "/") + ("…" if len(r["query"]) > 45 else "")
        score   = r.get("final_score") or "—"
        delta   = r.get("score_delta", 0)
        delta_s = f"+{delta}" if delta > 0 else (str(delta) if delta else "0")
        iters   = r.get("iterations_done", "—")
        dedup   = r.get("dedup_removed_total", "—")
        rel     = r.get("analyst_avg_relevance") or "—"
        gap     = r.get("avg_claude_gemini_gap") or "—"
        t       = r.get("elapsed_seconds", "—")
        marker  = " ✎" if r.get("query_was_expanded") else ""
        lines.append(
            f"| {r['tier']} | {q_short}{marker} | {score} | {delta_s} "
            f"| {iters} | {dedup} | {rel} | {gap} | {t} |"
        )

    lines += ["", "_✎ = query was expanded by QueryRewriter_", ""]

    lines += ["## Per-Tier Averages", ""]
    for tier in ["vague", "medium", "detailed"]:
        tr = [r for r in all_results if r["tier"] == tier]
        if not tr:
            continue
        avg_score = round(sum(r.get("final_score") or 0 for r in tr) / len(tr), 1)
        avg_iters = round(sum(r.get("iterations_done") or 0 for r in tr) / len(tr), 1)
        avg_dedup = round(sum(r.get("dedup_removed_total") or 0 for r in tr) / len(tr), 1)
        avg_rel   = round(sum(r.get("analyst_avg_relevance") or 0 for r in tr) / len(tr), 3)
        pct_exp   = round(sum(1 for r in tr if r.get("query_was_expanded")) / len(tr) * 100)
        lines.append(
            f"**{tier}** — score: `{avg_score}` · iters: `{avg_iters}` · "
            f"dedup removed: `{avg_dedup}` · avg relevance: `{avg_rel}` · "
            f"queries expanded: `{pct_exp}%`  "
        )

    lines += [
        "",
        "## Interpretation Guide",
        "",
        "- `vague` score much lower than `detailed` → QueryRewriter has room to help",
        "- `dedup_removed` > 0 → AnalystAgent is removing real noise",
        "- Low `analyst_avg_relevance` → search plan needs broader query diversity",
        "- High `gap avg` → evaluators disagree; report has ambiguous or unverifiable claims",
        "- High `iters` even on `detailed` → WriterAgent first-draft quality is the bottleneck",
        "",
        "---",
        "*Re-run after each major change and compare label-to-label in eval_results/.*",
    ]
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main(tier_filter: str | None, single_query: str | None) -> None:
    out_dir = Path("eval_results")
    out_dir.mkdir(exist_ok=True)
    label = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    if single_query:
        queries = [("ad_hoc", single_query)]
    else:
        queries = [
            (tier, q)
            for tier, qs in TEST_QUERIES.items()
            if tier_filter is None or tier == tier_filter
            for q in qs
        ]

    print(f"\n{'='*60}")
    print(f"  Deep Research Eval — {len(queries)} queries  [{label}]")
    print(f"{'='*60}\n")

    all_results: list[dict] = []

    for i, (tier, query) in enumerate(queries, 1):
        print(f"\n[{i}/{len(queries)}] tier={tier}")
        print(f"  Query: {query[:80]}{'…' if len(query) > 80 else ''}")
        print("-" * 50)

        result = await _run_one(
            query=query,
            tier=tier,
            on_progress=lambda msg: print(f"  {msg}"),
        )
        all_results.append(result)

        print(
            f"\n  ✓ score={result.get('final_score')}  "
            f"iters={result.get('iterations_done')}  "
            f"dedup={result.get('dedup_removed_total')}  "
            f"expanded={result.get('query_was_expanded')}  "
            f"time={result.get('elapsed_seconds')}s"
        )

    json_path = out_dir / f"baseline_{label}.json"
    md_path   = out_dir / f"baseline_{label}.md"

    with open(json_path, "w", encoding="utf-8") as f:
        slim = [{k: v for k, v in r.items() if k != "log"} for r in all_results]
        json.dump(slim, f, indent=2, ensure_ascii=False)

    md_path.write_text(_build_markdown_summary(all_results, label), encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"  Saved:")
    print(f"    {json_path}")
    print(f"    {md_path}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deep Research eval harness")
    parser.add_argument("--tier", choices=["vague", "medium", "detailed"], default=None)
    parser.add_argument("--query", default=None)
    args = parser.parse_args()
    asyncio.run(main(tier_filter=args.tier, single_query=args.query))
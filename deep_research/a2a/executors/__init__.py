"""
a2a/executors/
──────────────
AgentExecutor subclasses — one per A2A-ified agent.

Each executor wraps the existing agent logic from deep_research/core/:
  analyst.py   → wraps analysis.analyse()
  search.py    → wraps planner.run_single_search() via MCP client
  writer.py    → wraps writer.draft_report()
  evaluator.py → wraps evaluator.consensus_evaluation()
                 + agent-initiated targeted search

Executors are populated in Phase B–F. This package is importable from Phase A.
"""

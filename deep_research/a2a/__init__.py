"""
deep_research.a2a
─────────────────
Agent2Agent (A2A) protocol layer.

Structure:
  schemas.py      — Pydantic models for A2A DataPart input/output payloads
  invocation.py   — Unified call_agent() contract used by pipeline
  server.py       — Mounts A2A agent servers on the FastAPI app
  clients.py      — Typed convenience wrappers around call_agent()
  executors/      — AgentExecutor subclasses (one per A2A-ified agent)

Agents exposed as A2A servers (selected by cost / reusability):
  search    → SearchExecutor     (web search via MCP)
  analyst   → AnalystExecutor    (dedup + relevance scoring)
  writer    → WriterExecutor     (LLM report drafting)
  evaluator → EvaluatorExecutor  (dual-model consensus + agent-initiated search)

Agents kept local (lightweight / context-dependent):
  QueryRewriter, Planner, Rewriter, Structure
"""

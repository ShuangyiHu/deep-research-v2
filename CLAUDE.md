# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Setup:**
```bash
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env  # then fill in API keys
```

**Run the application (3 separate processes):**
```bash
# Terminal 1: FastAPI server (also hosts all A2A agent servers on /a2a/<name>)
uvicorn deep_research.main:app --reload --port 8000

# Terminal 2: Celery worker
celery -A deep_research.worker.celery_app worker --loglevel=info

# Terminal 3 (optional): Flower monitoring
celery -A deep_research.worker.celery_app flower --port=5555
```

**Tests:**
```bash
pytest              # run all unit tests
pytest -v           # verbose
pytest -m e2e       # run end-to-end tests (costs money, requires real API keys)
pytest tests/test_pipeline.py  # run a single test file
```

**Docker builds:**
```bash
docker build -f Dockerfile.api -t deep-research-api:latest .
docker build -f Dockerfile.worker -t deep-research-worker:latest .
```

## Architecture

This is a multi-model AI research pipeline that generates polished research reports iteratively. It uses a FastAPI + Celery + Redis architecture for user-facing async tasks, and an A2A (Agent2Agent) + MCP (Model Context Protocol) mesh for inter-agent communication.

**Layering:**
- **Celery** = user-facing async task orchestration (HTTP 202 + background job)
- **A2A** = inter-agent communication protocol (4 agents exposed as HTTP services)
- **MCP** = tool abstraction inside SearchExecutor (web search provider)

### Infrastructure

- **FastAPI** accepts requests, immediately returns a `job_id` (HTTP 202), and stores results in Redis
- **Celery worker** executes the full pipeline asynchronously
- **Gradio UI** is mounted directly on the FastAPI app and polls `/api/v1/status/{job_id}` every 2.5s
- **Redis** serves as both the Celery broker and the result backend — no database needed
- **A2A servers** for 4 agents are mounted on the same FastAPI instance at `/a2a/<name>/`

### Pipeline Flow (`deep_research/core/pipeline.py`)

```
[Phase 1 — Drafting]
  QueryRewriterAgent  → rewrite_query()     (local)    expand vague queries
  PlannerAgent        → plan_searches()     (local)    generate N typed search items
  SearchAgent ×N      → search_execute()    (A2A)      parallel web searches via MCP
    └─ MCP search_client → search_server → OpenAI WebSearchTool
  AnalystAgent        → analyst_analyse()   (A2A)      deduplicate + TF-IDF score
  WriterAgent         → writer_draft()      (A2A)      draft initial markdown report

[Phase 2 — Iterative Refinement, up to max_iter]
  EvaluatorAgent      → evaluator_evaluate() (A2A)     Claude + Gemini consensus score
    └─ agent-initiated: if needs_more_search + budget > 0,
       EvaluatorExecutor autonomously calls Search A2A + Analyst A2A
       and returns collected_evidence in the feedback response
  RewriteAgent + StructureAgent → rewrite_sections() (local)
  [Regression check → rollback to best-seen report if score drops]

Final report appended with "Report Quality Assessment" section
```

**Which agents are A2A vs local:**

| Agent | Mode | Reason |
|-------|------|--------|
| QueryRewriterAgent | local | Single cheap call, pure utility |
| PlannerAgent | local | Tightly coupled to pipeline N logic |
| SearchAgent | **A2A** | Swappable backend (Tavily/Brave/custom via MCP); parallelism |
| AnalystAgent | **A2A** | No LLM, reusable CPU logic; different scaling needs |
| WriterAgent | **A2A** | Heaviest LLM call; candidate for larger model swap |
| EvaluatorAgent | **A2A** | Dual-model parallel; owns agent-initiated search decisions |
| RewriteAgent | local | Needs full context each iteration |
| StructureAgent | local | Tight coupling to Rewrite pass |

### A2A Architecture

All 4 A2A servers share the same FastAPI process (mounted via `app.mount`):
- `GET /a2a/search/.well-known/agent-card.json` — AgentCard discovery
- `POST /a2a/search/` — JSON-RPC execute
- Same pattern for `/a2a/analyst/`, `/a2a/writer/`, `/a2a/evaluator/`

**Unified invocation contract** (`deep_research/a2a/invocation.py`):
```python
await call_agent("analyst", "analyse", payload)  # same interface for all agents
```

**Agent URL override** (env vars): `A2A_SEARCH_URL`, `A2A_ANALYST_URL`, `A2A_WRITER_URL`, `A2A_EVALUATOR_URL` — changing one URL moves that agent to a separate container with zero code changes.

### Agent-Initiated Search (EvaluatorExecutor)

The Evaluator is the only agent with "collaborative" behavior. When `needs_more_search=True` and `search_budget_remaining > 0` (passed in from pipeline), `EvaluatorExecutor` autonomously calls Search A2A + Analyst A2A before returning. The pipeline receives `collected_evidence` and `budget_consumed` in the feedback dict and merges the new docs — it does **not** inspect `needs_more_search` to decide whether to search.

Failure isolation: if the internal Search/Analyst calls fail, the executor degrades gracefully (returns `collected_evidence=None`, `budget_consumed=0`) so the pipeline continues normally.

### Agent Model Assignments

| Agent | Model |
|-------|-------|
| QueryRewriterAgent | gpt-4o-mini |
| PlannerAgent | gpt-4o-mini |
| SearchAgent (via MCP) | gpt-4o-mini + OpenAI WebSearchTool |
| AnalystAgent | No LLM (TF-IDF + Jaccard similarity) |
| WriterAgent | gpt-4o-mini |
| EvaluatorAgent | claude-sonnet-4-6 + gemini-2.0-flash (parallel) |
| RewriteAgent | gpt-4o |
| StructureAgent | gpt-4o-mini |

### Key Design Decisions

- **Dual-model evaluation**: Claude and Gemini score independently; the *lower* score is used as the consensus when disagreement ≥ gap threshold. Quality threshold defaults to 8/10.
- **Regression rollback**: if a rewrite causes the score to drop, the pipeline reverts to the best-seen report (re-eval with budget=0).
- **`SearchDocumentCollection`** carries metadata (query provenance, relevance scores) throughout the pipeline rather than plain strings, enabling deduplication across agents. Wire transport uses Pydantic `SearchDocumentData` (in `a2a/schemas.py`), bridged via `to_dict()`/`from_dict()`.
- **Analyst is LLM-free**: deduplication (Jaccard similarity) and scoring (TF-IDF) are deterministic.
- **MCP in-process mode** (default): `MCP_SEARCH_MODE=in-process` calls `_web_search_impl` directly without a network hop. Set to `http` when splitting SearchExecutor into a separate container.

### Key Files

| File | Role |
|------|------|
| `deep_research/core/pipeline.py` | Top-level orchestration; calls all agents in sequence |
| `deep_research/core/config.py` | Pydantic Settings (reads `.env`); CANONICAL_SECTIONS; tunable parameters |
| `deep_research/a2a/invocation.py` | `call_agent(name, skill, payload)` — unified A2A invocation contract |
| `deep_research/a2a/clients.py` | Typed wrappers: `search_execute`, `analyst_analyse`, `writer_draft`, `evaluator_evaluate` |
| `deep_research/a2a/schemas.py` | Pydantic wire models for A2A DataPart payloads |
| `deep_research/a2a/server.py` | Mounts A2A sub-apps on FastAPI via `register_a2a_apps()` |
| `deep_research/a2a/executors/` | One executor per A2A agent (analyst, search, writer, evaluator) |
| `deep_research/mcp/search_server.py` | MCP server exposing `web_search` tool |
| `deep_research/mcp/search_client.py` | MCP client used by SearchExecutor |
| `deep_research/core/search_documents.py` | `SearchDocument` and `SearchDocumentCollection` data models |
| `deep_research/worker/tasks.py` | Celery task entry point (`run_research_task`) |
| `deep_research/api/routes.py` | HTTP endpoints: POST /generate, GET /status/{job_id}, DELETE /cancel/{job_id} |
| `deep_research/ui/app.py` | Gradio frontend, polls API via httpx |

### Environment Variables

Required API keys: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`  
Optional: `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL`, `SENDGRID_FROM_NAME`

A2A configuration (all have defaults pointing to `localhost:8000`):
- `A2A_BASE_URL` (default: `http://localhost:8000/a2a`)
- `A2A_SEARCH_URL`, `A2A_ANALYST_URL`, `A2A_WRITER_URL`, `A2A_EVALUATOR_URL` — override individual agent URLs for split-container deployment

MCP configuration:
- `MCP_SEARCH_MODE` (default: `in-process`) — use `http` for standalone MCP server

Pipeline tuning (all have defaults):
- `PIPELINE_QUALITY_THRESHOLD` (default: 8) — minimum consensus score to stop iterating
- `PIPELINE_MAX_ITERATIONS` (default: 4)
- `PIPELINE_HOW_MANY_SEARCHES` (default: 5)
- `PIPELINE_MAX_TARGETED_SEARCHES` (default: 2)
- `REDIS_URL` (default: `redis://localhost:6379/0`)

### Redis Setup

```bash
# macOS
brew install redis && brew services start redis

# Docker
docker run -d -p 6379:6379 redis:alpine
```

### Testing Notes

- Test fixtures (sample reports, feedback objects) are in `tests/conftest.py`
- `pytest.ini` sets `asyncio_mode=auto` — async tests work without explicit markers
- E2E tests (`@pytest.mark.e2e`) hit real APIs and cost money; they're excluded from the default run
- `tests/eval_baseline.py` can be used for manual evaluation baseline comparisons
- A2A executor tests use `patch("deep_research.a2a.clients.<fn>")` not the executor module, because executors use lazy imports

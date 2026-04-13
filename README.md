# Deep Research — v2

Multi-model AI research pipeline with a Gradio UI, FastAPI backend, Celery task queue, A2A inter-agent protocol, and MCP tool abstraction.

```
Query → Plan → Search (A2A+MCP) → Analyst (A2A) → Writer (A2A) → Evaluator (A2A, agent-initiated) → Rewrite → repeat
```

---

## Architecture

Three protocol layers, each with a distinct scope:

| Layer | Technology | Scope |
|-------|-----------|-------|
| User async tasks | FastAPI + Celery + Redis | HTTP 202 → background job → poll result |
| Inter-agent communication | A2A (Agent2Agent) | 4 agents as HTTP services on `/a2a/<name>/` |
| Tool abstraction | MCP (Model Context Protocol) | Web search provider inside SearchExecutor |

### Pipeline flow

```
[Phase 1 — Drafting]
  QueryRewriter  (local)  expand vague queries
  Planner        (local)  generate N typed search items
  Search ×N      (A2A)    parallel web searches via MCP → OpenAI WebSearchTool
  Analyst        (A2A)    deduplicate + TF-IDF relevance score (no LLM)
  Writer         (A2A)    draft initial markdown report

[Phase 2 — Iterative Refinement, up to max_iter]
  Evaluator      (A2A)    Claude + Gemini consensus score
    └─ agent-initiated: if evidence gap detected + budget > 0,
       EvaluatorExecutor autonomously calls Search A2A + Analyst A2A
       and returns collected_evidence to the pipeline
  Rewriter       (local)  improve weak sections
  Structurer     (local)  fix flow and transitions
  [Regression rollback if score drops]

Final report + "Report Quality Assessment" quality section appended
```

### A2A agents

All 4 A2A servers run in the same FastAPI process (split-container ready via env var URL override):

| Agent | Endpoint | What it does |
|-------|----------|-------------|
| Search | `/a2a/search/` | Fan-out batch web searches via MCP |
| Analyst | `/a2a/analyst/` | Jaccard dedup + TF-IDF scoring (LLM-free) |
| Writer | `/a2a/writer/` | Draft 1000+ word markdown report (gpt-4o-mini) |
| Evaluator | `/a2a/evaluator/` | Claude + Gemini parallel scoring + agent-initiated supplemental search |

AgentCard discovery: `GET /a2a/<name>/.well-known/agent-card.json`

---

## Project structure

```
deep_research/
├── core/
│   ├── pipeline.py         # Top-level orchestration
│   ├── config.py           # All settings (pydantic-settings) + constants
│   ├── planner.py          # PlannerAgent (local) + search data models
│   ├── writer.py           # WriterAgent (also used by WriterExecutor)
│   ├── evaluator.py        # consensus_evaluation() (used by EvaluatorExecutor)
│   ├── rewriter.py         # RewriteAgent + StructureAgent (local)
│   ├── analysis.py         # TF-IDF + Jaccard (used by AnalystExecutor)
│   ├── query_rewriter.py   # QueryRewriterAgent (local)
│   ├── search_documents.py # SearchDocument / SearchDocumentCollection
│   └── clients.py          # Claude / Gemini / OpenAI API singletons
├── a2a/
│   ├── invocation.py       # call_agent(name, skill, payload) — unified contract
│   ├── clients.py          # Typed wrappers: search_execute, analyst_analyse, writer_draft, evaluator_evaluate
│   ├── schemas.py          # Pydantic wire models (SearchInput/Output, EvaluateInput/Output, …)
│   ├── server.py           # register_a2a_apps() — mounts sub-apps on FastAPI
│   └── executors/
│       ├── search.py       # SearchExecutor (MCP fanout)
│       ├── analyst.py      # AnalystExecutor (TF-IDF, no LLM)
│       ├── writer.py       # WriterExecutor (gpt-4o-mini)
│       └── evaluator.py    # EvaluatorExecutor (Claude+Gemini + agent-initiated search)
├── mcp/
│   ├── search_server.py    # MCP server: web_search tool
│   └── search_client.py    # MCP client (in-process default, HTTP optional)
├── worker/
│   ├── celery_app.py       # Celery app factory
│   └── tasks.py            # run_research_task Celery task
├── api/
│   ├── schemas.py          # Pydantic request/response models
│   └── routes.py           # FastAPI router
├── ui/
│   └── app.py              # Gradio frontend
├── services/
│   └── email_service.py    # SendGrid delivery
└── main.py                 # FastAPI app factory
```

---

## Setup

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Fill in your API keys in .env
```

Required keys:

| Key | Where to get it |
|-----|-----------------|
| `OPENAI_API_KEY` | platform.openai.com |
| `ANTHROPIC_API_KEY` | console.anthropic.com |
| `GOOGLE_API_KEY` | aistudio.google.com |
| `SENDGRID_API_KEY` | app.sendgrid.com (optional) |

### 3. Start Redis

```bash
# macOS
brew install redis && brew services start redis

# Docker (any platform)
docker run -d -p 6379:6379 redis:alpine
```

---

## Running the app

Three processes, three terminal tabs:

### Tab 1 — FastAPI + Gradio + A2A servers

```bash
uvicorn deep_research.main:app --reload --port 8000
```

- Gradio UI: http://localhost:8000
- Swagger docs: http://localhost:8000/docs
- A2A discovery: http://localhost:8000/a2a/evaluator/.well-known/agent-card.json

### Tab 2 — Celery worker

```bash
celery -A deep_research.worker.celery_app worker --loglevel=info
```

### Tab 3 — (Optional) Celery monitoring

```bash
celery -A deep_research.worker.celery_app flower --port=5555
```

---

## How it works

```
Browser → POST /api/v1/generate
              ↓
          FastAPI pushes task to Redis → returns job_id (202)
              ↓
Gradio polls GET /api/v1/status/{job_id} every 2.5s
              ↓
          Celery worker runs run_pipeline_async():
          ├── QueryRewriter + Planner    (local)
          ├── search_execute()           → Search A2A → MCP web_search
          ├── analyst_analyse()          → Analyst A2A
          ├── writer_draft()             → Writer A2A
          └── iterative loop:
              ├── evaluator_evaluate()   → Evaluator A2A
              │     └─ if evidence gap: autonomously calls Search + Analyst A2A
              ├── rewrite_sections()     (local)
              └── regression rollback if score drops
              ↓
          Result stored in Redis
              ↓
Gradio renders report  →  (optional) SendGrid emails report
```

---

## API reference

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/generate` | Queue a research task |
| `GET` | `/api/v1/status/{job_id}` | Poll task state + progress log |
| `DELETE` | `/api/v1/cancel/{job_id}` | Cancel a running task |
| `GET` | `/health` | Health check |
| `GET` | `/a2a/<name>/.well-known/agent-card.json` | A2A AgentCard discovery |

### POST /api/v1/generate

```json
{
  "query": "How are AI coding tools reshaping junior developer hiring by 2030?",
  "email": "you@example.com",
  "threshold": 8,
  "max_iter": 4
}
```

Response `202 Accepted`:
```json
{ "job_id": "abc-123", "message": "Research task queued." }
```

### GET /api/v1/status/{job_id}

```json
{
  "job_id": "abc-123",
  "state": "PROGRESS",
  "log": ["Planning searches…", "→ 5 searches planned", "Running searches in parallel…"],
  "report": null,
  "email_sent": null,
  "error": null
}
```

States: `PENDING` → `PROGRESS` → `SUCCESS` | `FAILURE` | `REVOKED`

---

## Configuration

Key environment variables (all have defaults — see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `PIPELINE_QUALITY_THRESHOLD` | `8` | Min score (1–10) to stop iterating |
| `PIPELINE_MAX_ITERATIONS` | `4` | Max refinement rounds |
| `PIPELINE_HOW_MANY_SEARCHES` | `5` | Initial search count |
| `PIPELINE_MAX_TARGETED_SEARCHES` | `2` | Budget for agent-initiated supplemental searches |
| `A2A_BASE_URL` | `http://localhost:8000/a2a` | Base URL for A2A agents |
| `A2A_SEARCH_URL` | — | Override Search agent URL (split-container) |
| `A2A_ANALYST_URL` | — | Override Analyst agent URL |
| `A2A_WRITER_URL` | — | Override Writer agent URL |
| `A2A_EVALUATOR_URL` | — | Override Evaluator agent URL |
| `MCP_SEARCH_MODE` | `in-process` | `in-process` or `http` for standalone MCP server |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker + result backend |

# Deep Research — v2

Multi-model AI research pipeline with a Gradio UI, FastAPI backend, and Celery task queue.

```
Query → GPT-4o-mini drafts → Claude + Gemini evaluate → GPT-4o rewrites → repeat → SendGrid email
```

---

## Project structure

```
deep_research/
├── core/
│   ├── config.py       # All settings (pydantic-settings) + constants
│   ├── clients.py      # Claude / Gemini / OpenAI singletons
│   ├── utils.py        # @with_retry decorator + safe_extract_json
│   ├── planner.py      # PlannerAgent + SearchAgent
│   ├── writer.py       # WriterAgent + draft_report()
│   ├── evaluator.py    # Claude + Gemini consensus evaluation
│   ├── rewriter.py     # RewriteAgent + StructureAgent
│   └── pipeline.py     # run_pipeline_async() / run_pipeline()
├── services/
│   └── email_service.py   # SendGrid delivery
├── worker/
│   ├── celery_app.py   # Celery app factory
│   └── tasks.py        # run_research_task Celery task
├── api/
│   ├── schemas.py      # Pydantic request/response models
│   └── routes.py       # FastAPI router (POST /generate, GET /status, DELETE /cancel)
├── ui/
│   └── app.py          # Gradio frontend
├── main.py             # FastAPI app factory (mounts Gradio + API)
├── requirements.txt
└── .env.example
```

---

## Project layout explained

```
project_root/               ← unzip here, run ALL commands from here
├── deep_research/          ← Python package (never cd into this)
│   ├── core/
│   ├── api/
│   ├── ui/
│   ├── worker/
│   ├── services/
│   └── main.py
├── tests/                  ← test files live outside the package
├── requirements.txt
├── pytest.ini
└── .env
```

`deep_research/` is a Python *package* — it must be visible from the
project root so that `from deep_research.core.config import settings` resolves.
Never `cd` into `deep_research/` to run commands.

## Setup

### 1. Install dependencies

```bash
# From project_root/
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

# Linux
sudo apt install redis-server && sudo systemctl start redis

# Docker (any platform)
docker run -d -p 6379:6379 redis:alpine
```

---

## Running the app

You need **three** processes running simultaneously. Open three terminal tabs:

### Tab 1 — FastAPI server + Gradio UI

```bash
# From project_root/ — deep_research is a package visible from here
uvicorn deep_research.main:app --reload --port 8000
```

- Gradio UI: http://localhost:8000
- Swagger docs: http://localhost:8000/docs

### Tab 2 — Celery worker

```bash
# From project_root/
celery -A deep_research.worker.celery_app worker --loglevel=info
```

### Tab 3 — (Optional) Celery monitoring dashboard

```bash
pip install flower
# From project_root/
celery -A deep_research.worker.celery_app flower --port=5555
```

---

## How it works

```
Browser → POST /api/v1/generate
              ↓
          FastAPI pushes task to Redis
              ↓
          Returns job_id immediately (202)
              ↓
Gradio polls GET /api/v1/status/{job_id} every 2.5s
              ↓
          Celery worker picks up task
          ├── draft_report()          GPT-4o-mini
          ├── consensus_evaluation()  Claude + Gemini (parallel)
          ├── targeted_search()       GPT-4o-mini (if needed)
          ├── rewrite_sections()      GPT-4o
          └── repeat up to max_iter
              ↓
          Result stored in Redis
              ↓
Gradio receives SUCCESS state → renders report
              ↓
(Optional) SendGrid emails report to user
```

---

## API reference

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/generate` | Queue a research task |
| `GET` | `/api/v1/status/{job_id}` | Poll task state + progress log |
| `DELETE` | `/api/v1/cancel/{job_id}` | Cancel a running task |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Swagger UI |

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

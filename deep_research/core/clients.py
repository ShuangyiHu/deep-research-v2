"""
clients.py
──────────
Singleton API clients shared across all agents.
 
Why singletons:
    Each agent module imports the client it needs directly from here.
    Constructing clients once at import time avoids per-call auth overhead
    and makes test-time monkey-patching straightforward.
 
Client → Agent mapping:
    claude_client   → EvaluatorAgent (consensus_evaluation in evaluator.py)
    gemini_client   → EvaluatorAgent (consensus_evaluation in evaluator.py)
    openai_client   → PlannerAgent, SearchAgent, WriterAgent, RewriteAgent,
                      StructureAgent, QueryRewriterAgent
                      (via openai-agents SDK Runner — reads OPENAI_API_KEY from env)
 
Note on openai_client:
    The openai-agents SDK (Runner / Agent) reads OPENAI_API_KEY from the
    environment automatically. The explicit openai_client here is available
    for any direct OpenAI calls outside the Agents SDK if needed.
"""

import anthropic
from openai import AsyncOpenAI

from deep_research.core.config import settings


# ── Claude (sync) ──────────────────────────────────────────────────────────────
# Used synchronously inside the evaluator (called via asyncio.to_thread).
claude_client: anthropic.Anthropic = anthropic.Anthropic(
    api_key=settings.anthropic_api_key
)

# ── Gemini via OpenAI-compatible endpoint (async) ──────────────────────────────
gemini_client: AsyncOpenAI = AsyncOpenAI(
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    api_key=settings.google_api_key,
)

# ── OpenAI (for the Agents SDK — uses OPENAI_API_KEY from env automatically) ──
# The openai-agents SDK reads OPENAI_API_KEY from the environment, so no
# explicit client construction is needed for Runner / Agent usage.
# We export a plain AsyncOpenAI client for any direct OpenAI calls if needed.
openai_client: AsyncOpenAI = AsyncOpenAI(api_key=settings.openai_api_key)

"""
clients.py
──────────
Singleton API clients.

Import `claude_client`, `gemini_client` here instead of constructing them
in multiple modules. This also makes test-time monkey-patching easy.
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

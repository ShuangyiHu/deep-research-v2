"""
rewriter.py
───────────
Step 5 of the pipeline: rewrite weak sections → structure pass.
Structure pass no longer enforces a fixed section order.
"""

import json
import logging
from typing import Callable

from agents import Agent, Runner

logger = logging.getLogger(__name__)


def _build_rewrite_agent() -> Agent:
    return Agent(
        name="RewriteAgent",
        instructions=(
            "You are a senior research editor. You will receive a report, weak sections, "
            "rewrite instructions, and additional evidence.\n"
            "Rewrite ONLY the weak sections identified, incorporating the instructions and evidence.\n"
            "Keep all other sections unchanged.\n"
            "Return the COMPLETE revised report in markdown."
        ),
        model="gpt-4o",
    )


def _build_structure_agent() -> Agent:
    return Agent(
        name="StructureAgent",
        instructions=(
            "You are a copy editor. Your ONLY job is to improve the flow of the report — "
            "do NOT change any content, data, or analysis.\n"
            "Tasks:\n"
            "- Fix awkward transitions between sections\n"
            "- Remove any duplicate paragraphs\n"
            "- Ensure Introduction comes first and Conclusion comes last\n"
            "- Do NOT rename, reorder, or add sections beyond those fixes\n"
            "Return the COMPLETE report in markdown."
        ),
        model="gpt-4o-mini",
    )


_rewrite_agent: Agent | None = None
_structure_agent: Agent | None = None


def get_rewrite_agent() -> Agent:
    global _rewrite_agent
    if _rewrite_agent is None:
        _rewrite_agent = _build_rewrite_agent()
    return _rewrite_agent


def get_structure_agent() -> Agent:
    global _structure_agent
    if _structure_agent is None:
        _structure_agent = _build_structure_agent()
    return _structure_agent


async def rewrite_sections(
    report: str,
    feedback: dict,
    evidence: str | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> str:
    weak: list[str] = feedback.get("weak_sections", [])
    instructions: dict = feedback.get("rewrite_instructions", {})

    if not weak:
        _emit(on_progress, "No weak sections — skipping rewrite.")
        return report

    _emit(on_progress, f"Rewriting sections: {weak}…")

    rewrite_prompt = (
        f"Weak sections to improve: {weak}\n\n"
        f"Rewrite instructions per section:\n{json.dumps(instructions, indent=2)}\n\n"
        f"Additional evidence to incorporate:\n"
        f"{evidence if evidence else 'None — use your knowledge.'}\n\n"
        f"Original report:\n{report}"
    )

    rewritten_result = await Runner.run(get_rewrite_agent(), rewrite_prompt)
    rewritten_text: str = str(rewritten_result.final_output)

    _emit(on_progress, "Running structure pass…")
    structured_result = await Runner.run(get_structure_agent(), rewritten_text)
    final_text: str = str(structured_result.final_output)

    _emit(on_progress, "→ Rewrite + structure pass complete")
    return final_text


def _emit(cb, msg):
    logger.info(msg)
    if cb:
        cb(msg)
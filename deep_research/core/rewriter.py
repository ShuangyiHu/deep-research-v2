"""
rewriter.py
───────────
Step 5 of the pipeline: rewrite weak sections → structure pass.
Structure pass no longer enforces a fixed section order.
"""

import logging
from typing import Callable

from agents import Agent, Runner

logger = logging.getLogger(__name__)


def _build_rewrite_agent() -> Agent:
    return Agent(
        name="RewriteAgent",
        instructions=(
            "You are a senior research editor. You will receive a report, weak sections, "
            "rewrite instructions, and the full body of search evidence the report was built from.\n\n"
            "CRITICAL GROUNDING RULES:\n"
            "- Use ONLY facts, statistics, and claims found in the provided evidence.\n"
            "- NEVER introduce information from your training knowledge, even to 'improve' a weak section.\n"
            "- If the evidence does not support an improvement to a weak section, it is better to\n"
            "  state the limitation explicitly (e.g. 'the available evidence does not cover X in depth')\n"
            "  than to fabricate content.\n"
            "- Cite claims inline as compact clickable markdown links: "
            "  `([TechCrunch](https://...))`, where the link text is the publisher/title "
            "  from the evidence's `SOURCE:` line (or 'source' as fallback) and the URL is "
            "  the verbatim `SOURCE:` URL. If no URL exists, use `(source query: <exact "
            "  query>)` as plain text. NEVER invent, reconstruct, or guess a URL or domain.\n"
            "- Do NOT paste raw URLs into prose — always wrap them in markdown link syntax.\n\n"
            "Task:\n"
            "- You will receive a per-section checklist with three kinds of items:\n"
            "    * Missing angles — sub-topics absent from the section, add them.\n"
            "    * Questions to answer — answer each one directly, grounded in evidence.\n"
            "    * Evidence to cite — integrate each listed SOURCE url or search query.\n"
            "- Treat the checklist as a strict TODO: every item that has supporting\n"
            "  evidence in the provided material MUST be addressed. Do not paraphrase the\n"
            "  existing paragraph and call it done.\n"
            "- If a checklist item has NO supporting evidence, explicitly state\n"
            "  'the available evidence does not cover X' in the rewritten section — do\n"
            "  NOT silently skip it and do NOT fabricate.\n"
            "- Rewrite ONLY the weak sections identified. Keep all other sections unchanged verbatim.\n"
            "- Return the COMPLETE revised report in markdown."
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
    full_evidence: str,
    new_evidence: str | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> str:
    """
    Rewrite weak sections using ONLY the provided evidence.

    Args:
        report:        The current report to revise.
        feedback:      Evaluator feedback with weak_sections and rewrite_instructions.
        full_evidence: The complete body of search results backing the report
                       (from SearchDocumentCollection.to_eval_string()). Always required —
                       the rewriter must have access to the same evidence the report
                       was grounded in, otherwise it will fill gaps with training knowledge.
        new_evidence:  Optional supplemental evidence from the evaluator's agent-initiated
                       search this round. Shown separately so the rewriter knows what's new
                       and can prioritize addressing weak sections using it.
    """
    weak: list[str] = feedback.get("weak_sections", [])
    instructions: dict = feedback.get("rewrite_instructions", {})

    if not weak:
        _emit(on_progress, "No weak sections — skipping rewrite.")
        return report

    _emit(on_progress, f"Rewriting sections: {weak}…")

    new_evidence_block = (
        f"New supplemental evidence collected this round (prioritize integrating this "
        f"into the weak sections):\n{new_evidence}\n\n"
        if new_evidence
        else "No new evidence this round — rewrite by reorganizing or clarifying "
             "content using ONLY the full evidence below. Do NOT fabricate.\n\n"
    )

    instructions_block = _format_instructions(instructions)

    rewrite_prompt = (
        f"Weak sections to improve: {weak}\n\n"
        f"Per-section rewrite checklist (address EVERY item — these are the exact "
        f"gaps the evaluator flagged):\n{instructions_block}\n\n"
        f"{new_evidence_block}"
        f"Full evidence the report is grounded in (use ONLY this — do not invent):\n"
        f"{full_evidence}\n\n"
        f"Original report:\n{report}"
    )

    rewritten_result = await Runner.run(get_rewrite_agent(), rewrite_prompt)
    rewritten_text: str = str(rewritten_result.final_output)

    _emit(on_progress, "Running structure pass…")
    structured_result = await Runner.run(get_structure_agent(), rewritten_text)
    final_text: str = str(structured_result.final_output)

    _emit(on_progress, "→ Rewrite + structure pass complete")
    return final_text


def _format_instructions(instructions: dict) -> str:
    """Render the evaluator's structured rewrite_instructions as a checklist.

    Accepts both the new object format
        {section: {missing_angles, questions_to_answer, evidence_to_integrate}}
    and the legacy free-text form
        {section: "instruction string"}
    (older evaluator outputs / tests).
    """
    if not instructions:
        return "(no per-section instructions provided)"

    lines: list[str] = []
    for section, body in instructions.items():
        lines.append(f"### {section}")
        if isinstance(body, str):
            lines.append(f"- {body}")
            continue
        if not isinstance(body, dict):
            lines.append(f"- {body!r}")
            continue
        angles = body.get("missing_angles") or []
        questions = body.get("questions_to_answer") or []
        evidence = body.get("evidence_to_integrate") or []
        if angles:
            lines.append("- Missing angles to add:")
            lines.extend(f"    • {a}" for a in angles)
        if questions:
            lines.append("- Questions this section must answer:")
            lines.extend(f"    • {q}" for q in questions)
        if evidence:
            lines.append("- Evidence to cite (verbatim SOURCE urls or search queries):")
            lines.extend(f"    • {e}" for e in evidence)
        if not (angles or questions or evidence):
            lines.append("- (no specific instructions — do a prose polish only)")
    return "\n".join(lines)


def _emit(cb, msg):
    logger.info(msg)
    if cb:
        cb(msg)
"""
ui/app.py  — Deep Research
──────────────────────────
LEFT  : query · examples · email (with time warning) · sliders · generate/cancel
RIGHT : status · progress log · final report · download

Email design:
  - User fills email BEFORE clicking Generate
  - Email is passed to the API at generate time
  - Celery worker sends it automatically when the report is done
  - No manual "Send" button needed (avoids needing SendGrid configured separately)
"""

import time
import tempfile
import httpx
import gradio as gr

API_BASE      = "http://localhost:8000/api/v1"
POLL_INTERVAL = 2.5
MAX_WAIT      = 60 * 20

EXAMPLE_QUERIES = [
    "Between 2025 and 2030, how are AI-assisted coding tools expected to shift "
    "hiring demand for junior software engineers in North America, and which "
    "technical or workflow skills are projected to remain complementary to "
    "AI-driven software development?",

    "What are the projected economic effects of widespread autonomous vehicle "
    "adoption on urban logistics and last-mile delivery jobs in the US by 2030?",

    "How is the rapid expansion of GLP-1 weight-loss drugs expected to reshape "
    "the US healthcare industry and consumer spending patterns through 2027?",
]

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;1,400&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@300;400;500&display=swap');

:root {
    --ink:      #1a1a1a;
    --paper:    #f6f1e9;
    --mid:      #eae4d8;
    --border:   #cfc8ba;
    --amber:    #b87a10;
    --amber-bg: #fdf3dc;
    --red:      #992828;
    --green:    #256638;
    --white:    #ffffff;
    --mono:     'IBM Plex Mono', monospace;
    --serif:    'Lora', Georgia, serif;
    --sans:     'IBM Plex Sans', system-ui, sans-serif;
    --r:        8px;
}

body, .gradio-container {
    background: var(--paper) !important;
    font-family: var(--sans) !important;
    font-size: 17px !important;
    color: var(--ink) !important;
}
.gradio-container { max-width: 1360px !important; padding: 0 28px !important; }
footer { display: none !important; }

/* Header */
#dr-header { border-bottom: 2px solid var(--ink); padding: 32px 0 20px; margin-bottom: 24px; }
#dr-header h1 {
    font-family: var(--serif) !important;
    font-size: 2.6rem !important;
    font-weight: 600 !important;
    margin: 0 0 8px !important;
    letter-spacing: -0.02em;
    color: var(--ink) !important;
}
#dr-header p { font-size: 1.05rem !important; color: #606060 !important; margin: 0 !important; font-weight: 300 !important; }

/* Inputs */
textarea, input[type=text], input[type=email] {
    background: var(--mid) !important;
    border: 1.5px solid var(--border) !important;
    border-radius: var(--r) !important;
    font-family: var(--sans) !important;
    font-size: 1rem !important;
    color: var(--ink) !important;
    padding: 14px !important;
    line-height: 1.6 !important;
    transition: border-color .15s !important;
}
textarea:focus, input:focus {
    border-color: var(--amber) !important;
    outline: none !important;
    box-shadow: 0 0 0 3px rgba(184,122,16,.12) !important;
}

/* Section label */
.dr-label {
    font-family: var(--mono) !important;
    font-size: 0.72rem !important;
    font-weight: 500 !important;
    text-transform: uppercase;
    letter-spacing: 0.13em;
    color: var(--amber) !important;
    margin: 0 0 10px !important;
    display: block;
}

/* Example buttons */
.ex-btn, .ex-btn button {
    width: 100% !important;
    text-align: left !important;
    background: var(--mid) !important;
    border: 1.5px solid var(--border) !important;
    border-radius: var(--r) !important;
    padding: 13px 15px !important;
    font-family: var(--sans) !important;
    font-size: 0.95rem !important;
    color: var(--ink) !important;
    cursor: pointer !important;
    margin-bottom: 10px !important;
    line-height: 1.55 !important;
    white-space: normal !important;
    transition: border-color .15s, background .15s !important;
}
.ex-btn button:hover { border-color: var(--amber) !important; background: var(--amber-bg) !important; }

/* Email notice */
#email-notice {
    background: var(--amber-bg);
    border: 1.5px solid #e8c070;
    border-radius: var(--r);
    padding: 12px 14px;
    font-size: 0.92rem !important;
    color: #7a5200 !important;
    line-height: 1.55;
    margin-bottom: 12px;
}

/* Generate button */
#gen-btn, #gen-btn button {
    background: var(--ink) !important;
    color: var(--paper) !important;
    border: none !important;
    border-radius: var(--r) !important;
    font-family: var(--mono) !important;
    font-size: 0.95rem !important;
    font-weight: 500 !important;
    letter-spacing: 0.05em !important;
    padding: 16px 0 !important;
    width: 100% !important;
    cursor: pointer !important;
    transition: background .15s !important;
    margin-bottom: 10px !important;
}
#gen-btn button:hover { background: #333 !important; }

/* Cancel button */
#can-btn, #can-btn button {
    background: transparent !important;
    color: var(--red) !important;
    border: 1.5px solid var(--red) !important;
    border-radius: var(--r) !important;
    font-family: var(--mono) !important;
    font-size: 0.88rem !important;
    padding: 12px 0 !important;
    width: 100% !important;
    cursor: pointer !important;
    transition: all .15s !important;
}
#can-btn button:hover { background: var(--red) !important; color: #fff !important; }

/* Slider labels */
label span { font-size: 1rem !important; }

/* Status */
#dr-status p { font-family: var(--mono) !important; font-size: 1rem !important; font-weight: 500 !important; margin: 0 !important; }

/* Progress log */
#dr-log textarea {
    background: #111 !important;
    color: #d4c99a !important;
    font-family: var(--mono) !important;
    font-size: 0.88rem !important;
    border: none !important;
    border-radius: var(--r) !important;
    padding: 16px !important;
    line-height: 1.7 !important;
    min-height: 260px !important;
    resize: none !important;
}

/* Report */
#dr-report {
    background: var(--white) !important;
    border: 1.5px solid var(--border) !important;
    border-radius: var(--r) !important;
    padding: 28px 36px !important;
    min-height: 120px !important;
    font-size: 1rem !important;
    line-height: 1.75 !important;
}
#dr-report h1 { font-family: var(--serif) !important; font-size: 1.8rem !important; font-weight: 600 !important; }
#dr-report h2 {
    font-family: var(--serif) !important; font-size: 1.25rem !important; font-weight: 600 !important;
    border-bottom: 1px solid var(--border) !important; padding-bottom: 6px !important; margin-top: 1.8em !important;
}

/* Email sent confirmation */
#email-sent p { font-family: var(--mono) !important; font-size: 0.9rem !important; color: var(--green) !important; }

/* Divider */
.dr-div { height: 1px; background: var(--border); margin: 18px 0; }

/* Footer */
#dr-footer { border-top: 1px solid var(--border); margin-top: 36px; padding-top: 14px; font-family: var(--mono); font-size: 0.78rem; color: #999; }
"""

# ── API helpers ───────────────────────────────────────────────────────────────

def _post_generate(query, email, threshold, max_iter):
    payload = {"query": query, "threshold": threshold, "max_iter": max_iter}
    # Email passed at generate time → Celery task sends automatically on completion
    if email and isinstance(email, str) and email.strip():
        payload["email"] = email.strip()
    try:
        r = httpx.post(f"{API_BASE}/generate", json=payload, timeout=15)
        r.raise_for_status()
        return r.json()["job_id"]
    except Exception:
        return None

def _get_status(job_id):
    try:
        r = httpx.get(f"{API_BASE}/status/{job_id}", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"state": "FAILURE", "error": str(exc), "log": [], "report": None}

def _cancel(job_id):
    try:
        httpx.delete(f"{API_BASE}/cancel/{job_id}", timeout=10)
    except Exception:
        pass

ICONS = {
    "PENDING":  "⏳  QUEUED",
    "PROGRESS": "⚙️  RUNNING…",
    "SUCCESS":  "✓  COMPLETE",
    "FAILURE":  "✗  FAILED",
    "REVOKED":  "⊘  CANCELLED",
}
def _badge(state): return ICONS.get(state, state)
def _fmt_log(lines): return "\n".join(lines) if lines else "Waiting for worker…"

# ── Generator ─────────────────────────────────────────────────────────────────
# Yield order: status_md, log_box, report_box, job_id_state,
#              gen_btn, can_btn, download_file, email_sent_md

def generate(query, email, threshold, max_iter, _job):
    if not query or len(query.strip()) < 20:
        yield ("⚠️ Query must be at least 20 characters.", "", gr.update(),
               "", gr.update(interactive=True), gr.update(interactive=False),
               None, "")
        return

    yield ("", "Submitting…", gr.update(),
           "", gr.update(interactive=False), gr.update(interactive=False),
           None, "")

    job_id = _post_generate(query, email, threshold, max_iter)
    if not job_id:
        yield ("✗  Cannot reach API — is uvicorn running?",
               "Connection failed.", gr.update(),
               "", gr.update(interactive=True), gr.update(interactive=False),
               None, "")
        return

    # Tell user their email is registered
    email_note = ""
    if email and isinstance(email, str) and email.strip():
        email_note = f"✓ Report will be emailed to **{email.strip()}** when complete."

    yield (_badge("PENDING"), f"Task queued → {job_id}", gr.update(),
           job_id, gr.update(interactive=False), gr.update(interactive=True),
           None, email_note)

    start = time.time()
    while time.time() - start < MAX_WAIT:
        time.sleep(POLL_INTERVAL)
        s     = _get_status(job_id)
        state = s.get("state", "PENDING")
        log   = s.get("log", [])

        if state == "SUCCESS":
            report = s.get("report") or "*No report returned.*"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".md", mode="w", encoding="utf-8")
            tmp.write(report); tmp.close()
            sent = s.get("email_sent")
            sent_note = ""
            if email and isinstance(email, str) and email.strip():
                sent_note = (
                    f"✓ Report emailed to **{email.strip()}**"
                    if sent else
                    f"⚠️ Email delivery failed — check SENDGRID_API_KEY in .env"
                )
            yield (_badge("SUCCESS"), _fmt_log(log), report,
                   job_id, gr.update(interactive=True), gr.update(interactive=False),
                   tmp.name, sent_note)
            return

        if state == "FAILURE":
            err = s.get("error", "Unknown error")
            yield (_badge("FAILURE"), _fmt_log(log) + f"\n\n✗ {err}", gr.update(),
                   job_id, gr.update(interactive=True), gr.update(interactive=False),
                   None, "")
            return

        if state == "REVOKED":
            yield (_badge("REVOKED"), "Task cancelled.", gr.update(),
                   job_id, gr.update(interactive=True), gr.update(interactive=False),
                   None, "")
            return

        yield (_badge(state), _fmt_log(log), gr.update(),
               job_id, gr.update(interactive=False), gr.update(interactive=True),
               None, email_note)

    yield ("⚠️  TIMEOUT", "Max wait exceeded.", gr.update(),
           job_id, gr.update(interactive=True), gr.update(interactive=False),
           None, "")


def cancel(job_id):
    if job_id:
        _cancel(job_id)
    return ("⊘  CANCELLED", "Cancelled by user.", gr.update(),
            "", gr.update(interactive=True), gr.update(interactive=False),
            None, "")


# ── UI ────────────────────────────────────────────────────────────────────────

def build_ui() -> gr.Blocks:
    with gr.Blocks(css=CSS, title="Deep Research") as demo:

        job_id_state = gr.State(value="")

        gr.HTML("""
        <div id="dr-header">
            <h1>Deep Research</h1>
            <p>Multi-model AI pipeline &mdash; Claude &times; Gemini &times; GPT-4o &mdash;
               evaluates, rewrites, and refines until the report earns a quality threshold.</p>
        </div>
        """)

        with gr.Row():

            # ── LEFT COLUMN ───────────────────────────────────────────────────
            with gr.Column(scale=4, min_width=380):

                gr.HTML('<span class="dr-label">Research Question</span>')
                query_box = gr.Textbox(
                    placeholder="Ask a deep, specific research question — minimum 20 characters.",
                    lines=6, max_lines=12,
                    show_label=False, container=False,
                )

                gr.HTML('<div class="dr-div"></div><span class="dr-label">Example Questions</span>')
                for q in EXAMPLE_QUERIES:
                    btn = gr.Button(q, elem_classes=["ex-btn"], size="sm")
                    btn.click(fn=lambda txt=q: txt, outputs=query_box)

                gr.HTML('<div class="dr-div"></div>')

                # Email — left column, BEFORE generate button
                gr.HTML("""
                <div id="email-notice">
                  ⏱ Generating a report typically takes <strong>5–10 minutes</strong>.
                  Leave your email below and we'll send you the finished report automatically —
                  no need to keep this tab open.
                </div>
                <span class="dr-label">Email for delivery (optional)</span>
                """)
                email_box = gr.Textbox(
                    placeholder="your@email.com",
                    show_label=False, container=False,
                )
                # Confirmation shown after clicking Generate
                email_sent_md = gr.Markdown(value="", elem_id="email-sent")

                gr.HTML('<div class="dr-div"></div><span class="dr-label">Settings</span>')
                threshold_slider = gr.Slider(minimum=1, maximum=10, value=8, step=1, label="Quality threshold (1–10)")
                max_iter_slider  = gr.Slider(minimum=1, maximum=8,  value=4, step=1, label="Max refinement iterations")

                gr.HTML('<div class="dr-div"></div>')
                gen_btn = gr.Button("Generate Report →", elem_id="gen-btn", variant="primary")
                can_btn = gr.Button("Cancel", elem_id="can-btn", interactive=False)

            # ── RIGHT COLUMN — no email here ──────────────────────────────────
            with gr.Column(scale=6):

                gr.HTML('<span class="dr-label">Status</span>')
                status_md = gr.Markdown(value="", elem_id="dr-status")

                gr.HTML('<span class="dr-label" style="margin-top:16px">Progress Log</span>')
                log_box = gr.Textbox(
                    value="", lines=14, max_lines=20,
                    interactive=False, show_label=False, container=False,
                    elem_id="dr-log",
                )

                gr.HTML('<div class="dr-div"></div><span class="dr-label">Final Report</span>')
                report_box = gr.Markdown(
                    value="*Your report will appear here once the pipeline completes.*",
                    elem_id="dr-report",
                )
                download_file = gr.File(label="Download .md", interactive=False)

        gr.HTML("""
        <div id="dr-footer">
            Deep Research v2 &nbsp;·&nbsp; Draft: GPT-4o-mini &nbsp;·&nbsp;
            Evaluators: claude-sonnet-4-6 + Gemini 2.0 Flash &nbsp;·&nbsp; Rewriter: GPT-4o
        </div>
        """)

        outputs = [
            status_md, log_box, report_box,
            job_id_state, gen_btn, can_btn,
            download_file, email_sent_md,
        ]

        gen_btn.click(
            fn=generate,
            inputs=[query_box, email_box, threshold_slider, max_iter_slider, job_id_state],
            outputs=outputs,
        )
        can_btn.click(fn=cancel, inputs=[job_id_state], outputs=outputs)

    return demo


def create_gradio_app() -> gr.Blocks:
    return build_ui()


if __name__ == "__main__":
    build_ui().launch(server_name="0.0.0.0", server_port=7860, show_error=True)
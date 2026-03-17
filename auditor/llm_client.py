"""Gemini-powered AI summary generation for audit results."""

import time

import google.generativeai as genai


_MODEL = "gemini-1.5-flash"

_SYSTEM_INSTRUCTION = (
    "You are an expert hackathon submission auditor. "
    "You write factual, concise, professional assessments based strictly on the "
    "commit metadata and metrics you are given. "
    "Do not invent details not present in the data. "
    "Never accuse a team of wrongdoing without evidence — use careful language "
    "such as 'suggests', 'indicates', 'appears to'."
)

_PROMPT_TEMPLATE = """\
Analyse the following hackathon repository audit data and write a 2-3 sentence \
qualitative summary of your findings. Cite the most significant metrics and explain \
what they imply about the authenticity and legitimacy of the submission.

--- AUDIT DATA ---
Team name        : {team_name}
Repository       : {repo_url}
Risk score       : {risk_score}/100
Status           : {status}
Total commits    : {total_commits}
In-window commits: {in_window_commits}  (commits inside the hackathon time window)
Pre-event commits: {pre_hackathon_commits}
Total lines added: {total_lines_added}
Event duration   : {hackathon_duration_hours} hours

Team contribution breakdown:
{balance_text}

Triggered flags:
{flags_text}
--- END DATA ---

Write your 2-3 sentence summary now. Start directly with the assessment; \
do not include a preamble like "Here is my summary:".
"""


def generate_summary(
    api_key: str,
    team_name: str,
    repo_url: str,
    risk_score: int,
    status: str,
    flags: list[dict],
    raw_metrics: dict,
    sleep_delay: float = 4.5,
) -> str:
    """Call Gemini to produce a 2-3 sentence qualitative summary.

    Sleeps *sleep_delay* seconds after the call to stay within Gemini's free-tier
    rate limit (15 requests per minute).  Set sleep_delay=0 to skip.
    """
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=_MODEL,
        system_instruction=_SYSTEM_INSTRUCTION,
    )

    prompt = _build_prompt(team_name, repo_url, risk_score, status, flags, raw_metrics)

    try:
        response = model.generate_content(prompt)
        summary = response.text.strip()
    except Exception as exc:
        summary = f"[AI summary unavailable: {exc}]"
    finally:
        if sleep_delay > 0:
            time.sleep(sleep_delay)

    return summary


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_prompt(
    team_name: str,
    repo_url: str,
    risk_score: int,
    status: str,
    flags: list[dict],
    raw_metrics: dict,
) -> str:
    balance = raw_metrics.get("team_balance", {})
    if balance:
        balance_lines = [
            f"  @{member}: {v['commits']} commits, "
            f"{v['additions']:,} lines added ({v['percentage']}%)"
            for member, v in balance.items()
        ]
        balance_text = "\n".join(balance_lines)
    else:
        balance_text = "  (no data)"

    if flags:
        flags_lines = [
            f"  [{f['severity']}] {f['code']}: {f['detail']}"
            for f in flags
        ]
        flags_text = "\n".join(flags_lines)
    else:
        flags_text = "  (none — submission appears clean)"

    return _PROMPT_TEMPLATE.format(
        team_name=team_name,
        repo_url=repo_url,
        risk_score=risk_score,
        status=status,
        total_commits=raw_metrics.get("total_commits", "N/A"),
        in_window_commits=raw_metrics.get("in_window_commits", "N/A"),
        pre_hackathon_commits=raw_metrics.get("pre_hackathon_commits", "N/A"),
        total_lines_added=f"{raw_metrics.get('total_lines_added', 0):,}",
        hackathon_duration_hours=raw_metrics.get("hackathon_duration_hours", "N/A"),
        balance_text=balance_text,
        flags_text=flags_text,
    )

"""Core authenticity evaluation engine.

Evaluates repositories against three behavioural profiles:

  Profile A — AI "Vibe Coding"  (acceptable)
  Profile B — Pre-building       (rule-breaking)
  Profile C — Plagiarism/Copying (rule-breaking)

Plus team-contribution verification (presence & balance).
"""

from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Thresholds (adjust via constants, not magic numbers in logic)
# ---------------------------------------------------------------------------

# How many days *before* the hackathon a commit must be to trigger the
# "old non-team commit" plagiarism flag.
_OLD_COMMIT_DAYS = 30

# Fraction of a team's total additions that one person must exceed to trigger
# the dominant-contributor flag (e.g. 0.90 = 90 %).
_DOMINANT_THRESHOLD = 0.90

# Minimum filtered additions in the hackathon window for the
# "single massive commit" flag to apply.
_SINGLE_COMMIT_MIN_LINES = 500

# Fraction of in-window additions concentrated in one commit to flag it.
_SINGLE_COMMIT_CONCENTRATION = 0.80

# Minimum lines added in contributor stats before the hackathon that
# triggers the "bulk pre-hackathon work" flag from aggregate stats.
_PRE_HACK_STATS_MIN_LINES = 300
_PRE_HACK_STATS_RATIO = 0.60  # 60 % of total lines written before the event


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze(
    team_name: str,
    repo_url: str,
    repo_data: dict,
    commits: list[dict],
    contributor_stats: list[dict],
    team_members: list[str],
    hackathon_start: datetime,
    hackathon_end: datetime,
    min_commits_per_member: int,
) -> dict:
    """Evaluate a repository and return a structured risk assessment.

    Returns a dict with keys:
        risk_score        — int 0-100
        status            — "PASS" | "REVIEW" | "HIGHLY SUSPICIOUS"
        triggered_flags   — list of flag dicts {code, severity, detail}
        raw_metrics       — dict of counts and percentages
    """
    flags: list[dict] = []
    score: int = 0
    team_set = {m.lower() for m in team_members}

    # Build contributor map (bot accounts excluded)
    contrib_map: dict[str, dict] = {
        s["login"].lower(): s
        for s in contributor_stats
        if s.get("login") and not _is_bot_login(s["login"])
    }

    # -----------------------------------------------------------------------
    # Profile C — Plagiarism / Copying
    # -----------------------------------------------------------------------

    # C1: Repository is an explicit GitHub fork
    if repo_data.get("is_fork"):
        score += 50
        flags.append(
            _flag(
                "IS_FORK",
                "CRITICAL",
                f"Repository is a fork of '{repo_data.get('fork_parent', 'unknown')}'. "
                "Original work must be created from scratch during the event.",
            )
        )

    # C2: Commits by non-team authors that predate the hackathon by 30+ days
    cutoff = hackathon_start - timedelta(days=_OLD_COMMIT_DAYS)
    old_nonteam = [
        c
        for c in commits
        if c.get("author_date")
        and c["author_date"] < cutoff
        and _author_login(c) not in team_set
        and not _is_bot_commit(c)
    ]
    if old_nonteam:
        unique_authors = _unique_authors(old_nonteam, limit=5)
        score += 40
        flags.append(
            _flag(
                "OLD_NONTEAM_COMMITS",
                "HIGH",
                f"{len(old_nonteam)} commit(s) from non-team authors predate the "
                f"hackathon by 30+ days (authors: {unique_authors}). "
                "This strongly suggests the codebase was taken from an existing project.",
            )
        )

    # -----------------------------------------------------------------------
    # Profile B — Pre-building
    # -----------------------------------------------------------------------

    # B1: git author_date before the hackathon start
    prebuilt = [
        c for c in commits if c.get("author_date") and c["author_date"] < hackathon_start
    ]
    if prebuilt:
        pct = len(prebuilt) / max(len(commits), 1) * 100
        earliest = min(c["author_date"] for c in prebuilt)

        if pct > 50:
            penalty, severity = 40, "HIGH"
        elif pct > 20:
            penalty, severity = 25, "MEDIUM"
        else:
            penalty, severity = 15, "MEDIUM"

        score += penalty
        flags.append(
            _flag(
                "PRE_HACKATHON_AUTHOR_DATE",
                severity,
                f"{len(prebuilt)}/{len(commits)} commits ({pct:.0f}%) carry a git "
                f"author_date before the hackathon started. "
                f"Earliest: {_fmt_dt(earliest)}. "
                "author_date is set locally and cannot be faked by rebasing; "
                "it proves local development occurred before the event.",
            )
        )

    # B2: committer_date before hackathon start (pushed before the event)
    pushed_early = [
        c
        for c in commits
        if c.get("committer_date")
        and c["committer_date"] < hackathon_start
        # Only flag if author_date is NOT already pre-hackathon (avoid double-counting)
        and not (c.get("author_date") and c["author_date"] < hackathon_start)
    ]
    if pushed_early:
        score += 15
        flags.append(
            _flag(
                "PRE_HACKATHON_PUSH",
                "MEDIUM",
                f"{len(pushed_early)} commit(s) have committer_date before hackathon "
                "start, meaning code was pushed to GitHub before the event window opened.",
            )
        )

    # B3: Bulk pre-hackathon work visible in aggregate contributor stats
    # (catches large repos where individual old commits are outside our 300-commit window)
    for stat in contributor_stats:
        login = (stat.get("login") or "").lower()
        if login not in team_set or _is_bot_login(login):
            continue

        pre_hack_lines = sum(
            w.get("a", 0)
            for w in (stat.get("weeks") or [])
            if _week_ts_to_dt(w.get("w", 0)) < hackathon_start
        )
        total_lines = stat.get("total_additions", 0)

        if (
            total_lines >= _PRE_HACK_STATS_MIN_LINES
            and pre_hack_lines / total_lines >= _PRE_HACK_STATS_RATIO
        ):
            pct = pre_hack_lines / total_lines * 100
            # Only add flag if not already caught by B1
            already_flagged = any(f["code"] == "PRE_HACKATHON_AUTHOR_DATE" for f in flags)
            if not already_flagged:
                score += 20
                flags.append(
                    _flag(
                        "BULK_PRE_HACKATHON_WORK",
                        "HIGH",
                        f"@{login} has {pre_hack_lines:,} lines ({pct:.0f}% of their total) "
                        f"authored before the hackathon according to GitHub's contribution "
                        "statistics. Most of the codebase appears to have been built in advance.",
                    )
                )
            break

    # B4: Single massive commit — the "perfect drop" pattern
    in_window = [
        c
        for c in commits
        if c.get("author_date") and hackathon_start <= c["author_date"] <= hackathon_end
    ]
    in_window_with_stats = [c for c in in_window if c.get("additions") is not None]

    if in_window_with_stats and len(in_window) <= 3:
        total_in_window = sum(c.get("additions", 0) for c in in_window_with_stats)
        largest = max(in_window_with_stats, key=lambda c: c.get("additions", 0))
        largest_lines = largest.get("additions", 0)

        concentration = largest_lines / total_in_window if total_in_window > 0 else 0

        if (
            largest_lines >= _SINGLE_COMMIT_MIN_LINES
            and concentration >= _SINGLE_COMMIT_CONCENTRATION
        ):
            score += 30
            flags.append(
                _flag(
                    "SINGLE_MASSIVE_COMMIT",
                    "HIGH",
                    f"Only {len(in_window)} commit(s) found inside the hackathon window. "
                    f"The single largest adds {largest_lines:,} real lines of code "
                    f"({concentration*100:.0f}% of all in-window code). "
                    "No iterative debugging or follow-up commits were detected — "
                    "genuine AI-assisted development almost always produces many small commits.",
                )
            )

    # -----------------------------------------------------------------------
    # Team Contribution Verification
    # -----------------------------------------------------------------------

    # T1: Missing / inactive team members
    missing = []
    for member in team_members:
        c = contrib_map.get(member.lower(), {})
        if c.get("total_commits", 0) < min_commits_per_member and c.get("total_additions", 0) < 10:
            missing.append(member)

    if missing:
        score += 10
        flags.append(
            _flag(
                "MISSING_CONTRIBUTOR",
                "LOW",
                f"No significant contribution detected from: "
                f"{', '.join(f'@{m}' for m in missing)}. "
                "Every declared team member should have at least "
                f"{min_commits_per_member} commit(s) or 10 lines added.",
            )
        )

    # T2: Dominant single contributor in a multi-person team
    if len(team_members) > 1:
        team_total_additions = sum(
            contrib_map.get(m.lower(), {}).get("total_additions", 0) for m in team_members
        )
        if team_total_additions > 0:
            for member in team_members:
                member_additions = contrib_map.get(member.lower(), {}).get("total_additions", 0)
                share = member_additions / team_total_additions
                if share > _DOMINANT_THRESHOLD:
                    score += 15
                    flags.append(
                        _flag(
                            "DOMINANT_SINGLE_CONTRIBUTOR",
                            "MEDIUM",
                            f"@{member} accounts for {share*100:.0f}% of code additions "
                            f"in a {len(team_members)}-person team (>{_DOMINANT_THRESHOLD*100:.0f}% "
                            "threshold). Other declared members have negligible contributions.",
                        )
                    )
                    break

    # -----------------------------------------------------------------------
    # Final scoring and status
    # -----------------------------------------------------------------------
    score = min(score, 100)

    if score >= 60:
        status = "HIGHLY SUSPICIOUS"
    elif score >= 30:
        status = "REVIEW"
    else:
        status = "PASS"

    raw_metrics = _build_raw_metrics(
        commits=commits,
        in_window=in_window,
        prebuilt=prebuilt,
        contributor_stats=contributor_stats,
        team_members=team_members,
        contrib_map=contrib_map,
        hackathon_start=hackathon_start,
        hackathon_end=hackathon_end,
    )

    return {
        "risk_score": score,
        "status": status,
        "triggered_flags": flags,
        "raw_metrics": raw_metrics,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _flag(code: str, severity: str, detail: str) -> dict:
    return {"code": code, "severity": severity, "detail": detail}


def _author_login(commit: dict) -> str:
    return (commit.get("author_login") or "").lower()


def _is_bot_login(login: str) -> bool:
    login = login.lower()
    return "[bot]" in login or login in {"web-flow", "github-actions"}


def _is_bot_commit(commit: dict) -> bool:
    login = commit.get("author_login", "") or ""
    email = commit.get("author_email", "") or ""
    return _is_bot_login(login) or "noreply" in email.lower()


def _unique_authors(commits: list[dict], limit: int = 5) -> str:
    seen: set[str] = set()
    for c in commits:
        name = c.get("author_login") or c.get("author_name") or "unknown"
        seen.add(name)
        if len(seen) >= limit:
            break
    return ", ".join(f"@{a}" for a in sorted(seen))


def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "unknown"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _week_ts_to_dt(ts: int) -> datetime:
    """Convert a Unix timestamp (GitHub weekly bucket) to a UTC datetime."""
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _build_raw_metrics(
    commits: list[dict],
    in_window: list[dict],
    prebuilt: list[dict],
    contributor_stats: list[dict],
    team_members: list[str],
    contrib_map: dict,
    hackathon_start: datetime,
    hackathon_end: datetime,
) -> dict:
    duration_hours = (hackathon_end - hackathon_start).total_seconds() / 3600

    total_lines = sum(
        s.get("total_additions", 0)
        for s in contributor_stats
        if not _is_bot_login(s.get("login", ""))
    )

    team_total = sum(
        contrib_map.get(m.lower(), {}).get("total_additions", 0) for m in team_members
    )

    team_balance: dict[str, dict] = {}
    for member in team_members:
        c = contrib_map.get(member.lower(), {})
        additions = c.get("total_additions", 0)
        team_balance[member] = {
            "commits": c.get("total_commits", 0),
            "additions": additions,
            "percentage": round(additions / team_total * 100, 1) if team_total > 0 else 0.0,
        }

    return {
        "total_commits": len(commits),
        "in_window_commits": len(in_window),
        "pre_hackathon_commits": len(prebuilt),
        "total_lines_added": total_lines,
        "hackathon_duration_hours": round(duration_hours, 1),
        "team_balance": team_balance,
    }

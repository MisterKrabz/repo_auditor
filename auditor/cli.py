"""CLI entry-point and pipeline orchestration."""

import argparse
import sys
import time
from datetime import datetime, timedelta

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from auditor.analyzer import analyze
from auditor.config import ConfigError, load_config
from auditor.github_client import GitHubClient, parse_repo_url
from auditor.input_parser import InputError, parse_teams
from auditor.llm_client import generate_summary
from auditor.reporter import print_summary_table, print_verbose_result, write_report

console = Console()

# Maximum number of commits we ask GitHub for per repo.
# Hackathon projects rarely exceed 200; 300 gives a comfortable buffer.
_DEFAULT_MAX_COMMITS = 300

# How many commits we fetch full stats (additions / deletions / files) for.
# Prioritises in-window commits, then nearby pre-event commits.
_DEFAULT_MAX_STATS = 50


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-auditor",
        description=(
            "AI-powered hackathon repository authenticity auditor.\n"
            "Evaluates GitHub submissions for vibe coding, pre-building, "
            "and plagiarism."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py teams.csv
  python main.py teams.json --output-dir ./reports
  python main.py teams.csv --verbose
  python main.py teams.csv --skip-ai --max-commits 100
""",
    )
    parser.add_argument(
        "input_file",
        help="Path to .csv or .json file containing team submissions.",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        metavar="DIR",
        help="Directory where the JSON report file will be saved (default: current dir).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print a detailed per-team breakdown to the console.",
    )
    parser.add_argument(
        "--skip-ai",
        action="store_true",
        help="Skip Gemini AI summary generation (faster, no API quota used).",
    )
    parser.add_argument(
        "--max-commits",
        type=int,
        default=_DEFAULT_MAX_COMMITS,
        metavar="N",
        help=f"Maximum commits to fetch per repo (default: {_DEFAULT_MAX_COMMITS}).",
    )
    parser.add_argument(
        "--max-stats",
        type=int,
        default=_DEFAULT_MAX_STATS,
        metavar="N",
        help=(
            f"Maximum commits to retrieve full stats for (default: {_DEFAULT_MAX_STATS}). "
            "Each stat fetch is one GitHub API call."
        ),
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # ---- Configuration ----
    try:
        config = load_config()
    except ConfigError as exc:
        console.print(f"[bold red]Configuration error:[/bold red] {exc}")
        sys.exit(1)

    # ---- Input parsing ----
    try:
        teams = parse_teams(args.input_file)
    except (FileNotFoundError, InputError, ValueError) as exc:
        console.print(f"[bold red]Input error:[/bold red] {exc}")
        sys.exit(1)

    console.print(
        f"\n[bold cyan]Repo Auditor[/bold cyan]  "
        f"Hackathon window: "
        f"[dim]{config['hackathon_start'].strftime('%Y-%m-%d %H:%M UTC')}[/dim] → "
        f"[dim]{config['hackathon_end'].strftime('%Y-%m-%d %H:%M UTC')}[/dim]"
    )
    console.print(
        f"Analysing [bold]{len(teams)}[/bold] team(s) from "
        f"[bold]{args.input_file}[/bold] …\n"
    )

    github = GitHubClient(config["github_token"])
    results: list[dict] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        overall = progress.add_task("[cyan]Overall progress", total=len(teams))

        for team in teams:
            team_name = team["team_name"]
            repo_url = team["repo_url"]
            members = team["member_handles"]

            progress.update(overall, description=f"[cyan]Auditing:[/cyan] {team_name}")

            try:
                owner, repo_name = parse_repo_url(repo_url)

                step = progress.add_task(
                    f"  [dim]└─[/dim] {team_name}: fetching repo metadata",
                    total=5,
                )

                # 1 — Repo metadata
                repo_data = github.fetch_repo(owner, repo_name)
                progress.advance(step)

                # 2 — Commit list (no stats yet)
                progress.update(step, description=f"  [dim]└─[/dim] {team_name}: fetching commits")
                commits = github.fetch_commits(owner, repo_name, max_count=args.max_commits)
                progress.advance(step)

                # 3 — Enrich key commits with per-file stats (boilerplate-filtered)
                progress.update(
                    step,
                    description=f"  [dim]└─[/dim] {team_name}: fetching commit stats",
                )
                commits = _enrich_with_stats(
                    github=github,
                    commits=commits,
                    owner=owner,
                    repo_name=repo_name,
                    hackathon_start=config["hackathon_start"],
                    hackathon_end=config["hackathon_end"],
                    max_fetches=args.max_stats,
                )
                progress.advance(step)

                # 4 — Aggregate contributor statistics
                progress.update(
                    step,
                    description=f"  [dim]└─[/dim] {team_name}: fetching contributor stats",
                )
                contributor_stats = github.fetch_contributor_stats(owner, repo_name)
                progress.advance(step)

                # 5 — Core analysis
                analysis = analyze(
                    team_name=team_name,
                    repo_url=repo_url,
                    repo_data=repo_data,
                    commits=commits,
                    contributor_stats=contributor_stats,
                    team_members=members,
                    hackathon_start=config["hackathon_start"],
                    hackathon_end=config["hackathon_end"],
                    min_commits_per_member=config["min_commits_per_member"],
                )

                # 6 — AI summary (optional)
                ai_summary = "[skipped]"
                if not args.skip_ai and config.get("gemini_api_key"):
                    progress.update(
                        step,
                        description=f"  [dim]└─[/dim] {team_name}: generating AI summary",
                    )
                    ai_summary = generate_summary(
                        api_key=config["gemini_api_key"],
                        team_name=team_name,
                        repo_url=repo_url,
                        risk_score=analysis["risk_score"],
                        status=analysis["status"],
                        flags=analysis["triggered_flags"],
                        raw_metrics=analysis["raw_metrics"],
                        sleep_delay=config["api_sleep_delay"],
                    )
                elif not args.skip_ai and not config.get("gemini_api_key"):
                    ai_summary = "[GEMINI_API_KEY not set — AI summary skipped]"

                progress.advance(step)

                result = {
                    "team_name": team_name,
                    "repo_url": repo_url,
                    "member_handles": members,
                    "risk_score": analysis["risk_score"],
                    "status": analysis["status"],
                    "triggered_flags": analysis["triggered_flags"],
                    "ai_summary": ai_summary,
                    "raw_metrics": analysis["raw_metrics"],
                }
                results.append(result)

                if args.verbose:
                    print_verbose_result(result)

            except Exception as exc:  # noqa: BLE001
                console.print(f"\n[bold red]Error processing '{team_name}':[/bold red] {exc}")
                if args.verbose:
                    import traceback

                    traceback.print_exc()

                results.append(
                    {
                        "team_name": team_name,
                        "repo_url": repo_url,
                        "member_handles": members,
                        "risk_score": -1,
                        "status": "ERROR",
                        "triggered_flags": [
                            {
                                "code": "FETCH_ERROR",
                                "severity": "CRITICAL",
                                "detail": str(exc),
                            }
                        ],
                        "ai_summary": "[Error during analysis — see triggered_flags]",
                        "raw_metrics": {},
                    }
                )

            finally:
                progress.advance(overall)

    # ---- Final output ----
    print_summary_table(results)

    output_path = write_report(results, args.output_dir)
    console.print(
        f"[bold green]✓[/bold green] Report written to: [bold underline]{output_path}[/bold underline]\n"
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _enrich_with_stats(
    github: GitHubClient,
    commits: list[dict],
    owner: str,
    repo_name: str,
    hackathon_start: datetime,
    hackathon_end: datetime,
    max_fetches: int,
) -> list[dict]:
    """Fetch boilerplate-filtered stats for a prioritised subset of commits.

    Priority order:
      1. Commits inside the hackathon window (critical for velocity / single-drop analysis)
      2. Commits just before the window (7-day look-back, for pre-build context)
      3. Everything else (oldest to most recent)
    """
    near_start = hackathon_start - timedelta(days=7)

    def _priority(commit: dict) -> int:
        dt = commit.get("author_date")
        if not dt:
            return 3
        if hackathon_start <= dt <= hackathon_end:
            return 0
        if near_start <= dt < hackathon_start:
            return 1
        return 2

    prioritised = sorted(range(len(commits)), key=lambda i: _priority(commits[i]))
    to_fetch = prioritised[:max_fetches]

    for idx in to_fetch:
        sha = commits[idx]["sha"]
        detail = github.fetch_commit_detail(owner, repo_name, sha)
        if detail:
            commits[idx]["additions"] = detail.get("additions")
            commits[idx]["deletions"] = detail.get("deletions")
            commits[idx]["changed_files"] = detail.get("changed_files", [])
        # Brief pause to stay well under GitHub's secondary rate limit
        time.sleep(0.25)

    return commits

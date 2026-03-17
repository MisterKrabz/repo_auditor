"""Assemble the final JSON report and render the console summary table."""

import json
from datetime import datetime, timezone
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

_STATUS_COLOR = {
    "PASS": "green",
    "REVIEW": "yellow",
    "HIGHLY SUSPICIOUS": "red",
    "ERROR": "magenta",
}

_SEVERITY_COLOR = {
    "CRITICAL": "bold red",
    "HIGH": "red",
    "MEDIUM": "yellow",
    "LOW": "cyan",
}


# ---------------------------------------------------------------------------
# File output
# ---------------------------------------------------------------------------


def write_report(results: list[dict], output_dir: str = ".") -> str:
    """Serialise *results* to a timestamped JSON file.

    Returns the absolute path of the written file.
    """
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"audit_results_{date_str}.json"
    output_path = Path(output_dir) / filename
    output_path.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_teams": len(results),
        "summary": {
            "pass": sum(1 for r in results if r.get("status") == "PASS"),
            "review": sum(1 for r in results if r.get("status") == "REVIEW"),
            "highly_suspicious": sum(
                1 for r in results if r.get("status") == "HIGHLY SUSPICIOUS"
            ),
            "error": sum(1 for r in results if r.get("status") == "ERROR"),
        },
        "results": results,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=_json_default)

    return str(output_path.resolve())


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------


def print_summary_table(results: list[dict]) -> None:
    """Print a formatted results table to stdout."""
    table = Table(
        title="[bold cyan]Repo Auditor — Final Results[/bold cyan]",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
        border_style="bright_black",
        expand=False,
    )

    table.add_column("Team", style="bold white", min_width=18)
    table.add_column("Status", min_width=20)
    table.add_column("Score", justify="center", min_width=8)
    table.add_column("Triggered Flags", min_width=36)

    for r in results:
        status = r.get("status", "ERROR")
        color = _STATUS_COLOR.get(status, "white")
        score = r.get("risk_score", -1)

        if score >= 60:
            score_color = "red"
        elif score >= 30:
            score_color = "yellow"
        elif score >= 0:
            score_color = "green"
        else:
            score_color = "magenta"

        score_cell = f"[{score_color}]{score}/100[/{score_color}]" if score >= 0 else "N/A"

        flag_codes = [f["code"] for f in r.get("triggered_flags", [])]
        flags_cell = ", ".join(flag_codes) if flag_codes else "[dim]none[/dim]"

        table.add_row(
            r.get("team_name", "Unknown"),
            f"[{color}]{status}[/{color}]",
            score_cell,
            flags_cell,
        )

    console.print()
    console.print(table)
    console.print()


def print_verbose_result(result: dict) -> None:
    """Print a detailed breakdown for a single team result."""
    status = result.get("status", "ERROR")
    score = result.get("risk_score", -1)
    color = _STATUS_COLOR.get(status, "white")

    title = (
        f"[bold]{result.get('team_name', 'Unknown')}[/bold]  "
        f"[{color}]{status}[/{color}]  "
        f"(score: {score}/100)"
    )

    lines: list[str] = []

    # Repo info
    lines.append(f"[dim]Repository:[/dim] {result.get('repo_url', 'N/A')}")
    lines.append(
        f"[dim]Members:[/dim]   {', '.join(f'@{m}' for m in result.get('member_handles', []))}"
    )
    lines.append("")

    # Raw metrics
    metrics = result.get("raw_metrics", {})
    if metrics:
        lines.append("[bold]Metrics[/bold]")
        lines.append(f"  Total commits       : {metrics.get('total_commits', 'N/A')}")
        lines.append(f"  In-window commits   : {metrics.get('in_window_commits', 'N/A')}")
        lines.append(f"  Pre-event commits   : {metrics.get('pre_hackathon_commits', 'N/A')}")
        lines.append(f"  Total lines added   : {metrics.get('total_lines_added', 'N/A'):,}"
                     if isinstance(metrics.get("total_lines_added"), int) else
                     f"  Total lines added   : {metrics.get('total_lines_added', 'N/A')}")
        lines.append(f"  Event duration (hrs): {metrics.get('hackathon_duration_hours', 'N/A')}")

        balance = metrics.get("team_balance", {})
        if balance:
            lines.append("")
            lines.append("[bold]Team Balance[/bold]")
            for member, v in balance.items():
                bar = _mini_bar(v.get("percentage", 0))
                lines.append(
                    f"  @{member:<20} {bar} "
                    f"{v.get('commits', 0)} commits  "
                    f"{v.get('additions', 0):,} lines  "
                    f"({v.get('percentage', 0):.1f}%)"
                )

    # Flags
    flags = result.get("triggered_flags", [])
    if flags:
        lines.append("")
        lines.append("[bold]Triggered Flags[/bold]")
        for flag in flags:
            sev = flag.get("severity", "LOW")
            sev_color = _SEVERITY_COLOR.get(sev, "white")
            lines.append(f"  [{sev_color}][{sev}][/{sev_color}] {flag.get('code', '?')}")
            lines.append(f"    {flag.get('detail', '')}")
    else:
        lines.append("")
        lines.append("[green]No flags triggered — submission appears clean.[/green]")

    # AI summary
    ai_summary = result.get("ai_summary", "")
    if ai_summary and ai_summary not in ("[skipped]", "[Error during analysis]"):
        lines.append("")
        lines.append("[bold]AI Summary[/bold]")
        lines.append(f"  [italic]{ai_summary}[/italic]")

    panel_text = "\n".join(lines)
    console.print(Panel(panel_text, title=title, border_style=color, expand=False))
    console.print()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mini_bar(pct: float, width: int = 15) -> str:
    filled = round(pct / 100 * width)
    return "[cyan]" + "█" * filled + "░" * (width - filled) + "[/cyan]"


def _json_default(obj):
    """JSON serialiser for types not natively supported."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serialisable")

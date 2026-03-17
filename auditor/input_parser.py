"""Parse CSV and JSON team submission files."""

import csv
import json
from pathlib import Path


class InputError(Exception):
    pass


def parse_teams(file_path: str) -> list[dict]:
    """Auto-detect format and parse team submissions from a CSV or JSON file.

    Expected CSV columns: team_name, repo_url, member_handles
    Expected JSON structure: list of objects with those same keys.

    member_handles can be a comma-separated string ("alice,bob") or a JSON array.

    Returns a list of dicts: {team_name, repo_url, member_handles: list[str]}
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {file_path}")

    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _parse_csv(path)
    elif suffix == ".json":
        return _parse_json(path)
    else:
        raise InputError(
            f"Unsupported file format '{suffix}'. Use a .csv or .json file."
        )


def _normalize_handles(raw) -> list[str]:
    """Convert handles to a clean list of lowercase strings, stripping leading @."""
    if isinstance(raw, list):
        handles = raw
    elif isinstance(raw, str):
        handles = [h for h in raw.split(",") if h.strip()]
    else:
        handles = []
    return [h.strip().lstrip("@").lower() for h in handles if h.strip()]


def _validate_entry(entry: dict, index: int) -> dict:
    for field in ("team_name", "repo_url", "member_handles"):
        if field not in entry:
            raise InputError(
                f"Entry #{index + 1} is missing required field '{field}'."
            )
    if not entry["repo_url"].startswith("https://github.com/"):
        raise InputError(
            f"Entry #{index + 1} has an invalid repo_url '{entry['repo_url']}'. "
            "Must be a full GitHub HTTPS URL."
        )
    handles = _normalize_handles(entry["member_handles"])
    if not handles:
        raise InputError(
            f"Entry #{index + 1} ('{entry['team_name']}') has no member handles."
        )
    return {
        "team_name": entry["team_name"].strip(),
        "repo_url": entry["repo_url"].strip().rstrip("/").removesuffix(".git"),
        "member_handles": handles,
    }


def _parse_csv(path: Path) -> list[dict]:
    teams = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise InputError("CSV file appears to be empty.")
        for i, row in enumerate(reader):
            teams.append(_validate_entry(dict(row), i))
    if not teams:
        raise InputError("CSV file contains no data rows.")
    return teams


def _parse_json(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise InputError(f"Invalid JSON: {e}")

    if not isinstance(data, list):
        raise InputError("JSON file must contain a top-level array of team objects.")
    if not data:
        raise InputError("JSON file contains an empty array.")

    return [_validate_entry(entry, i) for i, entry in enumerate(data)]

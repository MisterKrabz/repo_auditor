"""Environment variable loading and validation."""

import os
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()


class ConfigError(Exception):
    pass


def load_config() -> dict:
    """Load, parse, and validate all environment variables.

    Returns a dict with typed, ready-to-use config values.
    Raises ConfigError with a clear message on any missing or invalid input.
    """
    github_token = os.getenv("GITHUB_TOKEN", "").strip()
    if not github_token:
        raise ConfigError(
            "GITHUB_TOKEN is not set. Add a GitHub Classic PAT to your .env file."
        )

    start_raw = os.getenv("HACKATHON_START_TIME", "").strip().strip('"').strip("'")
    end_raw = os.getenv("HACKATHON_END_TIME", "").strip().strip('"').strip("'")

    if not start_raw or not end_raw:
        raise ConfigError(
            "HACKATHON_START_TIME and HACKATHON_END_TIME must be set in your .env file. "
            "Use ISO 8601 format, e.g. 2024-10-25T18:00:00Z"
        )

    try:
        hackathon_start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
    except ValueError:
        raise ConfigError(
            f"HACKATHON_START_TIME '{start_raw}' is not valid ISO 8601. "
            "Example: 2024-10-25T18:00:00Z"
        )

    try:
        hackathon_end = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
    except ValueError:
        raise ConfigError(
            f"HACKATHON_END_TIME '{end_raw}' is not valid ISO 8601. "
            "Example: 2024-10-27T12:00:00Z"
        )

    if hackathon_start >= hackathon_end:
        raise ConfigError(
            f"HACKATHON_START_TIME ({hackathon_start}) must be strictly before "
            f"HACKATHON_END_TIME ({hackathon_end})."
        )

    try:
        api_sleep_delay = float(os.getenv("API_SLEEP_DELAY", "4.5"))
    except ValueError:
        raise ConfigError("API_SLEEP_DELAY must be a number (e.g. 4.5)")

    try:
        min_commits_per_member = int(os.getenv("MIN_COMMITS_PER_MEMBER", "1"))
    except ValueError:
        raise ConfigError("MIN_COMMITS_PER_MEMBER must be an integer (e.g. 1)")

    return {
        "github_token": github_token,
        "gemini_api_key": os.getenv("GEMINI_API_KEY", "").strip() or None,
        "hackathon_start": hackathon_start,
        "hackathon_end": hackathon_end,
        "api_sleep_delay": api_sleep_delay,
        "min_commits_per_member": min_commits_per_member,
    }

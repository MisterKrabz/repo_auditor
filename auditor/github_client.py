"""GitHub REST API v3 client with rate-limit handling and boilerplate filtering."""

import time
from datetime import datetime
from urllib.parse import urlparse

import requests

# ---------------------------------------------------------------------------
# Boilerplate / generated-file detection
# ---------------------------------------------------------------------------

_BOILERPLATE_FILENAMES = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "npm-shrinkwrap.json",
        "composer.lock",
        "Gemfile.lock",
        "poetry.lock",
        "Pipfile.lock",
        "go.sum",
        "cargo.lock",
        "packages.lock.json",
    }
)

_BOILERPLATE_DIRS = frozenset(
    {
        "node_modules",
        "dist",
        "build",
        "out",
        ".next",
        ".nuxt",
        "__pycache__",
        "venv",
        ".venv",
        ".tox",
        "vendor",
        ".gradle",
        "target",
    }
)

# Suffixes that indicate minified / compiled / map files
_BOILERPLATE_SUFFIXES = (".min.js", ".min.css", ".map", ".lock", ".sum")

# Extensions that are typically dataset / binary files, not source code
_BOILERPLATE_EXTENSIONS = frozenset({".csv", ".tsv", ".xlsx", ".parquet", ".db", ".sqlite"})


def is_boilerplate(filepath: str) -> bool:
    """Return True if *filepath* represents auto-generated or boilerplate content."""
    parts = filepath.replace("\\", "/").split("/")

    # Any parent directory is a boilerplate dir
    for part in parts[:-1]:
        if part in _BOILERPLATE_DIRS:
            return True

    filename = parts[-1]

    # Exact filename match
    if filename in _BOILERPLATE_FILENAMES:
        return True

    # Suffix patterns like .min.js
    for suffix in _BOILERPLATE_SUFFIXES:
        if filename.endswith(suffix):
            return True

    # Extension-only check
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
        if ext in _BOILERPLATE_EXTENSIONS:
            return True

    return False


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def parse_repo_url(url: str) -> tuple[str, str]:
    """Extract *(owner, repo_name)* from any GitHub URL variant.

    Handles HTTPS (``https://github.com/owner/repo``) and SSH
    (``git@github.com:owner/repo.git``) formats.
    """
    url = url.strip()

    # SSH format: git@github.com:owner/repo.git
    if url.startswith("git@github.com:"):
        path = url[len("git@github.com:"):]
        parts = path.removesuffix(".git").split("/")
        if len(parts) >= 2:
            return parts[0], parts[1]

    # HTTPS format
    url = url.rstrip("/").removesuffix(".git")
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]
    if len(path_parts) >= 2:
        return path_parts[0], path_parts[1]

    raise ValueError(f"Cannot parse GitHub URL: {url!r}")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class GitHubClient:
    BASE_URL = "https://api.github.com"

    def __init__(self, token: str) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make a request, transparently handling rate limits and retries."""
        while True:
            resp = self._session.request(method, url, timeout=30, **kwargs)

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                time.sleep(retry_after)
                continue

            # Primary rate-limit exhausted
            if resp.status_code == 403:
                remaining = resp.headers.get("X-RateLimit-Remaining", "1")
                if remaining == "0":
                    reset_ts = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
                    sleep_secs = max(reset_ts - time.time() + 2, 5)
                    time.sleep(sleep_secs)
                    continue

            return resp

    def _get_all_pages(self, url: str, params: dict | None = None) -> list:
        """Fetch every page of a paginated API endpoint."""
        results: list = []
        params = {**(params or {}), "per_page": 100}

        while url:
            resp = self._request("GET", url, params=params)
            if not resp.ok:
                break

            data = resp.json()
            if not isinstance(data, list):
                break

            results.extend(data)

            if len(data) < 100:
                break

            # Follow GitHub's Link header for the next page
            next_url = None
            for part in resp.headers.get("Link", "").split(","):
                if 'rel="next"' in part:
                    next_url = part.split(";")[0].strip().strip("<>")
                    break

            url = next_url
            params = {}  # Already encoded in next_url

        return results

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    def fetch_repo(self, owner: str, repo_name: str) -> dict:
        """Fetch repository metadata in a normalised dict."""
        url = f"{self.BASE_URL}/repos/{owner}/{repo_name}"
        resp = self._request("GET", url)
        resp.raise_for_status()
        data = resp.json()

        license_info = data.get("license") or {}
        parent = data.get("parent") or {}

        return {
            "is_fork": data.get("fork", False),
            "fork_parent": parent.get("full_name"),
            "created_at": _parse_dt(data.get("created_at")),
            "license_name": license_info.get("name"),
            "license_spdx": license_info.get("spdx_id"),
            "default_branch": data.get("default_branch", "main"),
            "stars": data.get("stargazers_count", 0),
            "size_kb": data.get("size", 0),
            "description": data.get("description") or "",
        }

    def fetch_commits(self, owner: str, repo_name: str, max_count: int = 300) -> list[dict]:
        """Fetch up to *max_count* commits (basic info — no per-file stats).

        Returns commits in reverse-chronological order (newest first), exactly
        as the GitHub API delivers them.
        """
        url = f"{self.BASE_URL}/repos/{owner}/{repo_name}/commits"
        raw = self._get_all_pages(url)[:max_count]

        commits = []
        for c in raw:
            git_author = c.get("commit", {}).get("author") or {}
            git_committer = c.get("commit", {}).get("committer") or {}
            github_author = c.get("author") or {}

            commits.append(
                {
                    "sha": c["sha"],
                    # GitHub login (None when the email isn't linked to an account)
                    "author_login": github_author.get("login"),
                    "author_name": git_author.get("name", ""),
                    "author_email": git_author.get("email", ""),
                    "author_date": _parse_dt(git_author.get("date")),
                    # committer.date is the best proxy for "push time" available
                    # via the standard commits list endpoint
                    "committer_date": _parse_dt(git_committer.get("date")),
                    "message": (c.get("commit") or {}).get("message", ""),
                    # Populated later by fetch_commit_detail
                    "additions": None,
                    "deletions": None,
                    "changed_files": [],
                }
            )

        return commits

    def fetch_commit_detail(self, owner: str, repo_name: str, sha: str) -> dict:
        """Fetch a single commit's stats and file list, filtering boilerplate.

        Returns additions/deletions that exclude auto-generated files so that
        velocity metrics are not skewed by committing node_modules or lock files.
        """
        url = f"{self.BASE_URL}/repos/{owner}/{repo_name}/commits/{sha}"
        resp = self._request("GET", url)
        if not resp.ok:
            return {}

        data = resp.json()
        raw_stats = data.get("stats") or {}
        files = data.get("files") or []

        real_additions = 0
        real_deletions = 0
        file_names: list[str] = []

        for f in files:
            fname = f.get("filename", "")
            file_names.append(fname)
            if not is_boilerplate(fname):
                real_additions += f.get("additions", 0)
                real_deletions += f.get("deletions", 0)

        return {
            "sha": sha,
            "additions": real_additions,
            "deletions": real_deletions,
            "raw_additions": raw_stats.get("additions", 0),
            "raw_deletions": raw_stats.get("deletions", 0),
            "changed_files": file_names,
        }

    def fetch_contributor_stats(
        self, owner: str, repo_name: str, retries: int = 6
    ) -> list[dict]:
        """Fetch aggregate per-contributor statistics for the entire repo history.

        The endpoint may return HTTP 202 while GitHub computes the stats;
        this method retries up to *retries* times with a 3-second back-off.

        Each returned dict contains:
            login, total_commits, total_additions, total_deletions, weeks
        """
        url = f"{self.BASE_URL}/repos/{owner}/{repo_name}/stats/contributors"

        for attempt in range(retries):
            resp = self._request("GET", url)

            if resp.status_code == 200:
                data = resp.json()
                if not isinstance(data, list):
                    return []

                result: list[dict] = []
                for entry in data:
                    author = entry.get("author") or {}
                    login = author.get("login", "")
                    weeks = entry.get("weeks") or []
                    result.append(
                        {
                            "login": login,
                            "total_commits": entry.get("total", 0),
                            "total_additions": sum(w.get("a", 0) for w in weeks),
                            "total_deletions": sum(w.get("d", 0) for w in weeks),
                            "weeks": weeks,
                        }
                    )
                return result

            elif resp.status_code == 202:
                if attempt < retries - 1:
                    time.sleep(3)
                continue

            else:
                break

        return []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_dt(s: str | None) -> datetime | None:
    """Parse an ISO 8601 string into a timezone-aware datetime, or return None."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None

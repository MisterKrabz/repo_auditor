# Repo Auditor

An AI-powered Python CLI agent that evaluates hackathon GitHub submissions for authenticity. It distinguishes between legitimate AI-assisted development ("vibe coding"), pre-built projects, and plagiarism.

## How It Works

The auditor evaluates every repository against three behavioural profiles:

| Profile | Label | Description |
|---|---|---|
| A | ✅ Vibe Coding | Large AI-generated commits, both authored *and* pushed inside the event window, with iterative follow-up commits |
| B | ⚠️ Pre-building | `git author_date` before the hackathon start, or a single massive "perfect drop" commit with no debugging follow-ups |
| C | 🚨 Plagiarism | Repository is a fork, or contains old commits from authors not on the team |

It also checks team contributions (presence of every member, dominant-contributor imbalance).

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `GITHUB_TOKEN` | GitHub Classic PAT (needs `repo` read access) |
| `GEMINI_API_KEY` | Google Gemini API key |
| `HACKATHON_START_TIME` | ISO 8601 start, e.g. `2024-10-25T18:00:00Z` |
| `HACKATHON_END_TIME` | ISO 8601 end, e.g. `2024-10-27T12:00:00Z` |
| `API_SLEEP_DELAY` | Seconds to sleep between Gemini calls (default `4.5` for free-tier 15 RPM) |
| `MIN_COMMITS_PER_MEMBER` | Minimum commits expected per declared team member (default `1`) |

### 3. Create your teams file

**CSV format** (`teams.csv`):
```csv
team_name,repo_url,member_handles
Team Alpha,https://github.com/alice/project,"alice,bob"
Team Beta,https://github.com/charlie/app,"charlie,diana,eve"
```

**JSON format** (`teams.json`):
```json
[
  {
    "team_name": "Team Alpha",
    "repo_url": "https://github.com/alice/project",
    "member_handles": ["alice", "bob"]
  }
]
```

## Usage

```bash
# Basic run
python main.py teams.csv

# With verbose per-team breakdown
python main.py teams.csv --verbose

# Save report to a specific directory
python main.py teams.json --output-dir ./reports

# Skip AI summary generation (faster, saves Gemini quota)
python main.py teams.csv --skip-ai

# Limit commits fetched (reduces GitHub API calls)
python main.py teams.csv --max-commits 100 --max-stats 30
```

## Output

The agent writes a `audit_results_YYYYMMDD_HHMMSS.json` file to your output directory. Each result contains:

```json
{
  "team_name": "Team Alpha",
  "repo_url": "https://github.com/alice/project",
  "member_handles": ["alice", "bob"],
  "risk_score": 75,
  "status": "HIGHLY SUSPICIOUS",
  "triggered_flags": [
    {
      "code": "PRE_HACKATHON_AUTHOR_DATE",
      "severity": "HIGH",
      "detail": "18/22 commits (82%) have git author_date before hackathon start..."
    }
  ],
  "ai_summary": "The repository shows strong indicators of pre-built code...",
  "raw_metrics": {
    "total_commits": 22,
    "in_window_commits": 4,
    "pre_hackathon_commits": 18,
    "total_lines_added": 4821,
    "hackathon_duration_hours": 42.0,
    "team_balance": {
      "alice": { "commits": 20, "additions": 4600, "percentage": 95.4 },
      "bob":   { "commits": 2,  "additions": 221,  "percentage": 4.6  }
    }
  }
}
```

### Status levels

| Status | Risk Score | Meaning |
|---|---|---|
| `PASS` | 0–29 | No significant red flags detected |
| `REVIEW` | 30–59 | Some indicators warrant manual review |
| `HIGHLY SUSPICIOUS` | 60–100 | Strong evidence of rule-breaking |

### Flag codes

| Code | Severity | Profile |
|---|---|---|
| `IS_FORK` | CRITICAL | C — Plagiarism |
| `OLD_NONTEAM_COMMITS` | HIGH | C — Plagiarism |
| `PRE_HACKATHON_AUTHOR_DATE` | HIGH/MEDIUM | B — Pre-building |
| `PRE_HACKATHON_PUSH` | MEDIUM | B — Pre-building |
| `BULK_PRE_HACKATHON_WORK` | HIGH | B — Pre-building |
| `SINGLE_MASSIVE_COMMIT` | HIGH | B — Pre-building |
| `MISSING_CONTRIBUTOR` | LOW | Team |
| `DOMINANT_SINGLE_CONTRIBUTOR` | MEDIUM | Team |

## Notes on Boilerplate Filtering

Line counts automatically exclude the following from velocity and stats calculations to avoid false positives:

- `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `go.sum`, etc.
- Directories: `node_modules/`, `dist/`, `build/`, `venv/`, `__pycache__/`, etc.
- Minified files: `*.min.js`, `*.min.css`, `*.map`
- Dataset files: `*.csv`, `*.tsv`, `*.xlsx`, `*.parquet`

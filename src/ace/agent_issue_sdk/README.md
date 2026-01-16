# Agent Issue SDK

Create issues in the **DITC TODO** org project directly from any repository's agent code.

## Quick Start

```python
from ace.agent_issue_sdk import IssueCreator, IssueContent

# Token automatically fetched from GCP Secret Manager using appforge-creds.json
creator = IssueCreator()

issue = IssueContent(
    title="Add caching to API",
    target_repository="api-gateway",
    description="Implement Redis caching for performance",
    acceptance_criteria=[
        "Redis client configured",
        "Cache decorator implemented",
        "Tests pass",
    ],
)

result = await creator.create_issue(issue, difficulty="medium")
print(f"Created: {result['html_url']}")
```

## Installation

Using `uv` (recommended):

```bash
uv add git+https://github.com/Day-in-the-Country-LLC/appforge-poc.git
```

Or with `pip`:

```bash
pip install git+https://github.com/Day-in-the-Country-LLC/appforge-poc.git
```

## Setup

1. Ensure a credentials file matching `<project>-creds.json` exists in your repo root (e.g., `appforge-creds.json`)

2. Ensure your GCP project has the GitHub token in Secret Manager:

```bash
gcloud secrets create github-control-api-key --data-file=- <<< "ghp_your_token"
```

## Documentation

See `docs/agent-issue-sdk.md` for complete documentation, examples, and API reference.

## Use Cases

- **Escalate work** - Agent discovers follow-up tasks
- **Request clarification** - Agent needs human input
- **Report bugs** - Agent finds issues during execution
- **Create subtasks** - Break down complex work

## Features

- Async and sync APIs
- Automatic GitHub token retrieval from GCP Secret Manager using credentials file
- Automatic label management (`agent`, `difficulty:*`)
- Formatted issue bodies
- Support for implementation notes and related issues
- Full error handling

## Credentials

The SDK automatically detects and uses a credentials file matching `<project>-creds.json` in your repo root to:
1. Extract the GCP project ID
2. Authenticate with GCP Secret Manager
3. Fetch the GitHub token

Examples: `appforge-creds.json`, `frontend-creds.json`, `backend-creds.json`

## License

Same as appforge-poc

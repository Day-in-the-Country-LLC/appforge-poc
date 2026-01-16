# Agent Issue SDK

Create issues in the **DITC TODO** org project directly from any repository's agent code.

## Quick Start

```python
from ace.agent_issue_sdk import IssueCreator, IssueContent

# Token automatically fetched from GCP Secret Manager
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

1. Ensure your GCP project has the GitHub token in Secret Manager:

```bash
gcloud secrets create github-control-api-key --data-file=- <<< "ghp_your_token"
```

2. Set the GCP project environment variable:

```bash
export GOOGLE_CLOUD_PROJECT=your-gcp-project-id
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
- Automatic GitHub token retrieval from GCP Secret Manager
- Automatic label management (`agent`, `difficulty:*`)
- Formatted issue bodies
- Support for implementation notes and related issues
- Full error handling

## Environment

```bash
export GOOGLE_CLOUD_PROJECT=your-gcp-project-id
```

The SDK uses Application Default Credentials (ADC) for GCP authentication.

## License

Same as appforge-poc

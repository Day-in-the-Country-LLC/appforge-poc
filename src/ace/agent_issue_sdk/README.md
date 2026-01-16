# Agent Issue SDK

Create issues in the **DITC TODO** org project directly from any repository's agent code.

## Quick Start

```python
from ace.agent_issue_sdk import IssueCreator, IssueContent
import os

creator = IssueCreator(github_token=os.getenv("GITHUB_TOKEN"))

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

## Documentation

See `docs/agent-issue-sdk.md` for complete documentation, examples, and API reference.

## Use Cases

- **Escalate work** - Agent discovers follow-up tasks
- **Request clarification** - Agent needs human input
- **Report bugs** - Agent finds issues during execution
- **Create subtasks** - Break down complex work

## Features

- Async and sync APIs
- Automatic label management (`agent`, `difficulty:*`)
- Formatted issue bodies
- Support for implementation notes and related issues
- Full error handling

## Environment

```bash
export GITHUB_TOKEN=ghp_your_token_here
```

## License

Same as appforge-poc

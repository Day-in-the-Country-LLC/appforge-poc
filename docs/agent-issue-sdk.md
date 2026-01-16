# Agent Issue SDK

The Agent Issue SDK enables coding agents in any repository to create issues directly in the **DITC TODO** org project. This allows agents to escalate work, request clarification, or create follow-up tasks programmatically.

## Installation

### For Agents in Your Repos

1. **Install the package** in your agent's environment using `uv`:

```bash
uv add git+https://github.com/Day-in-the-Country-LLC/appforge-poc.git#egg=ace[agent-issue-sdk]
```

Or with `pip`:

```bash
pip install git+https://github.com/Day-in-the-Country-LLC/appforge-poc.git#egg=ace[agent-issue-sdk]
```

Or add to your `pyproject.toml`:

```toml
[project]
dependencies = [
    "ace[agent-issue-sdk] @ git+https://github.com/Day-in-the-Country-LLC/appforge-poc.git",
]
```

2. **Set environment variable** with your GitHub token:

```bash
export GITHUB_TOKEN=ghp_your_token_here
```

## Quick Start

### Async Usage (Recommended)

```python
import os
from ace.agent_issue_sdk import IssueCreator, IssueContent

# Initialize
creator = IssueCreator(
    github_token=os.getenv("GITHUB_TOKEN"),
    github_org="Day-in-the-Country-LLC",
    project_name="DITC TODO",
)

# Create issue content
issue = IssueContent(
    title="Add caching to API endpoints",
    target_repository="api-gateway",
    description="Implement Redis caching for frequently accessed endpoints to reduce latency.",
    acceptance_criteria=[
        "Redis client configured in app",
        "Cache decorator implemented for GET endpoints",
        "Cache invalidation on POST/PUT/DELETE",
        "Cache hit/miss metrics logged",
        "Tests pass with 80%+ coverage",
    ],
    implementation_notes="Use existing cache decorator pattern from utils/cache.py",
    related_issues=["#456", "#789"],
)

# Create the issue
issue_data = await creator.create_issue(
    content=issue,
    difficulty="medium",
    labels=["performance", "backend"],
)

print(f"Issue created: {issue_data['html_url']}")
```

### Synchronous Usage

```python
import os
from ace.agent_issue_sdk import IssueCreatorSync, IssueContent

# Initialize
creator = IssueCreatorSync(
    github_token=os.getenv("GITHUB_TOKEN"),
)

# Create issue (same as async)
issue = IssueContent(...)

# Create the issue (blocking call)
issue_data = creator.create_issue(
    content=issue,
    difficulty="medium",
)
```

## API Reference

### IssueCreator (Async)

```python
class IssueCreator:
    def __init__(
        self,
        github_token: str,
        github_org: str = "Day-in-the-Country-LLC",
        project_name: str = "DITC TODO",
        api_url: str = "https://api.github.com",
    )

    async def create_issue(
        self,
        content: IssueContent,
        difficulty: str = "medium",
        labels: Optional[list[str]] = None,
    ) -> dict
```

### IssueCreatorSync (Synchronous)

Same API as `IssueCreator` but `create_issue()` is synchronous (blocking).

### IssueContent

```python
@dataclass
class IssueContent:
    title: str                                    # Issue title
    target_repository: str                        # Target repo name
    description: str                              # What needs to be done
    acceptance_criteria: list[str]                # Testable requirements
    implementation_notes: Optional[str] = None    # Optional implementation guidance
    related_issues: Optional[list[str]] = None    # Optional related issue links
```

## Use Cases

### 1. Agent Needs Clarification

```python
issue = IssueContent(
    title="Clarification needed: API response format",
    target_repository="api-gateway",
    description="Agent encountered ambiguous specification for user profile endpoint response.",
    acceptance_criteria=[
        "Clarify if profile.avatar should be URL or base64",
        "Specify required vs optional fields",
        "Provide example response JSON",
    ],
)

await creator.create_issue(issue, difficulty="easy")
```

### 2. Agent Discovers Follow-up Work

```python
issue = IssueContent(
    title="Performance: Optimize database queries in user service",
    target_repository="user-service",
    description="While implementing user profile feature, discovered N+1 query problem in user list endpoint.",
    acceptance_criteria=[
        "Profile queries optimized with eager loading",
        "Query count reduced from N+1 to 1",
        "Performance test added",
        "Latency < 100ms for 1000 users",
    ],
    related_issues=["#123"],  # Link to original issue
)

await creator.create_issue(issue, difficulty="medium")
```

### 3. Agent Identifies Bug

```python
issue = IssueContent(
    title="Bug: Authentication fails with special characters in password",
    target_repository="auth-service",
    description="During testing, discovered that passwords with special characters (!, @, #) fail authentication.",
    acceptance_criteria=[
        "Reproduce bug with password: 'P@ssw0rd!'",
        "Fix character encoding in password hashing",
        "Add test cases for special characters",
        "All auth tests pass",
    ],
)

await creator.create_issue(issue, difficulty="easy")
```

## Difficulty Levels

- **easy** - Uses Codex (gpt-5.1-codex)
- **medium** - Uses Claude Haiku (claude-haiku-4-5)
- **hard** - Uses Claude Opus (claude-opus-4-5)

Choose based on complexity of the follow-up work.

## Labels

Issues are automatically labeled with:
- `agent` - Marks as agent-created
- `difficulty:{level}` - Based on difficulty parameter

Additional labels can be passed:

```python
await creator.create_issue(
    issue,
    difficulty="medium",
    labels=["performance", "backend", "urgent"],
)
```

## Error Handling

```python
from ace.agent_issue_sdk import IssueCreator, IssueContent

try:
    issue_data = await creator.create_issue(issue, difficulty="invalid")
except ValueError as e:
    print(f"Invalid difficulty: {e}")

try:
    issue_data = await creator.create_issue(issue)
except httpx.HTTPError as e:
    print(f"GitHub API error: {e}")
```

## Best Practices

1. **Be specific** - Include exact error messages, file paths, line numbers
2. **Provide context** - Link to related issues and design docs
3. **Make criteria testable** - Use measurable, specific requirements
4. **Use appropriate difficulty** - Don't overestimate or underestimate
5. **Include implementation notes** - Help the next agent succeed
6. **Add relevant labels** - Use labels like `bug`, `performance`, `security`

## Example: Complete Agent Workflow

```python
import os
from ace.agent_issue_sdk import IssueCreator, IssueContent

async def handle_agent_work():
    creator = IssueCreator(github_token=os.getenv("GITHUB_TOKEN"))
    
    try:
        # Do the main work
        result = await execute_main_task()
        
        # If successful, create follow-up issue
        if result.has_follow_up:
            follow_up = IssueContent(
                title=result.follow_up_title,
                target_repository=result.target_repo,
                description=result.follow_up_description,
                acceptance_criteria=result.criteria,
                related_issues=[f"#{result.original_issue_number}"],
            )
            
            issue_data = await creator.create_issue(
                follow_up,
                difficulty=result.difficulty,
                labels=result.labels,
            )
            
            print(f"Follow-up issue created: {issue_data['html_url']}")
            
    except Exception as e:
        # Create issue for the error
        error_issue = IssueContent(
            title=f"Error in {result.task_name}: {str(e)[:50]}",
            target_repository=result.target_repo,
            description=f"Agent encountered error:\n\n```\n{str(e)}\n```",
            acceptance_criteria=[
                "Investigate root cause",
                "Fix the issue",
                "Add test to prevent regression",
            ],
        )
        
        await creator.create_issue(error_issue, difficulty="medium")
```

## Environment Variables

- `GITHUB_TOKEN` - GitHub Personal Access Token (required)
- `GITHUB_ORG` - GitHub organization (optional, default: Day-in-the-Country-LLC)
- `GITHUB_PROJECT_NAME` - Project name (optional, default: DITC TODO)

## Troubleshooting

### "Invalid difficulty" error

```
ValueError: Invalid difficulty: hard. Must be easy, medium, or hard.
```

**Solution:** Use one of: `easy`, `medium`, `hard`

### "Authentication failed" error

```
httpx.HTTPStatusError: 401 Unauthorized
```

**Solution:** Verify `GITHUB_TOKEN` is set and has correct permissions

### Issue not appearing in project

**Possible causes:**
- Token doesn't have `repo` and `read:org` scopes
- Organization name is incorrect
- Project name is incorrect

**Solution:** Verify token scopes and org/project names in initialization

## For Developers

The SDK is in `src/ace/agent_issue_sdk/`:
- `client.py` - Main IssueCreator and IssueCreatorSync classes
- `__init__.py` - Package exports

To modify or extend, see the source code and tests in `tests/test_agent_issue_sdk.py`.

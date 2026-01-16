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

2. **Ensure credentials file exists**:

The SDK automatically fetches the GitHub token from GCP Secret Manager using a credentials file matching the pattern `<project>-creds.json` in your repo root (e.g., `appforge-creds.json`, `frontend-creds.json`). This file should already exist in your repo.

## Quick Start

### Async Usage (Recommended)

```python
from ace.agent_issue_sdk import IssueCreator, IssueContent

# Initialize (fetches GitHub token from GCP Secret Manager using appforge-creds.json)
creator = IssueCreator()

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
from ace.agent_issue_sdk import IssueCreatorSync, IssueContent

# Initialize (fetches GitHub token from GCP Secret Manager using appforge-creds.json)
creator = IssueCreatorSync()

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
        github_org: str = "Day-in-the-Country-LLC",
        project_name: str = "DITC TODO",
        api_url: str = "https://api.github.com",
        credentials_file: Optional[str] = None,  # Defaults to auto-detect *-creds.json
        secret_name: str = "github-control-api-key",
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

**Note:** GitHub token is automatically fetched from GCP Secret Manager using the credentials file on initialization.

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

## Credentials

**Required:**
- `<project>-creds.json` - GCP service account credentials file (e.g., `appforge-creds.json`, `frontend-creds.json`)
  - Must exist in repo root
  - SDK auto-detects the file using glob pattern `*-creds.json`
  - If multiple files match, the first one is used (with a warning logged)

**GCP Secret Manager:**
- `github-control-api-key` - GitHub Personal Access Token (stored in Secret Manager)

The SDK extracts the GCP project ID from the credentials file and uses it to fetch the GitHub token from Secret Manager.

## GitHub PAT Permissions

The GitHub Personal Access Token (PAT) stored in Secret Manager as `github-control-api-key` requires the following scopes and permissions:

### Required Scopes

- **`repo`** - Full control of private repositories
  - Enables reading and writing to repository content
  - Required for creating issues and pull requests
  - Allows access to repository metadata

- **`read:org`** - Read access to organization data
  - Required to access organization projects
  - Allows reading project information and status

### Specific Permissions Enabled

With these scopes, agents can:

- **Create issues** in any repository within the organization
- **Read and update issue details** (title, description, labels, assignees)
- **Add and remove labels** from issues
- **Add comments** to issues
- **Manage issue status** in organization projects
- **Read organization project information** and structure
- **Update project fields** and issue status within projects

### Creating the PAT

To create a PAT with the required permissions:

```bash
# Using GitHub CLI
gh auth token --scopes repo,read:org

# Or manually via GitHub UI:
# 1. Go to Settings > Developer settings > Personal access tokens > Tokens (classic)
# 2. Click "Generate new token (classic)"
# 3. Select scopes:
#    - repo (all sub-options)
#    - read:org
# 4. Generate and store securely
```

### Storing in GCP Secret Manager

```bash
gcloud secrets create github-control-api-key --data-file=- <<< "ghp_your_token_here"
```

### Verification

To verify the token has correct permissions:

```bash
# Check token scopes
curl -H "Authorization: token YOUR_PAT" https://api.github.com/user

# Look for "X-OAuth-Scopes" header in response
# Should include: repo, read:org
```

### Security Best Practices

- **Rotate regularly** - Regenerate the PAT periodically (e.g., quarterly)
- **Limit scope** - Only grant `repo` and `read:org` scopes (no admin access needed)
- **Monitor usage** - Review token activity in GitHub audit logs
- **Use in Secret Manager** - Never commit the token to version control
- **Restrict to organization** - If possible, use organization-scoped tokens

## Troubleshooting

### "Credentials file not found" error

```
FileNotFoundError: No credentials file found matching pattern '*-creds.json'. Ensure a file like 'project-creds.json' exists in the repo root.
```

**Solution:** Ensure a credentials file matching `<project>-creds.json` exists in your repo root. Examples:
- `appforge-creds.json`
- `frontend-creds.json`
- `backend-creds.json`

This file should be committed to your repo.

### "project_id not found in credentials file" error

```
ValueError: project_id not found in credentials file
```

**Solution:** Verify the credentials file is valid JSON and contains a `project_id` field:
```bash
cat appforge-creds.json | jq .project_id
```

### "Secret not found" error

```
google.api_core.exceptions.NotFound: 404 Secret [projects/xxx/secrets/github-control-api-key] not found
```

**Solution:** Ensure the secret exists in your GCP project:
```bash
gcloud secrets create github-control-api-key --data-file=- <<< "ghp_your_token"
```

### "Permission denied" error

```
google.api_core.exceptions.PermissionDenied: 403 Permission denied
```

**Solution:** Ensure your service account (from credentials file) has `secretmanager.secretAccessor` role:
```bash
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member=serviceAccount:SA_EMAIL \
  --role=roles/secretmanager.secretAccessor
```

### "Invalid difficulty" error

```
ValueError: Invalid difficulty: hard. Must be easy, medium, or hard.
```

**Solution:** Use one of: `easy`, `medium`, `hard`

### "Authentication failed" error

```
httpx.HTTPStatusError: 401 Unauthorized
```

**Solution:** Verify the GitHub token in Secret Manager has correct permissions (`repo` and `read:org` scopes)

## For Developers

The SDK is in `src/ace/agent_issue_sdk/`:
- `client.py` - Main IssueCreator and IssueCreatorSync classes
- `__init__.py` - Package exports

To modify or extend, see the source code and tests in `tests/test_agent_issue_sdk.py`.

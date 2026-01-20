Absolutely. Here’s a **detailed, buildable plan** for a PoC “agentic coding engine” repo that:

* uses **GitHub Issues/Projects as the ticket queue**
* lets agents interact with GitHub via the **GitHub MCP server** (no direct REST/GraphQL calls in your code) ([GitHub Docs][1])
* runs in **GCP** (Cloud Run recommended) with **all secrets in Secret Manager** ([Google Cloud Documentation][2])
* orchestrates behavior with **LangGraph / LangChain** ([LangChain Docs][3])
* supports a combo of **OpenAI Codex CLI** and “Claude coding agents” as *execution backends* (adapter pattern). ([OpenAI Developers][4])

I’m going to assume the PoC goal is: **pick up “agent-ready” tickets, do work on a branch, open PR, update ticket state, ask questions by commenting + assigning to you, then mark done.**

---

# 0) Decide the PoC scope (tight but real)

**PoC “definition of done”:**

1. You label an issue `agent:ready`.
2. Runner claims it (labels + claim comment with tmux/worktree/branch info).
3. Runner executes work (Codex or Claude backend) in an isolated workspace.
4. Runner pushes branch + opens PR.
5. Runner comments with PR link + sets `agent:done`.
6. If blocked: runner comments `BLOCKED:` + sets `agent:blocked` + assigns to you. When you reply `ANSWER:`, runner resumes.

Keep Projects v2 updates as *optional sugar*; labels are your reliable state machine.

---

# 1) Create a new repo

Repo name ideas:

* `agentic-coding-engine-poc`
* `ticket2pr-engine`
* `gh-issues-agent-runner`

Initialize with:

* Python project skeleton
* Dockerfile
* Terraform (or gcloud scripts) for Cloud Run + Secret Manager + SA
* GitHub workflow for lint/tests

---

# 2) Repo layout (opinionated and scalable)

```
agentic-coding-engine/
  README.md
  pyproject.toml
  uv.lock (or poetry.lock)   # optional, but recommended
  .python-version
  .env.example               # no secrets, just names

  docs/
    architecture.md
    github-labels-and-protocol.md
    local-dev.md
    gcp-deploy.md

  src/ace/                    # "ace" = agentic coding engine
    __init__.py

    config/
      settings.py             # reads env + Secret Manager
      logging.py

    github/
      mcp_client.py           # MCP transport + tool invocation wrapper
      issue_queue.py          # list/claim/label/comment/assign via MCP
      projects_v2.py          # optional (status field updates)

    workspaces/
      git_ops.py              # clone/worktree/branch/push
      tmux_ops.py             # optional tmux window/session mgmt
      artifact_log.py         # per-issue logs, stdout capture

    agents/
      base.py                 # interface: plan/run/respond_to_answer
      codex_cli.py            # adapter to Codex CLI :contentReference[oaicite:4]{index=4}
      claude_cli.py           # adapter to Claude agent CLI (your choice)
      policy.py               # safety: no main pushes, PR-only, etc.

    orchestration/
      graph.py                # LangGraph definition :contentReference[oaicite:5]{index=5}
      state.py                # Pydantic state types
      steps/
        fetch_candidates.py
        claim_issue.py
        hydrate_context.py
        run_agent.py
        post_updates.py
        open_pr.py
        handle_blocked.py
        mark_done.py

    runners/
      scheduler.py            # polling loop / Cloud Scheduler trigger
      worker.py               # the “do one ticket” entrypoint

  tests/
    test_state_machine.py
    test_issue_protocol.py
    test_git_ops.py

  infra/
    terraform/
      main.tf
      iam.tf
      secrets.tf
      cloudrun.tf
      scheduler.tf
    scripts/
      bootstrap_gcp.sh

  .github/
    workflows/
      ci.yml
```

Why this layout works:

* You can swap execution backends (Codex vs Claude) cleanly.
* GitHub access is encapsulated behind MCP wrappers.
* LangGraph graph stays readable.
* Infra is co-located so the repo is deployable.

---

# 3) The “ticket protocol” (put this in `docs/github-labels-and-protocol.md`)

### Labels (state machine)

* `agent:ready`
* `agent:in-progress`
* `agent:blocked`
* `agent:done`
* (optional) `agent:failed`

### Comments handshake

Agent asks:

**`BLOCKED:`**

* Questions (bullet list)
* “Reply with `ANSWER:` …”

You respond:

**`ANSWER:`**

* Your decisions

### Claim comment template (include tmux/worktree pointers)

When claiming, the runner posts one structured comment:

* agent_id
* host
* branch
* worktree_path
* tmux session/window (if applicable)
* started_at
* heartbeat policy (“updates every major step”)

This is what lets you “jump” into tmux from GitHub.

---

# 4) GitHub MCP server integration (tooling strategy)

You said: “agents interact with GitHub via its MCP server.”

That means **your Python code will talk to the GitHub MCP server**, and invoke GitHub operations as tool calls (search issues, comment, label, create PR, etc.). The server supports configuration and toolsets so you can scope permissions and reduce blast radius. ([GitHub][5])

**Key design choice for PoC:**

* Put all GitHub operations in `src/ace/github/*`
* Expose a small internal API like:

```python
class GithubQueue:
  def list_ready_issues(...) -> list[Issue]
  def claim_issue(issue) -> None
  def post_comment(issue, body) -> None
  def set_labels(issue, add=[], remove=[]) -> None
  def assign(issue, user) -> None
  def create_pr(repo, head, base, title, body) -> PullRequest
```

Under the hood those methods invoke MCP tools.

> Tip: Configure GitHub MCP toolsets to only allow what you need (issues + prs + repo contents), especially once you start letting agents use tools more directly. ([GitHub Docs][6])

---

# 5) GCP deployment model (simple + solid)

### Cloud Run as the runner

Cloud Run is a great fit for:

* webhook receiver (GitHub → your service)
* scheduled polling (Cloud Scheduler hits an endpoint)
* running “one ticket at a time” workers

### Secrets in Secret Manager (required)

You’ll store:

* GitHub MCP auth (PAT or whatever auth the MCP server needs in your deployment mode) ([GitHub Docs][7])
* OpenAI credentials (if you end up using API, but if you rely purely on Codex CLI login you may not need it)
* Claude credentials (depends on backend)
* Any repo deploy keys / GitHub App creds (if you go that route later)

Cloud Run can load secrets as env vars or mounted files. ([Google Cloud Documentation][2])
And you’ll grant the Cloud Run service account `Secret Manager Secret Accessor`. ([Google Cloud Documentation][8])

---

# 6) Runner behavior (LangGraph design)

LangGraph is perfect here because your workflow is a state machine with branches (blocked vs done vs retry). ([LangChain Docs][9])

### State (Pydantic)

Include:

* issue metadata
* repo info
* workspace paths
* chosen backend (codex/claude)
* last agent output
* blocked questions
* PR info

### Graph nodes (recommended)

1. `fetch_candidates` (issues labeled `agent:ready`)
2. `claim_issue` (labels + claim comment)
3. `hydrate_context` (pull issue body + key files + repo metadata)
4. `select_backend` (Codex vs Claude based on label/priority)
5. `run_agent` (backend adapter)
6. `evaluate_result`

   * if needs info → `handle_blocked`
   * if success → `open_pr`
   * if fail → `post_failure` (label `agent:failed`)
7. `mark_done` (label `agent:done`, comment PR link)

LangGraph docs call out workflows vs agents; you’re mostly building a workflow that uses an “agent” step inside it. ([LangChain Docs][9])

---

# 7) Workspace + branch strategy (non-negotiable safety)

Implement in `workspaces/git_ops.py`:

**Rules:**

* never commit to `main`
* always work on branch `agent/<issue#>-<slug>`
* always open PR
* (optional) require tests before PR creation

Use **git worktrees** so multiple issues can run in parallel without stomping on each other.

---

# 8) Execution backends: Codex + Claude as “adapters”

### Codex backend (Codex CLI)

Codex CLI is explicitly designed to run in a directory and read/write/run code. ([OpenAI Developers][4])
Your adapter does:

* run `codex` in agent mode in the worktree
* feed it a structured task prompt containing:

  * issue title/body
  * constraints (branch, tests, no main pushes)
  * required outputs (summary, files changed, commands run)

### Claude backend

Same pattern: wrap whatever “Claude coding agent” you choose (Claude Code / CLI / your internal agent). Keep the interface identical.

**Important PoC simplification:**

* Start with one backend (Codex CLI), get the loop working end-to-end.
* Then add Claude adapter.

---

# 9) tmux integration (optional, but you want it)

In `workspaces/tmux_ops.py`, implement:

* session per repo: `ace-<repo>`
* window per issue: `<issue#>-<slug>`
* run the backend inside that window

Then your claim comment includes tmux coordinates so you can attach and jump.

This gives you the “Gas Town feeling” without building an entire OS.

---

# 10) How tickets get into the engine (webhook + scheduler)

**You want two triggers:**

1. GitHub webhook / GitHub Action dispatch when label becomes `agent:ready`
2. Polling fallback every 1–5 minutes (Cloud Scheduler → Cloud Run) in case a webhook is missed

PoC approach:

* webhook handler stores a “work signal” in memory or kicks an immediate run
* polling endpoint scans for `agent:ready`

---

# 11) Infra steps (what you actually do)

### A) GCP bootstrap

1. Create project
2. Enable APIs: Cloud Run, Secret Manager, Cloud Build (and Scheduler if used)
3. Create service account for Cloud Run
4. Grant:

   * Secret Manager Secret Accessor ([Google Cloud Documentation][8])
   * (optional) Cloud Run Invoker (if needed)
5. Create secrets
6. Deploy Cloud Run service with secrets attached ([Google Cloud Documentation][2])

### B) GitHub

1. Create labels in repo
2. Create webhook:

   * `issues` and `issue_comment` events
   * or “label changed” equivalent (issues event covers label changes)
3. Ensure your runner can authenticate to GitHub MCP server in a headless way (PAT mode is documented for remote MCP config). ([GitHub Docs][7])

---

# 12) First milestone checklist (build order)

**Milestone 1 (1-day PoC): Labels-only, no Projects v2**

* [ ] Repo skeleton + Docker + Cloud Run deployment
* [ ] GitHub MCP client wrapper + “list issues by label”
* [ ] Claim issue (labels + claim comment)
* [ ] Dummy “agent” that writes a file + commits + opens PR
* [ ] Mark done

**Milestone 2: Blocked loop**

* [ ] Detect agent question → comment `BLOCKED:` + label blocked + assign to you
* [ ] Detect your reply `ANSWER:` → resume

**Milestone 3: Real backend**

* [ ] Codex CLI adapter executing in worktree ([OpenAI Developers][4])

**Milestone 4: tmux visibility**

* [ ] Runner spawns tmux window per issue
* [ ] Claim comment includes tmux session/window

**Milestone 5: Projects v2 sync (optional)**

* [ ] Update Project Status in parallel with labels (nice board UX) ([GitHub Docs][6])

---

# 13) What I’d standardize up front (to prevent later pain)

### Naming conventions

* Branch: `agent/<issue#>-<slug>`
* Worktree: `~/agent-hq/worktrees/<repo>/<issue#>/`
* tmux:

  * session: `<repo>`
  * window: `<issue#>-<slug>`

### One “policy” prompt injected into every run

Put in `agents/policy.py` and prepend to tasks:

* PR-only
* no main pushes
* run tests if available
* ask before big refactors
* summarize changes + commands run

---

## Quick question (so I can tailor the repo template correctly)

Do you want the PoC engine to operate on **one repo only** (simpler) or **multiple repos** (frontend/backend) right away?

Either is fine—multi-repo just changes the “workspace hydration” and branch strategy.

[1]: https://docs.github.com/en/copilot/how-tos/provide-context/use-mcp/use-the-github-mcp-server?utm_source=chatgpt.com "Using the GitHub MCP Server"
[2]: https://docs.cloud.google.com/run/docs/configuring/services/secrets?utm_source=chatgpt.com "Configure secrets for services | Cloud Run"
[3]: https://docs.langchain.com/oss/python/langgraph/overview?utm_source=chatgpt.com "LangGraph overview - Docs by LangChain"
[4]: https://developers.openai.com/codex/cli/?utm_source=chatgpt.com "Codex CLI"
[5]: https://github.com/github/github-mcp-server/blob/main/docs/server-configuration.md?utm_source=chatgpt.com "github-mcp-server/docs/server-configuration.md at main"
[6]: https://docs.github.com/en/enterprise-cloud%40latest/copilot/how-tos/provide-context/use-mcp/configure-toolsets?utm_source=chatgpt.com "Configuring toolsets for the GitHub MCP Server"
[7]: https://docs.github.com/en/copilot/how-tos/provide-context/use-mcp/set-up-the-github-mcp-server?utm_source=chatgpt.com "Setting up the GitHub MCP Server"
[8]: https://docs.cloud.google.com/python/docs/reference/secretmanager/latest?utm_source=chatgpt.com "Python Client for Secret Manager"
[9]: https://docs.langchain.com/oss/python/langgraph/workflows-agents?utm_source=chatgpt.com "Workflows and agents - Docs by LangChain"

Claude Code API Key stored in the GCP project appforge-483920 as CLAUDE_CODE_ADMIN_API_KEY
OpenAI API Key stored in the GCP project appforge-483920 as APPFORGE_OPENAI_API_KEY
GitHub Token stored in the GCP project appforge-483920 as GITHUB_CONTROL_API_KEY

GCP credentials file in repo and named appforge-creds.json

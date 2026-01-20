# Tmux Sessions: How It Works (Plain-English, Drunk Grandma Edition)

Below is a simple, no-fancy explanation of how a tmux session is created and
points at the right repo/worktree. Think of tmux sessions like little TV rooms.
Each room is labeled with the repo + issue number, and inside that room the agent
is already standing in the right folder.

## 1) Where the repo is cloned

- Workspace root is set by `AGENT_WORKSPACE_ROOT` (default: `/tmp/agent-hq`).
- Each issue gets its own "worktree" folder under:
  - `/tmp/agent-hq/worktrees/<repo>/<issue>/`
- Note: The code *calls it* a worktree, but it actually does a fresh `git clone`
  into that folder (not a git worktree checkout).

Source: `src/ace/config/settings.py`, `src/ace/workspaces/git_ops.py`.

## 2) How tmux knows which repo/worktree to use

Two things tie a session to the right repo:

1) **Session name** includes repo + issue number:
   - `ace-<repo>-<issue>`
   - Example: `ace-irlsc-events-193`
2) **Working directory** is set when the session is created:
   - tmux starts in `.../worktrees/<repo>/<issue>/`

Source: `src/ace/workspaces/tmux_ops.py`, `src/ace/agents/cli_agent.py`.

## 3) How a tmux session is spawned (current state)

Right now, it is **fully implemented** and **scripted via Python**, not a shell
script. The flow is:

1) Orchestrator makes sure the repo is cloned and branch exists.
2) It writes instructions to `ACE_TASK.md` in that worktree.
3) The CLI agent starts a tmux session with a command to run Codex or Claude.

The actual spawn happens in `CliAgent.run()` which calls `TmuxOps.start_session()`.

Source: `src/ace/orchestration/graph.py`, `src/ace/agents/cli_agent.py`,
`src/ace/workspaces/tmux_ops.py`.

## 4) Does it use a script? What is in the script?

Yes, for **Codex** the default command is:

- `./scripts/codex-gh --model {model} --ask-for-approval 3 --sandbox workspace-write`

That script just makes sure the GitHub token is set, then runs `codex`:

- If `GITHUB_TOKEN` is missing, it copies `GITHUB_CONTROL_API_KEY` into it.
- If `GITHUB_MCP_TOKEN_ENV` is set, it exports that too.
- Then it runs `codex`.

Claude does **not** use a script by default. It uses:

- `claude --model {model}`

Source: `scripts/codex-gh`, `src/ace/config/settings.py`.

## 5) How instructions get into the tmux session

Instructions live in the worktree at:

- `ACE_TASK.md`

When the tmux session starts, there are two ways the prompt gets in:

1) **Inline prompt**: If the command template has `{prompt}`, the full
   instructions are passed directly to the CLI.
2) **Paste-after-start**: If not, the agent starts tmux, then pastes the prompt
   into the session and hits Enter.

In this codebase, the default templates do **not** include `{prompt}`, so the
paste-after-start path is used.

Source: `src/ace/agents/cli_agent.py`.

## 6) How env vars get injected into the tmux session

When the session is created, the code does:

- `tmux new-session ... -- env KEY=VALUE ... <command>`

So the **command inside tmux** inherits those environment variables.

Injected keys can include:

- `GITHUB_TOKEN`, `GITHUB_CONTROL_API_KEY`, `GITHUB_MCP_TOKEN_ENV`
- `OPENAI_API_KEY` (Codex/OpenAI)
- `CLAUDE_CODE_ADMIN_API_KEY` (Claude)
- `GOOGLE_APPLICATION_CREDENTIALS`, `GCP_CREDENTIALS_FILE`

Also:

- Codex MCP config is written to `~/.codex/config.toml`.
- Claude MCP config is written to `<worktree>/.mcp.json` and git-ignored.

Source: `src/ace/agents/cli_agent.py`, `src/ace/agents/mcp_config.py`,
`src/ace/config/secrets.py`.

## 7) How "Enter" gets pressed after the prompt is pasted

The code literally sends the keys over tmux:

- `tmux send-keys -l "<prompt text>"`
- Then `tmux send-keys C-m` (Enter)
- It does Enter **twice**, with a small delay, to be safe.

There is also a retry loop (3 tries) if Enter fails to send.

Source: `src/ace/workspaces/tmux_ops.py`.

## 8) Quick mental picture

Imagine you have a row of little TV rooms:

- Each room is named like `ace-<repo>-<issue>`.
- When a room opens, the agent walks into the right folder.
- A note (`ACE_TASK.md`) is read out loud.
- The room is given the right keys (env vars) to access GitHub/GCP.
- If the agent gets quiet, the manager pokes it with a "please continue" note.

That is basically the whole tmux setup.

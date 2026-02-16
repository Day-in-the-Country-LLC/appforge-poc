Repository agent practices
--------------------------

- Always read this file (AGENTS.md) before making changes; follow any repo-specific practices documented here.
- Check for skills often and use available skills to perform tasks.
- Codex CLI skills: place skills at `~/.codex/skills/<skill-name>/SKILL.md` so the CLI can resolve them.
- Claude CLI skills: place skills at `~/.claude/skills/<skill-name>/SKILL.md` so the CLI can resolve them.
- Use `uv` only for Python package management (no `pip`, no `uv pip`).
- Target Python 3.12 or newer for runtime and tooling.
- Fail fast: if a required step or dependency fails, stop and surface the error clearly instead of continuing in a degraded state.
- Terraform for deployment and infrastructure is managed in `/Users/kristinday/ditc_terraform`. Do not add new Terraform changes in this repository.
- Prefer `--arg` style CLI arguments over setting environment variables for configuration.
- No fallbacks unless explicitly requested; errors must be loud (use "❌ ERROR" and stop execution).
- If the user asks to change behavior, implement that behavior directly; do not preserve or introduce fallback/alternate paths unless explicitly requested by the user.
- Every error log must include a "❌ ERROR" banner; never log errors without the banner.
- Skilling guidance for issue creation:
  - For creating GitHub issues, use `~/.codex/skills/github-issue-creation/SKILL.md` (or the equivalent `~/.claude/skills/...` path for Claude tools).

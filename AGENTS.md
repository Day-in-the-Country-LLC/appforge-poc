Repository agent practices
--------------------------

- Always read this file (AGENTS.md) before making changes; follow any repo-specific practices documented here.
- Use `uv` only for Python package management (no `pip`, no `uv pip`).
- Target Python 3.12 or newer for runtime and tooling.
- Fail fast: if a required step or dependency fails, stop and surface the error clearly instead of continuing in a degraded state.

# Multi-Agent Project Planning (Cross-Repo)

This document describes a practical way to use a **team of coding agents** to create a **comprehensive cross-repo plan** and then **create GitHub issues** with proper dependency relationships.

It is written for the workflow you described:

- You open a terminal in one repo.
- You use a single coding CLI agent (often Codex) to plan work spanning multiple repos in the same “project”.
- You want to know if a multi-agent approach is possible and whether it makes sense.

## Summary Recommendation

Use a **hub-and-spoke** model:

- 1 **Coordinator** agent owns: overall plan quality, consistency, dependencies, and the final issue set.
- N **Repo Scout** agents (one per repo) own: reading and summarizing their repo, identifying repo-specific work items, risks, and prerequisites.
- 1 **Issue Writer** agent owns: converting the approved plan into GitHub issues (titles, acceptance criteria, labels, dependencies).

Key rule: **Do not have every agent read every repo.**

Instead:

- Repo scouts operate with *tight scope* and return structured findings.
- Coordinator synthesizes all repo scout outputs into a single cross-repo plan and a dependency graph.

This keeps context manageable and prevents cross-repo confusion.

## Why This Is Better Than “Everyone Reads Everything”

“Team of agents who can see all repos” sounds attractive but usually fails in practice due to:

- **Context explosion**: too many files and conventions to hold in working memory.
- **Inconsistent assumptions**: agents infer missing project rules differently.
- **Duplicate or contradictory issues**: multiple agents propose overlapping work.
- **Time waste**: each agent repeats the same discovery.

A hub-and-spoke structure makes cross-repo planning reliable:

- Discovery is parallelized.
- Decisions are centralized.
- Output is consistent.

## Roles

### 1) Coordinator (1 agent)

Responsibilities:

- Defines the target outcomes for planning.
- Establishes project-wide conventions (labels, issue template, definitions of done, “what counts as blocking”).
- Produces:
  - `PLAN.md` (ordered steps)
  - `ISSUE_GRAPH.json` (issues + dependencies)
  - `RISK_REGISTER.md` (known unknowns + mitigation)

Inputs:

- Shared context (“context pack”) described below.
- Structured reports from repo scouts.

### 2) Repo Scouts (N agents, 1 per repo)

Responsibilities:

- Only read their assigned repo.
- Produce a structured report:

```json
{
  "repo": "org/repo",
  "summary": "What this repo is and how it fits",
  "entrypoints": ["src/...", "infra/..."],
  "deploy": "how it ships",
  "tests": "how to run CI locally",
  "risks": ["..."],
  "work_items": [
    {
      "title": "...",
      "goal": "...",
      "acceptance": ["..."],
      "files": ["path:line"],
      "est": "S|M|L",
      "depends_on": ["other work items or prerequisites"],
      "notes": "..."
    }
  ]
}
```

Repo scouts should:

- Prefer concrete file references.
- Call out missing configuration, unclear ownership boundaries, or external dependencies.
- Avoid creating issues directly (that’s the Issue Writer’s job), unless explicitly asked.

### 3) Synthesizer (can be the Coordinator)

Responsibilities:

- Deduplicate work items.
- Resolve cross-repo boundaries.
- Convert repo-local work items into a unified plan.
- Decide the dependency graph and sequencing.

### 4) Issue Writer (1 agent)

Responsibilities:

- Convert the plan into GitHub issues.
- Enforce:
  - consistent title format
  - acceptance criteria style
  - labeling
  - dependencies (blocked-by / blocks)
  - proper repo placement

If you already have an issue creation workflow/skill, this role maps cleanly to that.

## Required “Context Pack”

A multi-agent planning team only works if they share a small, authoritative set of project rules.

Recommended files:

1. **Project architecture**
- `project_architecture.md` (or equivalent)
- Must answer:
  - what repos exist
  - boundaries (who owns what)
  - shared infra/services
  - environments

2. **Repo inventory**
- `repos.json` (per project)

```json
{
  "project": "appforge",
  "repos": [
    {"name": "Day-in-the-Country-LLC/appforge-poc", "role": "orchestrator"},
    {"name": "Day-in-the-Country-LLC/digido", "role": "product"}
  ]
}
```

3. **Issue conventions**
- `issue_conventions.md`
- Defines:
  - required labels
  - how to write acceptance criteria
  - what “blocked” means
  - how to represent dependencies

4. **Cross-repo mapping metadata (optional but powerful)**
If planning depends on non-GitHub facets (like GCP projects), keep a mapping file (you already did this for repos->GCP).

## Execution Models

### Model A: Local Coordinator Orchestrates Scouts

How it works:

- You run a local coordinator script.
- The coordinator spawns N scouts (subprocess, or separate terminal sessions).
- Each scout outputs a report file.
- The coordinator merges reports into plan + issue graph.

Pros:

- Simple.
- Fast iteration.
- Minimal infra.

Cons:

- Your laptop is the compute.
- You need a local multi-process driver.

### Model B: Remote Planning “Planner Worker”

How it works:

- A planner service receives a request: “plan project X”.
- It checks out all repos (or uses a mirrored workspace).
- It spawns scouts and synthesizes results.

Pros:

- Repeatable.
- Can run on a schedule.

Cons:

- More infrastructure.
- Requires careful secrets management.

For most teams, Model A is the right starting point.

## Concrete Artifacts (What the Team Produces)

1. `docs/plans/<project>/<date>/PLAN.md`
- Human-readable sequencing.

2. `docs/plans/<project>/<date>/ISSUES.json`
- Machine-readable issue list.

3. `docs/plans/<project>/<date>/DEPENDENCIES.mmd`
- Mermaid diagram of dependencies.

4. `docs/plans/<project>/<date>/SCOUT_REPORTS/<repo>.json`
- Raw inputs from scouts.

### Example Issue Object

```json
{
  "repo": "Day-in-the-Country-LLC/appforge-poc",
  "title": "Observability: emit correlation fields in webhook lifecycle logs",
  "body": "...",
  "labels": ["ace", "observability", "priority:P1"],
  "depends_on": [
    {"repo": "Day-in-the-Country-LLC/ditc_terraform", "issue": "#123"}
  ],
  "estimate": "M"
}
```

## Dependency Rules (Keep This Simple)

Suggested dependency types:

- `blocks`: this issue must land first.
- `blocked_by`: inverse edge.

Guidelines:

- Prefer **one** blocking chain per issue (avoid diamond graphs unless necessary).
- Use **cross-repo dependencies** sparingly; if you need lots of them, your issue boundaries are probably wrong.

## How Agents Should Talk to Each Other (Structured IO)

Avoid free-form “essays” between agents.

Use:

- JSON for scout reports.
- Markdown for final plan.
- A single dependency graph file for ordering.

This reduces truncation problems and makes it easy to review and adjust.

## Prompts (Practical Defaults)

### Repo Scout Prompt Template

- Input: repo path + context pack summary
- Output: strict JSON with the schema above

Key constraints:

- Must cite file paths.
- Must propose 5–20 work items max.
- Must flag unknowns and dependencies.

### Coordinator Prompt Template

- Input: all scout JSON reports + context pack
- Output:
  - `PLAN.md`
  - `ISSUES.json`
  - `DEPENDENCIES.mmd`

Constraints:

- Deduplicate by goal.
- Ensure each issue is:
  - bounded
  - testable
  - has acceptance criteria

### Issue Writer Prompt Template

- Input: `ISSUES.json`
- Output:
  - created GitHub issues
  - updated dependency links

Constraints:

- Don’t invent dependencies.
- Don’t create issues in the wrong repo.

## Does It Make Sense For You?

Based on what you’ve described (multiple repos per project, repeated planning work, desire for consistent ordering and dependencies):

- Yes, it makes sense.
- It’s most valuable when:
  - you frequently have cross-repo sequences
  - you want consistent issue quality
  - you want faster discovery than a single agent can do

If your projects are small (1–2 repos) or planning is trivial, a single-agent plan is often enough.

## Suggested Next Step (Low-Risk Pilot)

Pick one project with 3–6 repos.

1. Create a minimal `repos.json`.
2. Run 1 coordinator + 3–6 scouts.
3. Generate a plan and review it manually.
4. Only then auto-create issues.

This keeps the automation risk low while proving the value.

## Implementation Note

If you want, we can implement a simple local driver script (Python 3.12, `uv`) that:

- loads repo list
- runs scouts in parallel
- writes reports
- runs synthesis
- outputs `ISSUES.json`

Then you can use your existing issue-creation workflow to create issues from that artifact.

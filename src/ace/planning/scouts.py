"""GitHub repository scouts and planning artifact synthesis."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from ace.agents.llm_client import call_openai
from ace.config.settings import Settings, get_settings
from ace.github.api_client import GitHubAPIClient
from ace.planning.issue_writer import parse_issues_payload
from ace.planning.models import (
    PlanningProjectRegistry,
    PlanningProjectRepository,
    PlanningSession,
)

try:
    from google.cloud import firestore  # type: ignore[import-untyped]
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    firestore = None


_DEFAULT_PROJECT_REGISTRY_DIR = Path("docs/projects")
_DEFAULT_REPO_GCP_MAPPING = Path("docs/repo-gcp-mapping.json")
_ALWAYS_INCLUDE_REPO = "ditc_terraform"
_ENTRYPOINT_FILES = (
    "__main__.py",
    "app.py",
    "main.py",
    "server.py",
    "cli.py",
    "manage.py",
    "index.js",
    "index.ts",
    "main.go",
)
_KEY_FILES = (
    "readme.md",
    "readme.rst",
    "readme.txt",
    "pyproject.toml",
    "package.json",
    "requirements.txt",
    "package-lock.json",
    "poetry.lock",
    "setup.py",
    "setup.cfg",
    "go.mod",
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "makefile",
    ".github/workflows/ci.yml",
)


class PlanningScoutError(ValueError):
    """Base error for planning scout operations."""


@dataclass(frozen=True)
class ScoutReport:
    """A normalized report produced by repo scouting."""

    repo: str
    summary: str
    entrypoints: list[str]
    risks: list[str]
    work_items: list[str]


@dataclass(frozen=True)
class PlanningArtifacts:
    """Rendered artifact payloads for a planning session."""

    plan_markdown: str
    issues_json: str
    dependencies_mmd: str


async def load_project_registry(
    project_slug: str,
    *,
    settings: Settings | None = None,
) -> PlanningProjectRegistry:
    """Load project metadata from Firestore, local files, or repo-gcp-mapping."""
    settings = settings or get_settings()
    firestore_registry = _load_project_registry_from_firestore(
        project_slug,
        project_id=(settings.gcp_project_id or "").strip(),
    )
    if firestore_registry is not None:
        return firestore_registry

    project_file = _DEFAULT_PROJECT_REGISTRY_DIR / f"{project_slug}.json"
    if project_file.exists():
        return _load_project_registry_from_file(project_slug)

    return _load_project_registry_from_mapping(project_slug)


def _load_project_registry_from_firestore(
    project_slug: str,
    *,
    project_id: str,
) -> PlanningProjectRegistry | None:
    """Load a project registry from Firestore if configured."""
    if not project_id or firestore is None:
        return None
    db = firestore.Client(project=project_id)
    snapshot = db.collection("projects").document(project_slug).get()
    if not snapshot.exists:
        return None
    payload = snapshot.to_dict() or {}
    return _project_registry_from_payload(payload, source=f"projects/{project_slug} (firestore)")


def _load_project_registry_from_mapping(
    project_slug: str,
) -> PlanningProjectRegistry:
    """Build a project registry from repo-gcp-mapping.json by gcp_project name."""
    if not _DEFAULT_REPO_GCP_MAPPING.is_file():
        raise PlanningScoutError(
            f"❌ ERROR: project registry not found for '{project_slug}' "
            f"and {_DEFAULT_REPO_GCP_MAPPING} does not exist"
        )
    try:
        entries = json.loads(_DEFAULT_REPO_GCP_MAPPING.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PlanningScoutError(
            f"❌ ERROR: failed to parse {_DEFAULT_REPO_GCP_MAPPING}: {exc}"
        ) from exc

    if not isinstance(entries, list):
        raise PlanningScoutError(f"❌ ERROR: {_DEFAULT_REPO_GCP_MAPPING} must be a JSON array")

    seen_repos: set[str] = set()
    repos: list[PlanningProjectRepository] = []
    has_always_include = False

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        gcp_project = (entry.get("gcp_project") or "").strip()
        repo_full = (entry.get("repo") or "").strip()
        if not repo_full:
            continue

        parts = repo_full.split("/", 1)
        owner = parts[0] if len(parts) == 2 else ""
        name = parts[1] if len(parts) == 2 else repo_full

        if name == _ALWAYS_INCLUDE_REPO:
            has_always_include = True

        if gcp_project != project_slug and name != _ALWAYS_INCLUDE_REPO:
            continue

        repo_key = f"{owner}/{name}".lower()
        if repo_key in seen_repos:
            continue
        seen_repos.add(repo_key)

        repos.append(
            PlanningProjectRepository(
                owner=owner,
                name=name,
                local_path=entry.get("local_path") or None,
                github_url=entry.get("github_url") or f"https://github.com/{owner}/{name}",
            )
        )

    if not has_always_include:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            repo_full = (entry.get("repo") or "").strip()
            parts = repo_full.split("/", 1)
            name = parts[1] if len(parts) == 2 else repo_full
            if name == _ALWAYS_INCLUDE_REPO:
                owner = parts[0] if len(parts) == 2 else ""
                repo_key = f"{owner}/{name}".lower()
                if repo_key not in seen_repos:
                    repos.append(
                        PlanningProjectRepository(
                            owner=owner,
                            name=name,
                            local_path=entry.get("local_path") or None,
                            github_url=(
                                entry.get("github_url") or f"https://github.com/{owner}/{name}"
                            ),
                        )
                    )
                break

    if not repos:
        raise PlanningScoutError(
            f"❌ ERROR: no repos found for project '{project_slug}' in {_DEFAULT_REPO_GCP_MAPPING}"
        )

    return PlanningProjectRegistry(project_slug=project_slug, repos=repos)


def _load_project_registry_from_file(project_slug: str) -> PlanningProjectRegistry:
    path = _DEFAULT_PROJECT_REGISTRY_DIR / f"{project_slug}.json"
    if not path.exists():
        raise PlanningScoutError(
            f"❌ ERROR: project registry not found in firestore or local file ({path})"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PlanningScoutError(
            f"❌ ERROR: project registry parse failed ({path}): {exc}"
        ) from exc
    return _project_registry_from_payload(raw, source=str(path))


def _project_registry_from_payload(
    payload: dict[str, Any],
    *,
    source: str,
) -> PlanningProjectRegistry:
    if not isinstance(payload, dict):
        raise PlanningScoutError(f"❌ ERROR: invalid project registry payload from {source}")
    slug = _required_str(
        payload.get("project_slug"),
        field="project_slug",
        source=source,
    )
    repos_payload = payload.get("repos")
    if not isinstance(repos_payload, list):
        raise PlanningScoutError(
            f"❌ ERROR: invalid project registry repos from {source}: expected array"
        )

    repos = [
        _project_repository_from_payload(raw_entry, index=index, source=source)
        for index, raw_entry in enumerate(repos_payload)
    ]
    return PlanningProjectRegistry(project_slug=slug, repos=repos)


def _project_repository_from_payload(
    raw_entry: Any,
    *,
    index: int,
    source: str,
) -> PlanningProjectRepository:
    if not isinstance(raw_entry, dict):
        raise PlanningScoutError(
            f"❌ ERROR: invalid project registry repos[{index}] from {source}: expected object"
        )
    owner = _required_str(raw_entry.get("owner"), field="owner", source=source)
    name = _required_str(raw_entry.get("name"), field="name", source=source)
    local_path = raw_entry.get("local_path") or None
    github_url = raw_entry.get("github_url") or None
    return PlanningProjectRepository(
        owner=owner,
        name=name,
        local_path=local_path,
        github_url=github_url,
    )


def _required_str(value: Any, *, field: str, source: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanningScoutError(
            f"❌ ERROR: invalid project registry {field} in {source}: expected non-empty string"
        )
    return value.strip()


async def run_repositories_scout(
    repos: Iterable[PlanningProjectRepository],
    *,
    github_token: str,
) -> list[ScoutReport]:
    """Run the configured repo scouts and return structured reports."""
    api_client: GitHubAPIClient | None = None
    reports: list[ScoutReport] = []
    try:
        for repo in repos:
            if repo.local_path:
                reports.append(_scout_local_repository(repo))
            else:
                if api_client is None:
                    api_client = GitHubAPIClient(github_token)
                reports.append(await _scout_repository(api_client, repo))
    finally:
        if api_client is not None:
            await api_client.close()
    return reports


def _scout_local_repository(repo: PlanningProjectRepository) -> ScoutReport:
    """Scout a repository from the local filesystem."""
    repo_name = f"{repo.owner}/{repo.name}"
    root = Path(repo.local_path)  # type: ignore[arg-type]
    if not root.is_dir():
        raise PlanningScoutError(
            f"❌ ERROR: local_path does not exist for {repo_name}: {repo.local_path}"
        )

    tree_entries: list[dict[str, Any]] = []
    for item in sorted(root.rglob("*")):
        rel = str(item.relative_to(root))
        if any(part.startswith(".") for part in item.parts[len(root.parts) :]):
            continue
        if item.is_file():
            tree_entries.append({"path": rel, "type": "blob"})
        elif item.is_dir():
            tree_entries.append({"path": rel, "type": "tree"})

    entrypoints = _extract_entrypoints(tree_entries)
    key_file_content = _extract_key_file_presence(tree_entries)
    key_file_samples = _read_local_key_file_samples(root, key_file_content)
    risks = _score_risks(tree_entries, key_file_content, entrypoints)
    work_items = _propose_work_items(entrypoints, key_file_content, key_file_samples)
    file_count = len([e for e in tree_entries if e.get("type") == "blob"])
    summary = _build_repo_summary(repo_name, file_count, tree_entries)
    return ScoutReport(
        repo=repo_name,
        summary=summary,
        entrypoints=entrypoints,
        risks=risks,
        work_items=work_items,
    )


def _read_local_key_file_samples(
    root: Path,
    present_files: set[str],
) -> dict[str, str]:
    """Read key file samples from the local filesystem."""
    candidates = (
        "readme.md",
        "readme.rst",
        "readme.txt",
        "pyproject.toml",
        "package.json",
        "requirements.txt",
        "go.mod",
        "makefile",
        ".github/workflows/ci.yml",
    )
    samples: dict[str, str] = {}
    for path in candidates:
        if path not in present_files:
            continue
        full = root / path
        if not full.is_file():
            continue
        try:
            text = full.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        samples[path] = text.replace("\n", " ").strip()[:240]
    return samples


async def _scout_repository(
    api_client: GitHubAPIClient,
    repo: PlanningProjectRepository,
) -> ScoutReport:
    repo_name = f"{repo.owner}/{repo.name}"
    repo_meta = await api_client.rest_get(f"/repos/{repo.owner}/{repo.name}")
    default_branch = _required_str(
        repo_meta.get("default_branch"),
        field="default_branch",
        source=f"{repo_name}/metadata",
    )
    tree_payload = await api_client.rest_get(
        f"/repos/{repo.owner}/{repo.name}/git/trees/{default_branch}",
        params={"recursive": "1"},
    )
    if not isinstance(tree_payload, dict):
        raise PlanningScoutError(f"❌ ERROR: invalid tree response for {repo_name}")
    tree_entries = tree_payload.get("tree") or []
    if not isinstance(tree_entries, list):
        raise PlanningScoutError(f"❌ ERROR: invalid tree payload for {repo_name}")

    entrypoints = _extract_entrypoints(tree_entries)
    key_file_content = _extract_key_file_presence(tree_entries)
    key_file_samples = await _read_key_file_samples(api_client, repo, key_file_content)
    risks = _score_risks(tree_entries, key_file_content, entrypoints)
    work_items = _propose_work_items(entrypoints, key_file_content, key_file_samples)
    file_count = len([entry for entry in tree_entries if entry.get("type") == "blob"])
    summary = _build_repo_summary(repo_name, file_count, tree_entries)
    return ScoutReport(
        repo=repo_name,
        summary=summary,
        entrypoints=entrypoints,
        risks=risks,
        work_items=work_items,
    )


def _extract_entrypoints(tree_entries: list[dict[str, Any]]) -> list[str]:
    entrypoints = []
    for entry in tree_entries:
        if entry.get("type") != "blob":
            continue
        path = _required_tree_path(entry)
        name = path.rsplit("/", 1)[-1].lower()
        if name in _ENTRYPOINT_FILES:
            entrypoints.append(path)
    return sorted(set(entrypoints))


def _extract_key_file_presence(tree_entries: list[dict[str, Any]]) -> set[str]:
    key_files = set()
    for entry in tree_entries:
        if entry.get("type") != "blob":
            continue
        path = _required_tree_path(entry)
        normalized = path.lower()
        if normalized in _KEY_FILES:
            key_files.add(normalized)
    return key_files


async def _read_key_file_samples(
    api_client: GitHubAPIClient,
    repo: PlanningProjectRepository,
    present_files: set[str],
) -> dict[str, str]:
    samples: dict[str, str] = {}
    candidates = (
        "readme.md",
        "readme.rst",
        "readme.txt",
        "pyproject.toml",
        "package.json",
        "requirements.txt",
        "go.mod",
        "makefile",
        ".github/workflows/ci.yml",
    )
    for path in candidates:
        if path not in present_files:
            continue
        try:
            payload = await api_client.rest_get(f"/repos/{repo.owner}/{repo.name}/contents/{path}")
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        file_content = payload.get("content")
        if not isinstance(file_content, str):
            continue
        encoding = payload.get("encoding")
        try:
            if encoding == "base64":
                decoded = base64.b64decode(file_content).decode("utf-8", errors="ignore")
            else:
                decoded = file_content
        except Exception:
            continue
        samples[path] = decoded.replace("\n", " ").strip()[:240]
        continue
    return samples


def _build_repo_summary(repo: str, file_count: int, tree_entries: list[dict[str, Any]]) -> str:
    top_dirs = {
        entry.get("path", "").split("/", 1)[0].strip()
        for entry in tree_entries
        if entry.get("type") == "blob" and "/" in entry.get("path", "")
    }
    top_dirs.discard("")
    directories = ", ".join(sorted(top_dirs)) if top_dirs else "none"
    return (
        f"Scanned {file_count} files across {len(top_dirs)} top-level directories ({directories})"
        + f" for {repo}."
    )


def _score_risks(
    tree_entries: list[dict[str, Any]],
    key_file_content: set[str],
    entrypoints: list[str],
) -> list[str]:
    risks: list[str] = []
    readme_present = any(
        path in key_file_content for path in {"readme.md", "readme.rst", "readme.txt"}
    )
    if not readme_present:
        risks.append("Repository lacks project documentation (`README`).")
    if len(tree_entries) == 0:
        risks.append("Repository tree fetch returned no files.")
    if not entrypoints:
        risks.append("No obvious entrypoint files were detected.")
    if len(tree_entries) > 2500:
        risks.append("Large file count suggests broad impact and longer plan effort.")
    return risks


def _propose_work_items(
    entrypoints: list[str],
    key_file_content: set[str],
    key_file_samples: dict[str, str],
) -> list[str]:
    work_items = []
    for entrypoint in entrypoints[:3]:
        work_items.append(f"Inspect and validate execution flow in `{entrypoint}`.")
    if "dockerfile" in key_file_content:
        work_items.append("Review container build and deployment path.")
    if (
        "requirements.txt" in key_file_content
        or "poetry.lock" in key_file_content
        or "pyproject.toml" in key_file_content
    ):
        work_items.append(
            "Confirm dependency constraints and update cadence for affected services."
        )
    if "package.json" in key_file_content or "package-lock.json" in key_file_content:
        work_items.append("Review npm scripts and runtime scripts for change surface.")
    if any(key in key_file_samples for key in {"readme.md", "readme.rst", "readme.txt"}):
        readme_sample = next(
            sample
            for key, sample in key_file_samples.items()
            if key in {"readme.md", "readme.rst", "readme.txt"}
        )
        if "testing" in readme_sample.lower():
            work_items.append("Prioritize test-first updates since README mentions testing scope.")
    return work_items or ["Review repository structure and architecture before drafting plan."]


def _required_tree_path(entry: dict[str, Any]) -> str:
    path = entry.get("path")
    if not isinstance(path, str) or not path.strip():
        raise PlanningScoutError("❌ ERROR: invalid tree entry path")
    return path.strip()


def _parse_plan_markdown_input(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=True, indent=2)


def _build_scout_context_payload(
    *,
    session: PlanningSession,
    project_slug: str,
    scout_reports: list[ScoutReport],
) -> dict[str, Any]:
    return {
        "session_id": session.id,
        "project_slug": project_slug,
        "request_text": _parse_plan_markdown_input(session.request_text),
        "repos": [
            {
                "repo": report.repo,
                "summary": report.summary,
                "entrypoints": report.entrypoints,
                "risks": report.risks,
                "work_items": report.work_items,
            }
            for report in scout_reports
        ],
    }


def _truncate(value: str, *, max_chars: int) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}..."


def _format_plan_agent_prompt(context_payload: dict[str, Any]) -> str:
    return (
        "You are the Planning Strategist Agent.\n"
        "Generate a production-ready implementation plan from multi-repo scout data.\n\n"
        "Return ONLY markdown with these sections (exact headings):\n"
        "# Implementation Plan\n"
        "## Objective\n"
        "## Scope\n"
        "## Repo Findings\n"
        "## Execution Phases\n"
        "## Risks and Mitigations\n"
        "## Validation Strategy\n\n"
        "Rules:\n"
        "- Include concrete repo paths/entrypoints where relevant.\n"
        "- Keep the plan actionable and sequence-aware.\n"
        "- No code fences and no JSON.\n\n"
        "Context JSON:\n"
        f"{json.dumps(context_payload, indent=2, ensure_ascii=True)}"
    )


def _format_issue_agent_prompt(
    *,
    context_payload: dict[str, Any],
    plan_markdown: str,
) -> str:
    return (
        "You are the Issue Decomposition Agent.\n"
        "Convert the implementation plan into executable GitHub issues.\n\n"
        "Return ONLY JSON with this schema:\n"
        "{\n"
        '  "issues": [\n'
        "    {\n"
        '      "id": "ISSUE-001",\n'
        '      "repo": "owner/repo",\n'
        '      "title": "string",\n'
        '      "description": "string",\n'
        '      "priority": "low|medium|high",\n'
        '      "depends_on": ["ISSUE-000"]\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- Use only repo values from the provided context.\n"
        "- IDs must be unique and stable (ISSUE-001, ISSUE-002, ...).\n"
        "- Keep dependencies acyclic.\n"
        "- Every issue must be independently testable.\n"
        "- No markdown fences.\n\n"
        "Plan markdown:\n"
        f"{_truncate(plan_markdown, max_chars=14000)}\n\n"
        "Context JSON:\n"
        f"{json.dumps(context_payload, indent=2, ensure_ascii=True)}"
    )


def _format_dependencies_agent_prompt(
    *,
    context_payload: dict[str, Any],
    issues_json: str,
) -> str:
    return (
        "You are the Dependency Graph Agent.\n"
        "Build a Mermaid flowchart that captures issue dependencies.\n\n"
        "Return ONLY JSON with this schema:\n"
        "{\n"
        '  "dependencies_mmd": "flowchart TD\\n    A --> B"\n'
        "}\n\n"
        "Rules:\n"
        "- Output must start with `flowchart TD`.\n"
        "- Include one node per issue id.\n"
        "- Include one edge per depends_on relation.\n"
        "- No markdown fences.\n\n"
        "Issues JSON:\n"
        f"{_truncate(issues_json, max_chars=14000)}\n\n"
        "Context JSON:\n"
        f"{json.dumps(context_payload, indent=2, ensure_ascii=True)}"
    )


def _format_plan_collaboration_prompt(
    *,
    context_payload: dict[str, Any],
    current_plan_markdown: str,
    issues_json: str,
    dependencies_mmd: str,
    controller_feedback: str,
    round_index: int,
) -> str:
    return (
        f"You are the Planning Strategist Agent in collaboration round {round_index}.\n"
        "Review the issue and dependency outputs from peer agents and revise the plan so all "
        "artifacts are aligned.\n\n"
        "Return ONLY markdown with these exact headings:\n"
        "# Implementation Plan\n"
        "## Objective\n"
        "## Scope\n"
        "## Repo Findings\n"
        "## Execution Phases\n"
        "## Risks and Mitigations\n"
        "## Validation Strategy\n\n"
        "Current PLAN.md:\n"
        f"{_truncate(current_plan_markdown, max_chars=14000)}\n\n"
        "Current ISSUES.json:\n"
        f"{_truncate(issues_json, max_chars=14000)}\n\n"
        "Current DEPENDENCIES.mmd:\n"
        f"{_truncate(dependencies_mmd, max_chars=12000)}\n\n"
        "Controller feedback:\n"
        f"{_truncate(controller_feedback, max_chars=4000)}\n\n"
        "Context JSON:\n"
        f"{json.dumps(context_payload, indent=2, ensure_ascii=True)}"
    )


def _format_issue_collaboration_prompt(
    *,
    context_payload: dict[str, Any],
    plan_markdown: str,
    current_issues_json: str,
    dependencies_mmd: str,
    controller_feedback: str,
    round_index: int,
) -> str:
    return (
        f"You are the Issue Decomposition Agent in collaboration round {round_index}.\n"
        "Coordinate with the updated plan and dependency graph to revise issues so they are "
        "complete, testable, and dependency-consistent.\n\n"
        "Return ONLY JSON with this schema:\n"
        "{\n"
        '  "issues": [\n'
        "    {\n"
        '      "id": "ISSUE-001",\n'
        '      "repo": "owner/repo",\n'
        '      "title": "string",\n'
        '      "description": "string",\n'
        '      "priority": "low|medium|high",\n'
        '      "depends_on": ["ISSUE-000"]\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Updated PLAN.md:\n"
        f"{_truncate(plan_markdown, max_chars=14000)}\n\n"
        "Current ISSUES.json:\n"
        f"{_truncate(current_issues_json, max_chars=14000)}\n\n"
        "Current DEPENDENCIES.mmd:\n"
        f"{_truncate(dependencies_mmd, max_chars=12000)}\n\n"
        "Controller feedback:\n"
        f"{_truncate(controller_feedback, max_chars=4000)}\n\n"
        "Context JSON:\n"
        f"{json.dumps(context_payload, indent=2, ensure_ascii=True)}"
    )


def _format_dependencies_collaboration_prompt(
    *,
    context_payload: dict[str, Any],
    plan_markdown: str,
    issues_json: str,
    current_dependencies_mmd: str,
    controller_feedback: str,
    round_index: int,
) -> str:
    return (
        f"You are the Dependency Graph Agent in collaboration round {round_index}.\n"
        "Coordinate with the updated plan and issues and return a corrected dependency graph.\n\n"
        "Return ONLY JSON with this schema:\n"
        "{\n"
        '  "dependencies_mmd": "flowchart TD\\n    A --> B"\n'
        "}\n\n"
        "Updated PLAN.md:\n"
        f"{_truncate(plan_markdown, max_chars=12000)}\n\n"
        "Updated ISSUES.json:\n"
        f"{_truncate(issues_json, max_chars=14000)}\n\n"
        "Current DEPENDENCIES.mmd:\n"
        f"{_truncate(current_dependencies_mmd, max_chars=12000)}\n\n"
        "Controller feedback:\n"
        f"{_truncate(controller_feedback, max_chars=4000)}\n\n"
        "Context JSON:\n"
        f"{json.dumps(context_payload, indent=2, ensure_ascii=True)}"
    )


def _format_controller_agent_prompt(
    *,
    context_payload: dict[str, Any],
    plan_markdown: str,
    issues_json: str,
    dependencies_mmd: str,
    round_index: int,
    max_turns_per_agent: int,
) -> str:
    return (
        f"You are the Planning Controller Agent in collaboration round {round_index}.\n"
        "Your job is to coordinate specialist agents for coherence and stop when artifacts are "
        "sufficiently aligned.\n\n"
        "Return ONLY JSON with this exact schema:\n"
        "{\n"
        '  "decision": "continue" | "finalize",\n'
        '  "feedback": "string"\n'
        "}\n\n"
        "Rules:\n"
        "- Use `continue` when specific changes are still needed.\n"
        "- Use `finalize` only when plan, issues, and dependency graph are aligned.\n"
        "- feedback must be concise and actionable for all three specialist agents.\n"
        f"- Maximum turns per specialist agent: {max_turns_per_agent}.\n\n"
        "Current PLAN.md:\n"
        f"{_truncate(plan_markdown, max_chars=12000)}\n\n"
        "Current ISSUES.json:\n"
        f"{_truncate(issues_json, max_chars=12000)}\n\n"
        "Current DEPENDENCIES.mmd:\n"
        f"{_truncate(dependencies_mmd, max_chars=10000)}\n\n"
        "Context JSON:\n"
        f"{json.dumps(context_payload, indent=2, ensure_ascii=True)}"
    )


def _strip_markdown_fences(raw: str) -> str:
    text = raw.strip()
    if "```" not in text:
        return text
    parts = [part.strip() for part in text.split("```") if part.strip()]
    if not parts:
        return ""
    candidate = parts[0]
    if candidate.lower().startswith("markdown"):
        candidate = candidate[8:].lstrip()
    return candidate.strip()


def _extract_json_payload(raw: str, *, context: str) -> Any:
    text = raw.strip()
    if not text:
        raise ValueError(f"❌ ERROR: {context} agent returned empty response")

    candidates: list[str] = []
    if text.startswith("{") and text.endswith("}"):
        candidates.append(text)
    if text.startswith("[") and text.endswith("]"):
        candidates.append(text)
    if "```" in text:
        for block in text.split("```"):
            candidate = block.strip()
            if not candidate:
                continue
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].lstrip()
            if (candidate.startswith("{") and candidate.endswith("}")) or (
                candidate.startswith("[") and candidate.endswith("]")
            ):
                candidates.append(candidate)
    first_curly = text.find("{")
    last_curly = text.rfind("}")
    if first_curly >= 0 and last_curly > first_curly:
        candidates.append(text[first_curly : last_curly + 1])

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            return json.loads(candidate)
        except Exception:
            continue
    raise ValueError(f"❌ ERROR: {context} agent did not return valid JSON")


def _normalize_issues_payload(
    *,
    session: PlanningSession,
    project_slug: str,
    payload: Any,
) -> str:
    if not isinstance(payload, dict):
        raise ValueError("❌ ERROR: issue agent response must be a JSON object")
    issues = payload.get("issues")
    if not isinstance(issues, list):
        raise ValueError("❌ ERROR: issue agent response must include an issues array")
    normalized = {
        "project_slug": project_slug,
        "session_id": session.id,
        "generated_at": datetime.now(UTC).isoformat(),
        "issues": issues,
        "total": len(issues),
    }
    issues_json = json.dumps(normalized, indent=2, ensure_ascii=True)
    parse_issues_payload(issues_json)
    return issues_json


def _parse_dependencies_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ValueError("❌ ERROR: dependency agent response must be a JSON object")
    dependencies_mmd = str(payload.get("dependencies_mmd", "")).strip()
    if not dependencies_mmd:
        raise ValueError("❌ ERROR: dependency agent response missing dependencies_mmd")
    if not dependencies_mmd.startswith("flowchart TD"):
        raise ValueError("❌ ERROR: dependency agent output must start with 'flowchart TD'")
    return dependencies_mmd


def _parse_controller_payload(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise ValueError("❌ ERROR: controller agent response must be a JSON object")
    decision = str(payload.get("decision", "")).strip().lower()
    if decision not in {"continue", "finalize"}:
        raise ValueError(
            f"❌ ERROR: controller agent returned invalid decision '{decision}'"
        )
    feedback = str(payload.get("feedback", "")).strip()
    if not feedback:
        raise ValueError("❌ ERROR: controller agent feedback is required")
    return decision, feedback


class PlanningSynthesisRuntime:
    """Cooperative runtime for strategist/issues/dependencies/controller agents."""

    def __init__(
        self,
        *,
        session: PlanningSession,
        project: PlanningProjectRegistry,
        scout_reports: list[ScoutReport],
        openai_api_key: str,
        model: str,
        plan_max_tokens: int,
        issue_max_tokens: int,
        dependencies_max_tokens: int,
        controller_max_tokens: int,
        reasoning_effort: str,
        max_turns_per_agent: int,
    ) -> None:
        self._session = session
        self._project = project
        self._scout_reports = scout_reports
        self._openai_api_key = openai_api_key
        self._model = model
        self._plan_max_tokens = plan_max_tokens
        self._issue_max_tokens = issue_max_tokens
        self._dependencies_max_tokens = dependencies_max_tokens
        self._controller_max_tokens = controller_max_tokens
        self._reasoning_effort = reasoning_effort
        self._max_turns_per_agent = max_turns_per_agent

    async def run(self) -> PlanningArtifacts:
        if self._max_turns_per_agent < 1:
            raise ValueError("❌ ERROR: planning synthesis max turns per agent must be >= 1")

        context_payload = _build_scout_context_payload(
            session=self._session,
            project_slug=self._project.project_slug,
            scout_reports=self._scout_reports,
        )

        plan_markdown_raw = await call_openai(
            prompt=_format_plan_agent_prompt(context_payload),
            model=self._model,
            api_key=self._openai_api_key,
            max_tokens=self._plan_max_tokens,
            trace_name="planning_synthesis_plan_agent",
            metadata={"session_id": self._session.id, "project_slug": self._project.project_slug},
            reasoning_effort=self._reasoning_effort,
        )
        plan_markdown = _strip_markdown_fences(plan_markdown_raw).strip()
        if not plan_markdown:
            raise ValueError("❌ ERROR: planning strategist agent returned empty markdown")

        issues_response = await call_openai(
            prompt=_format_issue_agent_prompt(
                context_payload=context_payload,
                plan_markdown=plan_markdown,
            ),
            model=self._model,
            api_key=self._openai_api_key,
            max_tokens=self._issue_max_tokens,
            trace_name="planning_synthesis_issue_agent",
            metadata={"session_id": self._session.id, "project_slug": self._project.project_slug},
            reasoning_effort=self._reasoning_effort,
        )
        issues_payload = _extract_json_payload(issues_response, context="issue")
        issues_json = _normalize_issues_payload(
            session=self._session,
            project_slug=self._project.project_slug,
            payload=issues_payload,
        )

        dependencies_response = await call_openai(
            prompt=_format_dependencies_agent_prompt(
                context_payload=context_payload,
                issues_json=issues_json,
            ),
            model=self._model,
            api_key=self._openai_api_key,
            max_tokens=self._dependencies_max_tokens,
            trace_name="planning_synthesis_dependency_agent",
            metadata={"session_id": self._session.id, "project_slug": self._project.project_slug},
            reasoning_effort=self._reasoning_effort,
        )
        dependencies_payload = _extract_json_payload(dependencies_response, context="dependency")
        dependencies_mmd = _parse_dependencies_payload(dependencies_payload)

        for round_index in range(1, self._max_turns_per_agent):
            controller_response = await call_openai(
                prompt=_format_controller_agent_prompt(
                    context_payload=context_payload,
                    plan_markdown=plan_markdown,
                    issues_json=issues_json,
                    dependencies_mmd=dependencies_mmd,
                    round_index=round_index,
                    max_turns_per_agent=self._max_turns_per_agent,
                ),
                model=self._model,
                api_key=self._openai_api_key,
                max_tokens=self._controller_max_tokens,
                trace_name=f"planning_synthesis_controller_agent_round_{round_index}",
                metadata={"session_id": self._session.id, "project_slug": self._project.project_slug},
                reasoning_effort=self._reasoning_effort,
            )
            controller_payload = _extract_json_payload(controller_response, context="controller")
            controller_decision, controller_feedback = _parse_controller_payload(controller_payload)
            if controller_decision == "finalize":
                break

            plan_collab_response = await call_openai(
                prompt=_format_plan_collaboration_prompt(
                    context_payload=context_payload,
                    current_plan_markdown=plan_markdown,
                    issues_json=issues_json,
                    dependencies_mmd=dependencies_mmd,
                    controller_feedback=controller_feedback,
                    round_index=round_index,
                ),
                model=self._model,
                api_key=self._openai_api_key,
                max_tokens=self._plan_max_tokens,
                trace_name=f"planning_synthesis_plan_agent_collab_round_{round_index}",
                metadata={"session_id": self._session.id, "project_slug": self._project.project_slug},
                reasoning_effort=self._reasoning_effort,
            )
            plan_markdown = _strip_markdown_fences(plan_collab_response).strip()
            if not plan_markdown:
                raise ValueError(
                    f"❌ ERROR: planning strategist collaboration round {round_index} returned empty markdown"
                )

            issues_collab_response = await call_openai(
                prompt=_format_issue_collaboration_prompt(
                    context_payload=context_payload,
                    plan_markdown=plan_markdown,
                    current_issues_json=issues_json,
                    dependencies_mmd=dependencies_mmd,
                    controller_feedback=controller_feedback,
                    round_index=round_index,
                ),
                model=self._model,
                api_key=self._openai_api_key,
                max_tokens=self._issue_max_tokens,
                trace_name=f"planning_synthesis_issue_agent_collab_round_{round_index}",
                metadata={"session_id": self._session.id, "project_slug": self._project.project_slug},
                reasoning_effort=self._reasoning_effort,
            )
            issues_payload = _extract_json_payload(issues_collab_response, context="issue")
            issues_json = _normalize_issues_payload(
                session=self._session,
                project_slug=self._project.project_slug,
                payload=issues_payload,
            )

            dependencies_collab_response = await call_openai(
                prompt=_format_dependencies_collaboration_prompt(
                    context_payload=context_payload,
                    plan_markdown=plan_markdown,
                    issues_json=issues_json,
                    current_dependencies_mmd=dependencies_mmd,
                    controller_feedback=controller_feedback,
                    round_index=round_index,
                ),
                model=self._model,
                api_key=self._openai_api_key,
                max_tokens=self._dependencies_max_tokens,
                trace_name=f"planning_synthesis_dependency_agent_collab_round_{round_index}",
                metadata={"session_id": self._session.id, "project_slug": self._project.project_slug},
                reasoning_effort=self._reasoning_effort,
            )
            dependencies_payload = _extract_json_payload(
                dependencies_collab_response,
                context="dependency",
            )
            dependencies_mmd = _parse_dependencies_payload(dependencies_payload)

        return PlanningArtifacts(
            plan_markdown=plan_markdown,
            issues_json=issues_json,
            dependencies_mmd=dependencies_mmd,
        )


async def build_planning_artifacts(
    *,
    session: PlanningSession,
    project: PlanningProjectRegistry,
    scout_reports: list[ScoutReport],
    openai_api_key: str,
    model: str,
    plan_max_tokens: int,
    issue_max_tokens: int,
    dependencies_max_tokens: int,
    controller_max_tokens: int,
    reasoning_effort: str,
    max_turns_per_agent: int,
) -> PlanningArtifacts:
    """Generate planning artifacts via a multi-agent OpenAI synthesis runtime."""
    runtime = PlanningSynthesisRuntime(
        session=session,
        project=project,
        scout_reports=scout_reports,
        openai_api_key=openai_api_key,
        model=model,
        plan_max_tokens=plan_max_tokens,
        issue_max_tokens=issue_max_tokens,
        dependencies_max_tokens=dependencies_max_tokens,
        controller_max_tokens=controller_max_tokens,
        reasoning_effort=reasoning_effort,
        max_turns_per_agent=max_turns_per_agent,
    )
    return await runtime.run()

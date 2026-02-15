"""GitHub repository scouts and planning artifact synthesis."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from ace.config.settings import Settings, get_settings
from ace.github.api_client import GitHubAPIClient
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
    """Load project metadata from Firestore (preferred) or local registry files."""
    settings = settings or get_settings()
    firestore_registry = _load_project_registry_from_firestore(
        project_slug,
        project_id=(settings.gcp_project_id or "").strip(),
    )
    if firestore_registry is not None:
        return firestore_registry
    return _load_project_registry_from_file(project_slug)


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
    return PlanningProjectRepository(owner=owner, name=name)


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
    api_client = GitHubAPIClient(github_token)
    reports: list[ScoutReport] = []
    try:
        for repo in repos:
            reports.append(await _scout_repository(api_client, repo))
    finally:
        await api_client.close()
    return reports


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
            payload = await api_client.rest_get(
                f"/repos/{repo.owner}/{repo.name}/contents/{path}"
            )
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
        path in key_file_content
        for path in {"readme.md", "readme.rst", "readme.txt"}
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


def build_planning_artifacts(
    *,
    session: PlanningSession,
    project: PlanningProjectRegistry,
    scout_reports: list[ScoutReport],
) -> PlanningArtifacts:
    """Combine scout reports into final plan artifacts."""
    plan_markdown = _render_plan_markdown(
        session=session,
        project_slug=project.project_slug,
        scout_reports=scout_reports,
    )
    issues_json = _render_issues_json(
        session=session,
        project_slug=project.project_slug,
        scout_reports=scout_reports,
    )
    dependencies_mmd = _render_dependencies_mmd(
        session=session,
        project_slug=project.project_slug,
        scout_reports=scout_reports,
    )
    return PlanningArtifacts(
        plan_markdown=plan_markdown,
        issues_json=issues_json,
        dependencies_mmd=dependencies_mmd,
    )


def _render_plan_markdown(
    *,
    session: PlanningSession,
    project_slug: str,
    scout_reports: list[ScoutReport],
) -> str:
    lines = [
        "# Planning Report",
        "",
        f"Session: {session.id}",
        f"Project: {project_slug}",
        f"Request: {_parse_plan_markdown_input(session.request_text)}",
        "",
        "## Repository Summaries",
        "",
    ]
    for report in scout_reports:
        lines.extend(
            [
                f"### {report.repo}",
                f"- Summary: {report.summary}",
                f"- Entrypoints: {', '.join(report.entrypoints) or 'not detected'}",
                "- Risks:",
            ]
        )
        for risk in report.risks:
            lines.append(f"  - {risk}")
        lines.extend(["- Work items:", *(f"  - {item}" for item in report.work_items)])
        lines.append("")
    lines.extend(
        [
            "## Synthesis",
            "",
            (
                "Generated from multi-repo scouts. Coordinate implementation in"
                " small slices and validate each repository independently."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _render_issues_json(
    *,
    session: PlanningSession,
    project_slug: str,
    scout_reports: list[ScoutReport],
) -> str:
    issues: list[dict[str, Any]] = []
    counter = 1
    for report in scout_reports:
        for work_item in report.work_items:
            issues.append(
                {
                    "id": f"{session.id}-{counter:03d}",
                    "project_slug": project_slug,
                    "repo": report.repo,
                    "title": work_item,
                    "description": f"Derived from scout output: {report.summary}",
                    "priority": "medium",
                }
            )
            counter += 1

    payload = {
        "project_slug": project_slug,
        "session_id": session.id,
        "generated_at": datetime.now(UTC).isoformat(),
        "issues": issues,
        "total": len(issues),
    }
    return json.dumps(payload, indent=2, ensure_ascii=True)


def _render_dependencies_mmd(
    *,
    session: PlanningSession,
    project_slug: str,
    scout_reports: list[ScoutReport],
) -> str:
    del session
    lines = ["flowchart TD", f'    P["{project_slug}"]']
    if not scout_reports:
        lines.append("    P --> Z[No repos discovered]")
        return "\n".join(lines)
    repo_nodes = []
    for index, report in enumerate(scout_reports, start=1):
        safe_id = _safe_node_id(report.repo)
        repo_nodes.append((safe_id, report.repo))
        lines.append(f'    {safe_id}["{report.repo}"]')
        lines.append(f"    P --> {safe_id}")
    for index, entry in enumerate(repo_nodes):
        from_node = repo_nodes[index - 1][0]
        if index == 0:
            continue
        to_node = repo_nodes[index][0]
        lines.append(f"    {from_node} --> {to_node}")
    return "\n".join(lines)


def _safe_node_id(repo: str) -> str:
    normalized = "".join(char if char.isalnum() else "_" for char in repo)
    if not normalized:
        return "repo"
    if normalized[0].isdigit():
        return f"r_{normalized}"
    return normalized

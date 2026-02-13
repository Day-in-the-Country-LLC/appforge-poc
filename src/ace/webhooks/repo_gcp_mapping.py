"""Repo-to-GCP project mapping loader for webhook observability context."""

from __future__ import annotations

import json
from pathlib import Path


def load_repo_gcp_mapping(path: str) -> dict[str, str]:
    """Load and validate repo-to-GCP project mappings from JSON."""
    mapping_path = Path(path).expanduser()
    if not mapping_path.is_file():
        raise ValueError(
            f"❌ ERROR: repo_gcp_mapping_missing ({mapping_path}): required mapping file not found"
        )

    try:
        raw = json.loads(mapping_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(
            f"❌ ERROR: repo_gcp_mapping_parse_failed ({mapping_path}): {exc}"
        ) from exc

    if not isinstance(raw, list) or not raw:
        raise ValueError(
            "❌ ERROR: repo_gcp_mapping_invalid: expected a non-empty JSON array of mappings"
        )

    parsed: dict[str, str] = {}
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"❌ ERROR: repo_gcp_mapping_invalid_entry[{index}]: expected object")
        repo = entry.get("repo")
        gcp_project = entry.get("gcp_project")
        if not isinstance(repo, str) or not repo.strip():
            raise ValueError(
                "❌ ERROR: repo_gcp_mapping_invalid_entry["
                f"{index}]: repo must be a non-empty string"
            )
        if not isinstance(gcp_project, str) or not gcp_project.strip():
            raise ValueError(
                "❌ ERROR: repo_gcp_mapping_invalid_entry["
                f"{index}]: gcp_project must be a non-empty string"
            )

        normalized_repo = repo.strip().lower()
        normalized_project = gcp_project.strip()
        if normalized_repo in parsed and parsed[normalized_repo] != normalized_project:
            raise ValueError(
                "❌ ERROR: repo_gcp_mapping_duplicate_repo: "
                f"{repo.strip()} maps to both {parsed[normalized_repo]} and {normalized_project}"
            )
        parsed[normalized_repo] = normalized_project

    return parsed

#!/usr/bin/env python3
"""Sync planning project registries into Firestore from repo-gcp-mapping.json.

Why: Cloud Run containers don't have your local repo checkout paths, and the
container image only includes the example mapping file. Firestore is the
canonical place the planner can read project registries in production.

Writes documents like:
  collection: projects
  doc id: <project_slug> (defaults to gcp_project from the mapping file)
  fields:
    project_slug: str
    repos: [{owner,name,github_url,(optional)local_path}]
    synced_at: ISO timestamp
    source: "repo-gcp-mapping.json"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _default_gcp_project() -> str:
    env_project = os.environ.get("GCP_PROJECT_ID", "").strip()
    if env_project:
        return env_project

    try:
        result = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return ""
    return (result.stdout or "").strip()


def _required_str(value: Any, *, field: str, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"❌ ERROR: mapping_entry_invalid[{index}]: {field} must be a string")
    return value.strip()


def _parse_owner_name(repo_full: str) -> tuple[str, str]:
    parts = repo_full.split("/", 1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1]
    # Fallback: treat as name only (owner empty).
    return "", repo_full


def _load_mapping(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"❌ ERROR: mapping_file_missing ({path})")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"❌ ERROR: mapping_file_parse_failed ({path}): {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError("❌ ERROR: mapping_file_invalid: expected JSON array")
    entries: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"❌ ERROR: mapping_file_invalid_entry[{index}]: expected object")
        entries.append(entry)
    return entries


def _build_registries(
    entries: list[dict[str, Any]],
    *,
    include_local_paths: bool,
) -> dict[str, dict[str, Any]]:
    by_slug: dict[str, dict[str, Any]] = {}
    seen_repo_keys_by_slug: dict[str, set[str]] = {}
    for index, entry in enumerate(entries):
        repo_full = _required_str(entry.get("repo"), field="repo", index=index)
        slug = _required_str(entry.get("gcp_project"), field="gcp_project", index=index)

        owner, name = _parse_owner_name(repo_full)
        repo_key = f"{owner}/{name}".lower()
        if slug not in by_slug:
            by_slug[slug] = {
                "project_slug": slug,
                "repos": [],
                "source": "repo-gcp-mapping.json",
                "synced_at": _now_iso(),
            }
            seen_repo_keys_by_slug[slug] = set()

        if repo_key in seen_repo_keys_by_slug[slug]:
            continue
        seen_repo_keys_by_slug[slug].add(repo_key)

        repo_doc: dict[str, Any] = {
            "owner": owner,
            "name": name,
        }
        github_url = entry.get("github_url") or (f"https://github.com/{owner}/{name}" if owner else None)
        if isinstance(github_url, str) and github_url.strip():
            repo_doc["github_url"] = github_url.strip()
        if include_local_paths:
            local_path = entry.get("local_path")
            if isinstance(local_path, str) and local_path.strip():
                repo_doc["local_path"] = local_path.strip()

        by_slug[slug]["repos"].append(repo_doc)

    # Stable order: sort repos by owner/name
    for slug, doc in by_slug.items():
        doc["repos"] = sorted(
            doc.get("repos", []),
            key=lambda r: (str(r.get("owner", "")).lower(), str(r.get("name", "")).lower()),
        )
    return by_slug


def _firestore_client(project_id: str):
    try:
        from google.cloud import firestore  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError("❌ ERROR: google-cloud-firestore is not installed") from exc
    return firestore.Client(project=project_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync planning project registries into Firestore from repo-gcp-mapping.json.",
    )
    parser.add_argument(
        "--firestore-project-id",
        default="",
        help="GCP project id that hosts Firestore (defaults to gcloud active project).",
    )
    parser.add_argument(
        "--collection",
        default="projects",
        help="Firestore collection name for project registries.",
    )
    parser.add_argument(
        "--mapping-file",
        default="docs/repo-gcp-mapping.json",
        help="Path to repo-gcp-mapping.json.",
    )
    parser.add_argument(
        "--include-local-paths",
        action="store_true",
        help="Include local_path fields (not recommended for production Cloud Run).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes to Firestore (default is dry-run).",
    )
    args = parser.parse_args(argv)

    firestore_project_id = (args.firestore_project_id or "").strip() or _default_gcp_project()
    if not firestore_project_id:
        print(
            "❌ ERROR: missing Firestore project id. Pass --firestore-project-id or run "
            "`gcloud config set project <PROJECT_ID>`.",
            file=sys.stderr,
        )
        return 2

    mapping_path = Path(args.mapping_file).expanduser()
    entries = _load_mapping(mapping_path)
    registries = _build_registries(entries, include_local_paths=bool(args.include_local_paths))
    if not registries:
        print("❌ ERROR: no project registries found in mapping file.", file=sys.stderr)
        return 2

    slugs = sorted(registries.keys())
    print(f"Firestore project: {firestore_project_id}")
    print(f"Collection: {args.collection}")
    print(f"Mapping file: {mapping_path}")
    print(f"Projects to upsert: {len(slugs)}")
    for slug in slugs[:30]:
        repos = registries[slug].get("repos", [])
        print(f"- {slug}: {len(repos)} repo(s)")
    if len(slugs) > 30:
        print(f"... ({len(slugs) - 30} more)")

    if not args.apply:
        print("Dry-run only. Re-run with --apply to write.")
        return 0

    db = _firestore_client(firestore_project_id)
    batch = db.batch()
    for slug in slugs:
        ref = db.collection(args.collection).document(slug)
        batch.set(ref, registries[slug], merge=True)
    batch.commit()
    print("Sync complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

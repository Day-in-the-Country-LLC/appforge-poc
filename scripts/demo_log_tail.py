#!/usr/bin/env python3
"""Tail Cloud Run webhook logs and colorize rows by target GCP project."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_SERVICES = ("appforge-webhooks", "appforge-webhooks-worker")
DEFAULT_COLOR_HEX = "DCDCDC"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stream webhook logs and colorize by target_gcp_project."
    )
    parser.add_argument(
        "--project",
        required=True,
        help="GCP project that hosts Cloud Run webhook services (for Cloud Logging queries).",
    )
    parser.add_argument(
        "--mapping-file",
        default="docs/repo-gcp-mapping.json",
        help="Path to repo->gcp mapping JSON with optional color values.",
    )
    parser.add_argument(
        "--service",
        action="append",
        dest="services",
        help=(
            "Cloud Run service name to include. "
            f"Repeatable. Defaults to: {', '.join(DEFAULT_SERVICES)}"
        ),
    )
    parser.add_argument(
        "--workflow-id",
        help="Optional workflow_id filter applied client-side.",
    )
    parser.add_argument(
        "--issue-key",
        help="Optional issue_key filter applied client-side.",
    )
    parser.add_argument(
        "--target-gcp-project",
        help="Optional target_gcp_project filter applied client-side.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color output.",
    )
    return parser.parse_args()


def load_project_colors(mapping_file: str) -> dict[str, str]:
    path = Path(mapping_file).expanduser()
    if not path.is_file():
        raise ValueError(f"❌ ERROR: mapping_file_missing ({path})")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"❌ ERROR: mapping_file_parse_failed ({path}): {exc}") from exc

    if not isinstance(raw, list) or not raw:
        raise ValueError("❌ ERROR: mapping_file_invalid: expected a non-empty JSON array")

    color_by_project: dict[str, str] = {}
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"❌ ERROR: mapping_file_invalid_entry[{index}]: expected object")

        gcp_project = entry.get("gcp_project")
        if not isinstance(gcp_project, str) or not gcp_project.strip():
            raise ValueError(
                "❌ ERROR: mapping_file_invalid_entry["
                f"{index}]: gcp_project must be a non-empty string"
            )
        project_key = gcp_project.strip()

        color_value = entry.get("color", DEFAULT_COLOR_HEX)
        if not isinstance(color_value, str) or not color_value.strip():
            raise ValueError(
                "❌ ERROR: mapping_file_invalid_entry["
                f"{index}]: color must be a non-empty hex string when provided"
            )
        normalized_color = normalize_hex_color(color_value)
        existing = color_by_project.get(project_key)
        if existing and existing != normalized_color:
            raise ValueError(
                "❌ ERROR: mapping_file_conflicting_project_colors: "
                f"{project_key} has both {existing} and {normalized_color}"
            )
        color_by_project[project_key] = normalized_color

    return color_by_project


def normalize_hex_color(value: str) -> str:
    cleaned = value.strip().lstrip("#")
    if len(cleaned) == 3:
        cleaned = "".join(ch * 2 for ch in cleaned)
    if len(cleaned) != 6:
        raise ValueError(f"❌ ERROR: invalid_color_hex ({value})")
    try:
        int(cleaned, 16)
    except ValueError as exc:
        raise ValueError(f"❌ ERROR: invalid_color_hex ({value})") from exc
    return cleaned.upper()


def hex_to_rgb(color_hex: str) -> tuple[int, int, int]:
    normalized = normalize_hex_color(color_hex)
    return (
        int(normalized[0:2], 16),
        int(normalized[2:4], 16),
        int(normalized[4:6], 16),
    )


def build_filter(services: list[str]) -> str:
    if not services:
        raise ValueError("❌ ERROR: at least one service must be provided")
    service_filter = " OR ".join(f'resource.labels.service_name="{svc}"' for svc in services)
    return (
        'resource.type="cloud_run_revision" '
        f"AND ({service_filter}) "
        "AND jsonPayload.stage:* "
        "AND jsonPayload.workflow_id:*"
    )


def build_tail_command(project: str, filter_expr: str) -> list[str]:
    format_expr = (
        "csv[no-heading](timestamp,"
        "resource.labels.service_name,"
        "jsonPayload.target_gcp_project,"
        "jsonPayload.stage,"
        "jsonPayload.resolution,"
        "jsonPayload.issue_key,"
        "jsonPayload.workflow_id)"
    )
    return [
        "gcloud",
        "logging",
        "tail",
        filter_expr,
        "--project",
        project,
        "--format",
        format_expr,
    ]


def colorize(text: str, color_hex: str, *, no_color: bool) -> str:
    if no_color:
        return text
    red, green, blue = hex_to_rgb(color_hex)
    return f"\x1b[38;2;{red};{green};{blue}m{text}\x1b[0m"


def should_keep_row(
    *,
    issue_key: str,
    workflow_id: str,
    target_gcp_project: str,
    args: argparse.Namespace,
) -> bool:
    if args.issue_key and issue_key != args.issue_key:
        return False
    if args.workflow_id and workflow_id != args.workflow_id:
        return False
    if args.target_gcp_project and target_gcp_project != args.target_gcp_project:
        return False
    return True


def main() -> int:
    args = parse_args()
    services = args.services if args.services else list(DEFAULT_SERVICES)

    try:
        color_by_project = load_project_colors(args.mapping_file)
        filter_expr = build_filter(services)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    command = build_tail_command(args.project, filter_expr)
    print("Streaming logs with filter:", filter_expr, file=sys.stderr)
    print("Command:", " ".join(command), file=sys.stderr)

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    assert process.stderr is not None

    try:
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = next(csv.reader([line]))
            except Exception:
                print(line)
                continue

            # Expected order:
            # timestamp, service, target_gcp_project, stage, resolution, issue_key, workflow_id
            row += [""] * (7 - len(row))
            timestamp, service, target_project, stage, resolution, issue_key, workflow_id = row[:7]
            target_project = target_project or "unknown"
            resolution = resolution or "-"

            if not should_keep_row(
                issue_key=issue_key,
                workflow_id=workflow_id,
                target_gcp_project=target_project,
                args=args,
            ):
                continue

            color_hex = color_by_project.get(target_project, DEFAULT_COLOR_HEX)
            target_rendered = colorize(target_project, color_hex, no_color=args.no_color)
            print(
                f"{timestamp} | {service} | {target_rendered} | "
                f"{stage} | {resolution} | {issue_key} | {workflow_id}"
            )
    except KeyboardInterrupt:
        pass
    finally:
        if process.poll() is None:
            process.terminate()
        stderr_output = process.stderr.read().strip()
        if stderr_output:
            print(stderr_output, file=sys.stderr)

    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())

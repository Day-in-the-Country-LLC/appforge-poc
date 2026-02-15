#!/usr/bin/env python3
"""Unified Appforge dashboard with Observe and Plan tabs."""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import streamlit as st
from ace_dashboard import AppConfig
from ace_dashboard import apply_style as apply_planning_style
from ace_dashboard import parse_args as parse_planning_args
from ace_dashboard import run_planner_app as render_plan_tab

DEFAULT_SERVICES = ("appforge-webhooks", "appforge-webhooks-worker")
DEFAULT_COLOR_HEX = "DCDCDC"
MAX_ROWS = 2000
APP_BACKGROUND_HEX = "0D1321"


@dataclass
class TailWorker:
    process: subprocess.Popen[str]
    thread: threading.Thread
    stop_event: threading.Event


def _default_project() -> str:
    env_project = os.environ.get("GCP_PROJECT_ID", "").strip()
    if env_project:
        return env_project

    try:
        result = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return ""

    return (result.stdout or "").strip()


def parse_app_args() -> tuple[argparse.Namespace, AppConfig]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--project",
        default="",
        help="GCP project hosting Cloud Run services",
    )
    parser.add_argument("--mapping-file", default="docs/repo-gcp-mapping.json")
    parser.add_argument("--services", default="appforge-webhooks,appforge-webhooks-worker")
    args, planner_argv = parser.parse_known_args()
    if not args.project.strip():
        args.project = _default_project()
    return args, parse_planning_args(list(planner_argv))


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
        normalized = normalize_hex_color(color_value)
        existing = color_by_project.get(project_key)
        if existing and existing != normalized:
            raise ValueError(
                "❌ ERROR: mapping_file_conflicting_project_colors: "
                f"{project_key} has both {existing} and {normalized}"
            )
        color_by_project[project_key] = normalized

    return color_by_project


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


def append_row(row: dict[str, str]) -> None:
    with st.session_state.row_lock:
        st.session_state.rows.append(row)
        if len(st.session_state.rows) > MAX_ROWS:
            st.session_state.rows = st.session_state.rows[-MAX_ROWS:]


def emit_system(message: str, *, color_hex: str = "FFFFFF") -> None:
    append_row(
        {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "service": "system",
            "target_gcp_project": "system",
            "stage": "system",
            "resolution": "-",
            "issue_key": message,
            "workflow_id": "-",
            "color_hex": color_hex,
        }
    )


def stream_logs_worker(
    *,
    process: subprocess.Popen[str],
    stop_event: threading.Event,
    color_by_project: dict[str, str],
) -> None:
    assert process.stdout is not None
    assert process.stderr is not None

    try:
        for raw_line in process.stdout:
            if stop_event.is_set():
                break

            line = raw_line.strip()
            if not line:
                continue

            try:
                parsed = next(csv.reader([line]))
            except Exception:
                emit_system(f"unparsed_log_line: {line}", color_hex="FFCC66")
                continue

            parsed += [""] * (7 - len(parsed))
            (
                timestamp,
                service,
                target_project,
                stage,
                resolution,
                issue_key,
                workflow_id,
            ) = parsed[:7]
            target_project = target_project or "unknown"
            resolution = resolution or "-"
            color_hex = color_by_project.get(target_project, DEFAULT_COLOR_HEX)

            append_row(
                {
                    "timestamp": timestamp,
                    "service": service,
                    "target_gcp_project": target_project,
                    "stage": stage,
                    "resolution": resolution,
                    "issue_key": issue_key,
                    "workflow_id": workflow_id,
                    "color_hex": color_hex,
                }
            )
    finally:
        stderr_output = process.stderr.read().strip()
        if stderr_output:
            emit_system(stderr_output, color_hex="FF8888")


def start_worker(*, project: str, mapping_file: str, services: list[str]) -> None:
    color_by_project = load_project_colors(mapping_file)
    filter_expr = build_filter(services)
    command = build_tail_command(project, filter_expr)

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    stop_event = threading.Event()
    thread = threading.Thread(
        target=stream_logs_worker,
        kwargs={
            "process": process,
            "stop_event": stop_event,
            "color_by_project": color_by_project,
        },
        daemon=True,
    )
    thread.start()

    st.session_state.worker = TailWorker(process=process, thread=thread, stop_event=stop_event)
    st.session_state.running = True
    emit_system("stream_started", color_hex="88FF88")
    emit_system(f"filter={filter_expr}", color_hex="88CCFF")


def stop_worker() -> None:
    worker: TailWorker | None = st.session_state.worker
    if worker is None:
        st.session_state.running = False
        return

    worker.stop_event.set()
    if worker.process.poll() is None:
        worker.process.terminate()
        try:
            worker.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            worker.process.kill()
    worker.thread.join(timeout=2)
    st.session_state.worker = None
    st.session_state.running = False
    emit_system("stream_stopped", color_hex="FFCC66")


def initialize_state() -> None:
    if "rows" not in st.session_state:
        st.session_state.rows = []
    if "row_lock" not in st.session_state:
        st.session_state.row_lock = threading.Lock()
    if "worker" not in st.session_state:
        st.session_state.worker = None
    if "running" not in st.session_state:
        st.session_state.running = False


def render_rows(
    rows: list[dict[str, Any]],
    *,
    workflow_filter: str,
    issue_filter: str,
    target_project_filter: str,
) -> None:
    filtered = []
    for row in rows:
        if workflow_filter and row["workflow_id"] != workflow_filter:
            continue
        if issue_filter and row["issue_key"] != issue_filter:
            continue
        if target_project_filter and row["target_gcp_project"] != target_project_filter:
            continue
        filtered.append(row)

    if not filtered:
        st.info("No rows match the current filters yet.")
        return

    for row in reversed(filtered[-300:]):
        line = (
            f"{row['timestamp']} | {row['service']} | {row['target_gcp_project']} | "
            f"{row['stage']} | {row['resolution']} | {row['issue_key']} | {row['workflow_id']}"
        )
        safe_line = html.escape(line)
        st.markdown(
            (
                "<div style='font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, "
                f"Consolas, monospace; color: #{row['color_hex']}; white-space: pre-wrap;'>"
                f"{safe_line}</div>"
            ),
            unsafe_allow_html=True,
        )


def apply_log_style() -> None:
    st.markdown(
        f"""
<style>
  .stApp {{
    background-color: #{APP_BACKGROUND_HEX};
  }}
  [data-testid="stSidebar"] {{
    background-color: #{APP_BACKGROUND_HEX};
  }}
</style>
""",
        unsafe_allow_html=True,
    )


def render_observe_tab(args: argparse.Namespace) -> None:
    if not args.project:
        st.error(
            "❌ ERROR: project is required for log streaming. Pass --project or set GCP_PROJECT_ID."
        )
        return

    initialize_state()
    st.title("ACE Live Log Stream")
    st.caption("Streams listener + worker logs and colorizes rows by target_gcp_project.")

    with st.sidebar:
        st.subheader("Stream Config")
        project = st.text_input("GCP Project", value=args.project)
        mapping_file = st.text_input("Mapping File", value=args.mapping_file)
        services_raw = st.text_input("Services (comma-separated)", value=args.services)
        services = [svc.strip() for svc in services_raw.split(",") if svc.strip()]
        if not services:
            services = list(DEFAULT_SERVICES)

        col_start, col_stop, col_clear = st.columns(3)
        if col_start.button("Start", use_container_width=True):
            if st.session_state.running:
                st.warning("Stream already running.")
            else:
                try:
                    start_worker(project=project, mapping_file=mapping_file, services=services)
                except Exception as exc:
                    emit_system(str(exc), color_hex="FF8888")
                    st.error(str(exc))

        if col_stop.button("Stop", use_container_width=True):
            stop_worker()

        if col_clear.button("Clear", use_container_width=True):
            with st.session_state.row_lock:
                st.session_state.rows = []

        st.markdown("---")
        st.subheader("View Filters")
        workflow_filter = st.text_input("workflow_id")
        issue_filter = st.text_input("issue_key")
        target_project_filter = st.text_input("target_gcp_project")

    status = "running" if st.session_state.running else "stopped"
    st.write(f"Status: **{status}**")
    with st.session_state.row_lock:
        snapshot = list(st.session_state.rows)
    st.write(f"Buffered rows: **{len(snapshot)}** (max {MAX_ROWS})")

    render_rows(
        snapshot,
        workflow_filter=workflow_filter.strip(),
        issue_filter=issue_filter.strip(),
        target_project_filter=target_project_filter.strip(),
    )

    # Lightweight auto-refresh while streaming.
    if st.session_state.running:
        time.sleep(1.5)
        st.rerun()


def _render_tab_selector() -> str:
    if hasattr(st, "segmented_control"):
        selected = st.sidebar.segmented_control(
            "Tab",
            options=["Observe", "Plan"],
            key="dashboard_tab",
            default="Plan",
            label_visibility="collapsed",
            width="stretch",
        )
    else:  # pragma: no cover - fallback for older streamlit versions
        selected = st.sidebar.radio(
            "Tab",
            ["Observe", "Plan"],
            key="dashboard_tab",
            label_visibility="collapsed",
        )
    return selected or "Plan"


def main() -> None:
    observe_args, planning_config = parse_app_args()

    st.set_page_config(page_title="Appforge Dashboard", layout="wide")

    tab = _render_tab_selector()

    if tab == "Plan":
        apply_planning_style()
        render_plan_tab(planning_config)
        return

    apply_log_style()
    render_observe_tab(observe_args)


if __name__ == "__main__":
    main()

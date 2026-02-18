#!/usr/bin/env python3
"""Planning tab for the Appforge dashboard — intake, polling, and issue approval."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import streamlit as st

from ace.config.secrets import load_secret

_CONFIG_FILE_NAMES = ("config.local.json",)
_FALLBACK_PLANNER_URL = "https://appforge-webhooks-gchmaqkvia-uc.a.run.app"


def _load_local_config() -> dict[str, Any]:
    """Load dashboard config from config.local.json next to the scripts dir."""
    for name in _CONFIG_FILE_NAMES:
        path = Path(__file__).resolve().parent.parent / name
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        cwd_path = Path.cwd() / name
        if cwd_path.is_file():
            try:
                return json.loads(cwd_path.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


@dataclass
class AppConfig:
    planner_url: str
    planner_token: str | None = None
    planner_token_secret: str | None = None
    planner_password_secret: str = "APPFORGE_PLANNER_DASHBOARD_PASSWORD"
    planner_secret_version: str = "latest"
    planner_secret_project_id: str | None = None
    planner_credentials_file: str | None = None
    poll_interval_seconds: int = 2
    auto_refresh: bool = False


class PlannerApiError(RuntimeError):
    """Raised for non-success responses from the planner API."""


class PlannerApiClient:
    """Small typed wrapper around planning endpoints."""

    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        # Intake and planning calls can legitimately take longer than the default.
        self._timeout = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=30.0)

    def _headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with httpx.Client(base_url=self.base_url, timeout=self._timeout) as client:
            response = client.request(
                method,
                path,
                headers=self._headers(),
                params=params,
                json=body,
            )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = response.text.strip() or response.reason_phrase
            raise PlannerApiError(
                f"❌ ERROR: planner API request failed ({response.status_code}) {body}"
            ) from exc

        try:
            return response.json()
        except ValueError as exc:  # pragma: no cover - response contract test path
            raise PlannerApiError("❌ ERROR: planner API returned non-JSON response") from exc

    def create_session(self, *, project_slug: str, request_text: str) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/planning/sessions",
            body={
                "project_slug": project_slug,
                "request_text": request_text,
            },
        )

    def send_message(
        self,
        *,
        session_id: str,
        content: str,
        source: str = "user",
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/planning/sessions/{session_id}/messages",
            body={"content": content, "source": source},
        )

    def start_session(self, *, session_id: str) -> dict[str, Any]:
        return self._request_json("POST", f"/planning/sessions/{session_id}:start")

    def approve_session_issues(
        self,
        *,
        session_id: str,
        issues: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/planning/sessions/{session_id}/issues/approve",
            body={"issues": issues},
        )

    def get_session(self, *, session_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/planning/sessions/{session_id}")

    def get_events(self, *, session_id: str, after: str | None = None) -> dict[str, Any]:
        params = None if after is None else {"after": after}
        return self._request_json("GET", f"/planning/sessions/{session_id}/events", params=params)

    def get_messages(self, *, session_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/planning/sessions/{session_id}/messages")

    def get_artifacts(self, *, session_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/planning/sessions/{session_id}/artifacts")

    def list_projects(self) -> list[str]:
        result = self._request_json("GET", "/planning/projects")
        return result.get("projects", [])


def _detect_gcp_project_id() -> str | None:
    """Return the active GCP project from env or gcloud CLI."""
    from_env = os.environ.get("GCP_PROJECT_ID", "").strip()
    if from_env:
        return from_env
    try:
        result = subprocess.run(
            ["gcloud", "config", "get-value", "project", "--quiet"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def parse_args(argv: list[str] | None = None) -> AppConfig:
    cfg = _load_local_config()
    parser = argparse.ArgumentParser(description="Run the ACE planning dashboard.")
    parser.add_argument(
        "--planner-url",
        default=cfg.get("planner_url", _FALLBACK_PLANNER_URL),
        help="Planner API base URL",
    )
    parser.add_argument(
        "--planner-api-token",
        default=cfg.get("planner_api_token", ""),
        help="Optional bearer token to skip password login",
    )
    parser.add_argument(
        "--planner-token-secret",
        default=cfg.get("planner_token_secret", "APPFORGE_PLANNER_API_TOKEN"),
        help="Secret Manager secret name for planner bearer token",
    )
    parser.add_argument(
        "--planner-password-secret",
        default=cfg.get("planner_password_secret", "APPFORGE_PLANNER_DASHBOARD_PASSWORD"),
        help="Secret Manager secret name for dashboard login password",
    )
    parser.add_argument(
        "--planner-secret-project-id",
        default=cfg.get("planner_secret_project_id", ""),
        help="GCP project for Secret Manager (auto-detected from gcloud if omitted)",
    )
    parser.add_argument(
        "--planner-secret-version",
        default=cfg.get("planner_secret_version", "latest"),
        help="Version for password/token secrets",
    )
    parser.add_argument(
        "--planner-secret-credentials-file",
        default=cfg.get("planner_credentials_file", ""),
        help="Optional service account JSON used to read Secret Manager",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=cfg.get("poll_interval_seconds", 2),
        help="Polling interval in seconds",
    )
    parser.add_argument(
        "--auto-refresh",
        action="store_true",
        default=cfg.get("auto_refresh", False),
        help="Enable periodic event polling",
    )
    args = parser.parse_args(argv)
    if args.poll_interval_seconds < 1:
        args.poll_interval_seconds = 2
    token = args.planner_api_token.strip() or None
    project_id = (args.planner_secret_project_id or "").strip() or _detect_gcp_project_id()
    credentials_path = (args.planner_secret_credentials_file or "").strip() or None
    return AppConfig(
        planner_url=args.planner_url.rstrip("/"),
        planner_token=token,
        planner_token_secret=(args.planner_token_secret or "").strip() or None,
        planner_password_secret=(args.planner_password_secret or "").strip(),
        planner_secret_version=(args.planner_secret_version or "latest").strip() or "latest",
        planner_secret_project_id=project_id,
        planner_credentials_file=credentials_path,
        poll_interval_seconds=args.poll_interval_seconds,
        auto_refresh=args.auto_refresh,
    )


def initialize_state() -> None:
    st.session_state.setdefault("active_session_id", None)
    st.session_state.setdefault("active_session", None)
    st.session_state.setdefault("events", [])
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("next_event_cursor", None)
    st.session_state.setdefault("message_error", "")
    st.session_state.setdefault("message_success", "")
    st.session_state.setdefault("issue_approval_result", None)
    st.session_state.setdefault("last_event_refresh", 0.0)
    st.session_state.setdefault("login_error", "")
    st.session_state.setdefault("planner_api_token", None)
    st.session_state.setdefault("planner_authenticated", False)


def clear_session() -> None:
    st.session_state["active_session_id"] = None
    st.session_state["active_session"] = None
    st.session_state["events"] = []
    st.session_state["messages"] = []
    st.session_state["next_event_cursor"] = None
    st.session_state["message_error"] = ""
    st.session_state["message_success"] = ""
    st.session_state["issue_approval_result"] = None


def clear_planner_auth() -> None:
    st.session_state["planner_api_token"] = None
    st.session_state["planner_authenticated"] = False
    clear_session()


@st.cache_data(ttl=300, show_spinner=False)
def _cached_load_secret(
    project_id: str,
    secret_name: str,
    version: str,
    credentials_file: str | None,
) -> str:
    """Load a secret from GCP Secret Manager, cached for 5 minutes."""
    return load_secret(project_id, secret_name, version, credentials_file).strip()


def _planner_token_from_secret(config: AppConfig) -> str | None:
    if not config.planner_secret_project_id:
        raise PlannerApiError(
            "❌ ERROR: Planner secret project id is required for password/token login. "
            "Set --planner-secret-project-id or --planner-secret-project env var."
        )
    if not config.planner_token_secret:
        raise PlannerApiError(
            "❌ ERROR: Planner token secret name is not configured. Set --planner-token-secret."
        )
    try:
        return _cached_load_secret(
            config.planner_secret_project_id,
            config.planner_token_secret,
            config.planner_secret_version,
            config.planner_credentials_file,
        )
    except Exception as exc:
        raise PlannerApiError(
            f"❌ ERROR: failed to load planner token secret ({config.planner_token_secret}): {exc}"
        ) from exc


def _planner_password_from_secret(config: AppConfig) -> str | None:
    if not config.planner_secret_project_id:
        raise PlannerApiError(
            "❌ ERROR: Planner secret project id is required for password login. "
            "Set --planner-secret-project-id or --planner-secret-project env var."
        )
    if not config.planner_password_secret:
        raise PlannerApiError(
            "❌ ERROR: Planner password secret name is not configured. "
            "Set --planner-password-secret."
        )
    try:
        return _cached_load_secret(
            config.planner_secret_project_id,
            config.planner_password_secret,
            config.planner_secret_version,
            config.planner_credentials_file,
        )
    except Exception as exc:
        raise PlannerApiError(
            "❌ ERROR: failed to load planner password secret "
            f"({config.planner_password_secret}): {exc}"
        ) from exc


def _login_with_password(config: AppConfig, password: str) -> None:
    """Verify password against Secret Manager, then fetch bearer token from Secret Manager."""
    expected_password = _planner_password_from_secret(config)
    if not expected_password:
        raise PlannerApiError("❌ ERROR: stored dashboard password is empty in Secret Manager")

    if not hmac.compare_digest(password.strip(), expected_password):
        raise PlannerApiError("❌ ERROR: invalid dashboard password")

    token = _planner_token_from_secret(config)
    if not token:
        raise PlannerApiError("❌ ERROR: API token secret is empty in Secret Manager")

    st.session_state["planner_api_token"] = token
    st.session_state["planner_authenticated"] = True


def apply_style() -> None:
    st.markdown(
        """
<style>
  header[data-testid="stHeader"] {
    display: none !important;
  }
  #MainMenu {
    display: none !important;
  }
  footer {
    display: none !important;
  }
  .stApp {
    background: radial-gradient(circle at top left, #1f2a44 0%, #141a2a 45%, #0d121d 100%);
    color: #eef2ff;
  }
  .page-title {
    font-family: "Trebuchet MS", "Segoe UI", "Helvetica Neue", sans-serif;
    font-size: 2.2rem;
    letter-spacing: 0.02em;
    color: #f8fbff;
  }
  .subtle {
    color: #9fb0ce;
    font-size: 0.92rem;
  }
  .session-card {
    border: 1px solid #384b72;
    border-radius: 12px;
    padding: 16px;
    background: rgba(22, 31, 53, 0.86);
    margin-bottom: 14px;
  }
  .status-pill {
    display: inline-block;
    border-radius: 999px;
    padding: 4px 10px;
    font-size: 0.82rem;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    background: #3c4f7a;
    color: #ebf0ff;
  }
  .event-card {
    border: 1px solid #2f4061;
    border-radius: 10px;
    background: rgba(15, 20, 34, 0.8);
    padding: 10px;
    margin-bottom: 8px;
  }
  .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  }
</style>
""",
        unsafe_allow_html=True,
    )


def set_flash(message: str, *, kind: str = "success") -> None:
    if kind == "error":
        st.session_state["message_error"] = message
        st.session_state["message_success"] = ""
    else:
        st.session_state["message_success"] = message
        st.session_state["message_error"] = ""


def render_messages() -> None:
    if st.session_state["message_error"]:
        st.error(st.session_state["message_error"])
        st.session_state["message_error"] = ""
    if st.session_state["message_success"]:
        st.info(st.session_state["message_success"])
        st.session_state["message_success"] = ""


def render_issue_approval_results() -> None:
    approval_result = st.session_state.get("issue_approval_result")
    if not approval_result:
        return

    approved = approval_result.get("approved", [])
    failed = approval_result.get("failed", [])

    if approved:
        st.success(f"Approved {len(approved)} issue(s).")
        with st.expander("Approved issues", expanded=True):
            for issue in approved:
                repo = str(issue.get("repo", "")).strip()
                number = issue.get("number")
                issue_id = str(issue.get("issue_id", "")).strip()
                label = f"{repo}#{number}" if number is not None else "unknown"
                if issue_id:
                    label = f"{label} ({issue_id})"
                st.markdown(f"- ✅ {label}")

    if failed:
        st.error(f"Failed to approve {len(failed)} issue(s).")
        with st.expander("Failed issues", expanded=True):
            for issue in failed:
                repo = str(issue.get("repo", "")).strip()
                number = issue.get("number")
                issue_id = str(issue.get("issue_id", "")).strip()
                error = str(issue.get("error", "unknown error"))
                label = f"{repo}#{number}" if number is not None else "unknown"
                if issue_id:
                    label = f"{label} ({issue_id})"
                st.markdown(f"- ❌ {label}: {error}")

    st.session_state["issue_approval_result"] = None


def load_session(api: PlannerApiClient) -> None:
    session_id = st.session_state.get("active_session_id")
    if not session_id:
        return
    try:
        session = api.get_session(session_id=session_id)
    except PlannerApiError as exc:
        set_flash(f"Failed to load session: {exc}", kind="error")
        clear_session()
        return
    st.session_state["active_session"] = session


def load_events(api: PlannerApiClient, *, force: bool = False) -> None:
    session_id = st.session_state.get("active_session_id")
    if not session_id:
        return

    if force:
        st.session_state["next_event_cursor"] = None
        st.session_state["events"] = []

    after = st.session_state.get("next_event_cursor")
    try:
        event_page = api.get_events(session_id=session_id, after=after)
    except PlannerApiError as exc:
        set_flash(f"Failed to load events: {exc}", kind="error")
        return

    events = event_page.get("events") or []
    if after is None:
        st.session_state["events"] = events
    else:
        st.session_state["events"].extend(events)
    st.session_state["next_event_cursor"] = event_page.get("next_cursor")


def load_messages(api: PlannerApiClient) -> None:
    session_id = st.session_state.get("active_session_id")
    if not session_id:
        return
    try:
        page = api.get_messages(session_id=session_id)
    except PlannerApiError as exc:
        set_flash(f"Failed to load messages: {exc}", kind="error")
        return
    st.session_state["messages"] = page.get("messages") or []


def _load_projects_from_mapping() -> list[str]:
    """Return distinct gcp_project names from repo-gcp-mapping.json."""
    mapping_path = Path(__file__).resolve().parent / "docs" / "repo-gcp-mapping.json"
    if not mapping_path.is_file():
        mapping_path = Path.cwd() / "docs" / "repo-gcp-mapping.json"
    if not mapping_path.is_file():
        return []
    try:
        entries = json.loads(mapping_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    seen: dict[str, None] = {}
    for entry in entries:
        name = (entry.get("gcp_project") or "").strip()
        if name and name not in seen:
            seen[name] = None
    return list(seen)


def create_session_form(api: PlannerApiClient) -> None:
    st.divider()
    st.subheader("Create planning session")

    projects = _load_projects_from_mapping()

    with st.form("planning_session_form"):
        if projects:
            project_slug = st.selectbox("Select Project", options=projects)
        else:
            project_slug = st.text_input(
                "Select Project",
                value="",
                help="No projects found. Check docs/repo-gcp-mapping.json.",
            )
        request_text = st.text_area(
            "What should the planner work on?",
            value="",
            height=120,
            placeholder="Plan the next release with measurable success criteria.",
        )
        create = st.form_submit_button("Create session")

    if create:
        if not project_slug.strip() or not request_text.strip():
            set_flash("Project slug and request text are required.", kind="error")
            st.rerun()
        else:
            try:
                with st.spinner("Creating session and waiting for intake agent..."):
                    session = api.create_session(
                        project_slug=project_slug.strip(),
                        request_text=request_text.strip(),
                    )
            except httpx.ConnectError:
                set_flash(
                    f"Cannot reach the planner API at {api.base_url}.",
                    kind="error",
                )
                st.rerun()
            except httpx.ReadTimeout:
                set_flash(
                    "Planner API timed out waiting for intake agent response. "
                    "Please retry; if this persists, check Cloud Run logs.",
                    kind="error",
                )
                st.rerun()
            except PlannerApiError as exc:
                set_flash(f"Failed to create session: {exc}", kind="error")
                st.rerun()
            except Exception as exc:
                set_flash(
                    f"Unexpected error creating session ({type(exc).__name__}): {exc}",
                    kind="error",
                )
                st.rerun()
            else:
                st.session_state["active_session_id"] = session["id"]
                st.session_state["active_session"] = session
                st.session_state["events"] = []
                st.session_state["messages"] = []
                st.session_state["next_event_cursor"] = None
                st.session_state["last_event_refresh"] = 0.0
                load_events(api, force=True)
                load_messages(api)
                set_flash("Session created. Continue intake in the chat below.")
                st.rerun()


def render_session_header(session: dict[str, Any]) -> None:
    status = session.get("status", "unknown")
    st.markdown(
        f"""
<div class="session-card">
  <div class="page-title">Session {session.get("id", "—")}</div>
  <div class="subtle">Project: {session.get("project_slug", "—")}</div>
  <p><span class="status-pill">Status: {status}</span></p>
  <p class="subtle mono">{session.get("request_text", "")}</p>
</div>
""",
        unsafe_allow_html=True,
    )


def render_conversation() -> None:
    st.subheader("Intake conversation")
    messages = st.session_state.get("messages") or []
    if not messages:
        st.info("No intake messages yet.")
        return

    for message in messages:
        source = str(message.get("source", "assistant")).strip().lower()
        role = "assistant" if source != "user" else "user"
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        with st.chat_message(role):
            st.markdown(content)


def render_intake_chat_input(api: PlannerApiClient, session: dict[str, Any]) -> None:
    session_id = session.get("id")
    if not session_id:
        return
    status = str(session.get("status", "")).strip().lower()
    if status != "intake_pending":
        if status == "ready_to_run":
            st.info("Intake complete. Planning starts automatically.")
        return

    st.markdown("<hr/>", unsafe_allow_html=True)
    st.subheader("Reply to intake agent")
    with st.form("intake_chat_form"):
        content = st.text_area(
            "Your message",
            value="",
            height=90,
            placeholder="Type your response for the intake agent...",
        )
        posted = st.form_submit_button("Send message")
    if not posted:
        return

    if not str(content).strip():
        set_flash("Message cannot be empty.", kind="error")
        return
    try:
        with st.spinner("Waiting for intake agent response..."):
            api.send_message(
                session_id=session_id,
                content=str(content).strip(),
                source="user",
            )
    except httpx.ConnectError:
        set_flash(
            f"Cannot reach the planner API at {api.base_url}.",
            kind="error",
        )
        return
    except httpx.ReadTimeout:
        set_flash(
            "Planner API timed out waiting for intake agent response. "
            "Please retry; if this persists, check Cloud Run logs.",
            kind="error",
        )
        return
    except PlannerApiError as exc:
        set_flash(f"Failed to send message: {exc}", kind="error")
        return
    except Exception as exc:
        set_flash(
            f"Unexpected error sending message ({type(exc).__name__}): {exc}",
            kind="error",
        )
        return

    load_session(api)
    load_events(api)
    load_messages(api)
    set_flash("Message sent.")
    st.rerun()


def render_planning_status(session: dict[str, Any]) -> None:
    status = str(session.get("status", "unknown")).strip().lower()
    intake_state = session.get("intake_state") or {}
    intake_in_progress = bool(
        intake_state.get("turn_in_progress") if isinstance(intake_state, dict) else False
    )
    if status.startswith("running"):
        st.info("⏳ Planning is in progress…")
        st.progress(100, text=f"Status: {status}")
        return
    if status == "intake_pending":
        if intake_in_progress:
            st.info("⏳ Intake agent is processing your latest message…")
            return
        st.info("Intake is in progress. Planning starts automatically when intake completes.")
        return
    if status == "ready_to_run":
        st.info("Intake complete. Planning is queued automatically.")
        return


def render_events(
    api: PlannerApiClient,
    poll_interval: int,
    auto_refresh: bool,
) -> None:
    session = st.session_state.get("active_session") or {}
    status = session.get("status", "unknown")
    is_running = status.startswith("running")
    intake_state = session.get("intake_state") or {}
    intake_in_progress = bool(
        intake_state.get("turn_in_progress") if isinstance(intake_state, dict) else False
    )

    st.subheader("Events")
    if st.button("Refresh events now"):
        load_events(api, force=True)
        load_session(api)
        st.rerun()

    if not st.session_state["events"]:
        if is_running:
            st.info("Waiting for planning events…")
        elif intake_in_progress:
            st.info("Waiting for intake progress events…")
        else:
            st.info("No events yet.")

    st.caption(f"Loaded events: {len(st.session_state['events'])}")
    for event in reversed(st.session_state["events"]):
        event_type = event.get("event_type", "unknown")
        payload = event.get("payload", {})
        payload_render = json.dumps(payload, indent=2, sort_keys=True)
        ts = event.get("created_at", "")
        st.markdown(
            f"""
<div class="event-card">
  <strong>{event_type}</strong>
  <div class="subtle mono">{ts}</div>
  <pre class="mono">{payload_render}</pre>
</div>
""",
            unsafe_allow_html=True,
        )

    should_poll = auto_refresh or is_running or intake_in_progress
    if should_poll:
        now = time.time()
        last = st.session_state.get("last_event_refresh", 0.0)
        interval = min(poll_interval, 3) if (is_running or intake_in_progress) else poll_interval
        if now - last >= interval:
            st.session_state["last_event_refresh"] = now
            load_events(api, force=False)
            load_session(api)
            load_messages(api)
            st.rerun()
        remaining = max(0.0, interval - (now - last))
        st.caption(f"Auto-refresh in {remaining:.0f}s." if remaining else "Auto-refreshing...")


def render_artifacts(api: PlannerApiClient, session_id: str) -> None:
    st.subheader("Artifacts")
    try:
        artifacts_payload = api.get_artifacts(session_id=session_id)
    except PlannerApiError as exc:
        set_flash(f"Failed to load artifacts: {exc}", kind="error")
        return

    artifacts = artifacts_payload.get("artifacts", [])
    if not artifacts:
        st.info("No artifacts yet.")
        return

    for artifact in artifacts:
        artifact_type = artifact.get("artifact_type", "artifact")
        content_url = artifact.get("content_url", "")
        label = f"{artifact_type} · {artifact.get('id', '')}".strip()
        if content_url:
            st.link_button(label, content_url)
        else:
            st.write(label)


def _session_created_issues() -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    for event in st.session_state.get("events", []):
        if event.get("event_type") != "issues_written":
            continue
        for issue in event.get("payload", {}).get("created", []) or []:
            repo = str(issue.get("repo", "")).strip()
            title = str(issue.get("title", "")).strip()
            issue_id = str(issue.get("issue_id", "")).strip()
            try:
                number = int(issue.get("number", 0))
            except (TypeError, ValueError):
                number = 0
            if not repo or number <= 0:
                continue
            issue_key = (repo, number)
            if issue_key in seen:
                continue
            seen.add(issue_key)
            created.append(
                {
                    "repo": repo,
                    "number": number,
                    "title": title,
                    "issue_id": issue_id,
                    "url": str(issue.get("url", "")).strip(),
                }
            )
    return created


def _is_planner_authenticated() -> bool:
    return bool(st.session_state.get("planner_api_token")) and bool(
        st.session_state.get("planner_authenticated")
    )


def _set_direct_token(token: str) -> None:
    st.session_state["planner_api_token"] = token
    st.session_state["planner_authenticated"] = True


def render_auth_sidebar(config: AppConfig) -> None:
    st.sidebar.title("Planner")
    st.sidebar.markdown(f"**API URL**  \n`{config.planner_url}`")
    if config.planner_secret_project_id:
        st.sidebar.caption(f"GCP project: `{config.planner_secret_project_id}`")

    if st.session_state.get("planner_authenticated") and st.session_state.get("planner_api_token"):
        st.sidebar.success("Signed in")
        if st.sidebar.button("Sign out"):
            clear_planner_auth()
            st.rerun()


def render_issue_approval(api: PlannerApiClient, session: dict[str, Any]) -> None:
    session_id = session.get("id")
    if not session_id:
        return

    created_issues = _session_created_issues()
    if not created_issues:
        return

    st.subheader("Created Issues")
    st.caption("Bulk-approve planning-created issues and send them to the Ready project queue.")

    options = []
    issue_by_option: dict[str, dict[str, Any]] = {}
    for index, issue in enumerate(created_issues, start=1):
        issue_label = issue["title"] or issue["issue_id"]
        option = f"{index}. {issue['repo']}#{issue['number']} · {issue_label}"
        options.append(option)
        issue_by_option[option] = issue

    with st.form("issue_approval_form"):
        selected = st.multiselect(
            "Issues to approve",
            options=options,
            default=options,
            help="Move selected issues to the configured Ready status in your GitHub Project.",
        )
        approved = st.form_submit_button("Approve selected issues")

    if not approved:
        return

    if not selected:
        set_flash("Select at least one issue to approve.", kind="error")
        return

    approve_payload: list[dict[str, Any]] = []
    for key in selected:
        issue = issue_by_option[key]
        approve_payload.append(
            {
                "issue_id": issue.get("issue_id"),
                "repo": issue["repo"],
                "number": issue["number"],
                "title": issue.get("title"),
            }
        )

    try:
        result = api.approve_session_issues(
            session_id=session_id,
            issues=approve_payload,
        )
    except PlannerApiError as exc:
        set_flash(f"Failed to approve issues: {exc}", kind="error")
        return

    st.session_state["issue_approval_result"] = result
    set_flash(
        "Issue approval requested. Review per-item results below.",
    )
    load_events(api)
    st.rerun()


def render_planning_page(api: PlannerApiClient, config: AppConfig) -> None:
    st.markdown("<div class='page-title'>ACE Planning Dashboard</div>", unsafe_allow_html=True)
    st.caption("Create planning sessions, chat through intake, and monitor progress events.")

    render_messages()

    if st.session_state["active_session_id"] is None:
        create_session_form(api)
        return

    session = st.session_state.get("active_session") or {}
    render_session_header(session)

    if not session:
        load_session(api)
        load_messages(api)
        session = st.session_state.get("active_session") or {}
        if not session:
            clear_session()
            st.rerun()
    elif not st.session_state.get("messages"):
        load_messages(api)

    col_left, col_right = st.columns([2, 1])
    with col_left:
        render_conversation()
        render_intake_chat_input(api, session)
        render_planning_status(session)
    with col_right:
        render_events(api, config.poll_interval_seconds, config.auto_refresh)
        render_artifacts(api, session.get("id", ""))
        render_issue_approval(api, session)
        render_issue_approval_results()

    st.button("Reset session", on_click=clear_session, type="secondary")


def render_sidebar(config: AppConfig) -> None:
    st.sidebar.checkbox(
        "Auto-refresh events",
        value=config.auto_refresh,
        key="planner_auto_refresh",
    )
    config.auto_refresh = st.session_state["planner_auto_refresh"]
    st.sidebar.slider(
        "Poll interval (seconds)",
        min_value=1,
        max_value=20,
        value=config.poll_interval_seconds,
        key="planner_poll_interval",
    )
    config.poll_interval_seconds = st.session_state["planner_poll_interval"]
    if st.sidebar.button("Clear active session"):
        clear_session()
        st.rerun()


def run_planner_app(config: AppConfig) -> None:
    """Render the planning dashboard. Called from the unified dashboard."""
    initialize_state()

    if config.planner_token:
        _set_direct_token(config.planner_token)
        st.info("Using CLI-provided planner token.")

    render_auth_sidebar(config)
    render_sidebar(config)
    if config.auto_refresh != st.session_state["planner_auto_refresh"]:
        config.auto_refresh = st.session_state["planner_auto_refresh"]
    if config.poll_interval_seconds != st.session_state["planner_poll_interval"]:
        config.poll_interval_seconds = st.session_state["planner_poll_interval"]

    api: PlannerApiClient | None = None
    if _is_planner_authenticated() and st.session_state.get("planner_api_token"):
        api = PlannerApiClient(
            config.planner_url,
            token=str(st.session_state["planner_api_token"]).strip(),
        )

    if not api:
        st.markdown(
            "<div class='page-title'>ACE Planning Dashboard</div>",
            unsafe_allow_html=True,
        )
        st.markdown("---")
        st.subheader("Sign in to continue")

        login_err = st.session_state.get("login_error", "")
        if login_err:
            st.markdown(
                f'<p style="color:#f87171;font-size:1.1rem;font-weight:bold;">{login_err}</p>',
                unsafe_allow_html=True,
            )

        if not config.planner_secret_project_id:
            st.warning(
                "Could not detect a GCP project for Secret Manager.\n\n"
                "Run `gcloud config set project <PROJECT>` or set "
                "`GCP_PROJECT_ID`, then refresh.\n\n"
                "Or pass `--planner-api-token` to skip password login."
            )
        else:
            st.caption(f"GCP project: `{config.planner_secret_project_id}`")
            st.text_input("Dashboard password", type="password", key="login_pw")
            if st.button("Sign in", type="primary"):
                password = st.session_state.get("login_pw", "")
                if not password.strip():
                    st.session_state["login_error"] = "Password is required."
                    st.rerun()
                else:
                    with st.spinner("Signing in..."):
                        try:
                            _login_with_password(config, password)
                        except Exception as exc:
                            st.session_state["login_error"] = (
                                f"Login failed ({type(exc).__name__}): {exc}"
                            )
                    if _is_planner_authenticated():
                        st.session_state["login_error"] = ""
                        st.rerun()
                    else:
                        st.rerun()
        return

    if api and st.session_state["active_session_id"]:
        load_session(api)
        load_events(api)

    render_planning_page(api, config)

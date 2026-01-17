"""Simple in-memory metrics registry with Prometheus text output."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class _Summary:
    count: int = 0
    total: float = 0.0


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._definitions: dict[str, tuple[str, str]] = {}
        self._counters: dict[tuple[str, str], float] = {}
        self._gauges: dict[tuple[str, str], float] = {}
        self._summaries: dict[tuple[str, str], _Summary] = {}
        self._task_starts: dict[str, float] = {}

    def define(self, name: str, help_text: str, metric_type: str) -> None:
        with self._lock:
            self._definitions[name] = (help_text, metric_type)

    def inc_counter(self, name: str, value: float = 1.0, labels: dict | None = None) -> None:
        key = (name, _label_str(labels))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + value

    def inc_gauge(self, name: str, value: float = 1.0, labels: dict | None = None) -> None:
        key = (name, _label_str(labels))
        with self._lock:
            self._gauges[key] = self._gauges.get(key, 0.0) + value

    def dec_gauge(self, name: str, value: float = 1.0, labels: dict | None = None) -> None:
        self.inc_gauge(name, -value, labels=labels)

    def set_gauge(self, name: str, value: float, labels: dict | None = None) -> None:
        key = (name, _label_str(labels))
        with self._lock:
            self._gauges[key] = value

    def observe_summary(self, name: str, value: float, labels: dict | None = None) -> None:
        key = (name, _label_str(labels))
        with self._lock:
            summary = self._summaries.get(key)
            if summary is None:
                summary = _Summary()
                self._summaries[key] = summary
            summary.count += 1
            summary.total += value

    def task_started(self, issue_number: int | None, task_id: str) -> None:
        if issue_number is None:
            return
        key = f"{issue_number}:{task_id}"
        with self._lock:
            self._task_starts.setdefault(key, time.time())

    def task_completed(self, issue_number: int | None, task_id: str) -> None:
        if issue_number is None:
            return
        key = f"{issue_number}:{task_id}"
        start_time = None
        with self._lock:
            start_time = self._task_starts.pop(key, None)
        if start_time is not None:
            self.observe_summary("ace_task_duration_seconds", time.time() - start_time)
        self.inc_counter("ace_task_completed_total")

    def render_prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            definitions = dict(self._definitions)
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            summaries = dict(self._summaries)

        for name, (help_text, metric_type) in definitions.items():
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {metric_type}")

            if metric_type == "counter":
                for (metric_name, label_str), value in sorted(counters.items()):
                    if metric_name == name:
                        lines.append(f"{metric_name}{label_str} {value}")
            elif metric_type == "gauge":
                for (metric_name, label_str), value in sorted(gauges.items()):
                    if metric_name == name:
                        lines.append(f"{metric_name}{label_str} {value}")
            elif metric_type == "summary":
                for (metric_name, label_str), summary in sorted(summaries.items()):
                    if metric_name == name:
                        lines.append(f"{metric_name}_sum{label_str} {summary.total}")
                        lines.append(f"{metric_name}_count{label_str} {summary.count}")

        return "\n".join(lines) + "\n"


def _label_str(labels: dict | None) -> str:
    if not labels:
        return ""
    items = sorted((str(k), str(v)) for k, v in labels.items())
    inner = ",".join(f'{k}="{v}"' for k, v in items)
    return f"{{{inner}}}"


metrics = MetricsRegistry()

metrics.define("ace_agent_runs_total", "Agent runs by status/backend.", "counter")
metrics.define("ace_agent_duration_seconds", "Agent run duration in seconds.", "summary")
metrics.define("ace_active_agents", "Active agents currently running.", "gauge")
metrics.define("ace_task_completed_total", "Tasks completed.", "counter")
metrics.define("ace_task_duration_seconds", "Task duration in seconds.", "summary")
metrics.define("ace_task_nudges_total", "Task nudges sent.", "counter")
metrics.define("ace_task_restarts_total", "Task session restarts.", "counter")
metrics.define("ace_task_wait_timeout_total", "Task wait timeouts.", "counter")
metrics.define("ace_task_nudge_exceeded_total", "Tasks exceeded nudge limit.", "counter")
metrics.define("ace_task_validation_failed_total", "Task validation failures.", "counter")

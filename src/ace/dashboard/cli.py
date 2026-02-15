"""CLI entry point for the unified ACE dashboard."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    """Launch the unified Streamlit dashboard via ``uv run appforge-dashboard``."""
    script = Path(__file__).resolve().parents[3] / "scripts" / "appforge_dashboard.py"
    if not script.exists():
        print(f"❌ ERROR: Dashboard script not found at {script}", file=sys.stderr)
        sys.exit(1)
    cmd = ["streamlit", "run", str(script)]
    if sys.argv[1:]:
        cmd += ["--"] + sys.argv[1:]
    sys.exit(subprocess.call(cmd))

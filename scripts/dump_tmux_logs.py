#!/usr/bin/env python

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ace.workspaces.tmux_ops import TmuxOps, session_name_for_issue


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dump tmux session output for ACE agents.")
    parser.add_argument(
        "--session",
        help="Full tmux session name (if omitted, repo+issue are used).",
    )
    parser.add_argument("--repo", help="Repo slug used to build the tmux session name.")
    parser.add_argument("--issue", help="Issue number used to build the tmux session name.")
    parser.add_argument(
        "--lines",
        type=int,
        default=400,
        help="Number of trailing lines to capture (default: 400; 0 for full buffer).",
    )
    parser.add_argument(
        "--out",
        help="Optional path to write the captured output instead of stdout.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = args.session
    if not session:
        if not (args.repo and args.issue):
            print("Provide --session or both --repo and --issue.", file=sys.stderr)
            return 1
        session = session_name_for_issue(str(args.repo), str(args.issue))

    tmux = TmuxOps()
    if not tmux.session_exists(session):
        print(f"tmux session '{session}' not found.", file=sys.stderr)
        return 2

    try:
        output = tmux.capture_session_output(session, lines=args.lines)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 3

    if args.out:
        path = Path(args.out)
        path.write_text(output, encoding="utf-8")
        print(f"Wrote tmux output to {path}")
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

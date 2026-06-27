#!/usr/bin/env python3
"""Print a markdown trace summary for posting as a PR comment.

Usage:
    python scripts/witness_pr_comment.py [TRACE_ID]

If TRACE_ID is omitted, the most recently started trace is used. The script
prints the markdown to stdout so a CI step can capture it and post it as a
PR comment (for example via the GitHub CLI).
"""

from __future__ import annotations

import sys

from sqlmodel import select

from witness import report, storage


def _latest_trace_id() -> str | None:
    with storage.get_session() as s:
        row = s.exec(
            select(storage.Trace).order_by(storage.Trace.started_at.desc())
        ).first()
        return row.id if row is not None else None


def main(argv: list[str]) -> int:
    storage.init_db()

    trace_id = argv[1] if len(argv) > 1 and argv[1] else None
    if trace_id is None:
        trace_id = _latest_trace_id()
    if trace_id is None:
        print("No traces found.", file=sys.stderr)
        return 1

    try:
        md = report.write_summary_markdown(trace_id)
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

#!/usr/bin/env python3
"""
ContextRecoveryHook - PostCompact Handler
Runs AFTER context compaction completes to:
  1. Save the compact_summary (compression summary) to CONTEXT.md
  2. Log the event
Safe to run on both auto and manual compaction.
"""
import json
import sys
from datetime import datetime
from pathlib import Path


def format_timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dir(path):
    path.mkdir(parents=True, exist_ok=True)


def read_stdin() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


def safe_read(path):
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def append_to_context(summary: str) -> None:
    """Append compact summary to CONTEXT.md."""
    context_path = Path.home() / ".claude" / "CONTEXT.md"
    if not context_path.exists():
        return
    try:
        content = context_path.read_text(encoding="utf-8")
        # Remove old compact summary section if exists
        if "## Compaction Summary" in content:
            content = content.split("## Compaction Summary")[0].rstrip()
        # Append new summary
        content += f"\n\n## Compaction Summary\n"
        content += f"_Captured at {format_timestamp()}_  \n"
        content += f"> {summary[:2000]}\n"
        context_path.write_text(content, encoding="utf-8")
    except Exception as e:
        print(f"[post_compact] WARNING: failed to update CONTEXT.md: {e}", file=sys.stderr)


def log_event(event_type, data):
    log_root = Path.home() / ".claude" / "logs"
    ensure_dir(log_root)
    log_file = log_root / "events.json"
    events = []
    if log_file.exists():
        try:
            events = json.loads(log_file.read_text(encoding="utf-8"))
        except Exception:
            events = []
    events.append({
        "type": event_type,
        "timestamp": format_timestamp(),
        **data,
    })
    events = events[-100:]
    log_file.write_text(json.dumps(events, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    input_data = read_stdin()

    session_id = input_data.get("session_id", "unknown")
    trigger = input_data.get("trigger", "unknown")
    compact_summary = input_data.get("compact_summary", "")

    log_event("post_compact", {
        "session_id": session_id,
        "trigger": trigger,
        "summary_length": len(compact_summary),
    })

    if compact_summary:
        append_to_context(compact_summary)

    sys.exit(0)


if __name__ == "__main__":
    main()

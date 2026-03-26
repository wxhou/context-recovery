#!/usr/bin/env python3
"""
ContextRecoveryHook - PostCompact Handler
Runs AFTER context compaction completes to:
  1. Save the compact_summary (compression summary) to session-specific context.md
  2. Log the event to events.jsonl (append-only)
Safe to run on both auto and manual compaction.
"""
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix="_" + path.name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def format_timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def session_dir(session_id: str) -> Path:
    """Return the per-session directory for this session_id."""
    return Path.home() / ".claude" / "sessions" / session_id


def ensure_dir(path):
    path.mkdir(parents=True, exist_ok=True)


def read_stdin() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def safe_read(path):
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def append_to_context(summary: str, s_dir: Path) -> None:
    """Append compact summary to session-specific context.md."""
    context_path = s_dir / "context.md"
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
        _atomic_write(context_path, content)
    except Exception as e:
        print(f"[post_compact] WARNING: failed to update context.md: {e}", file=sys.stderr)


def save_cycle_summary(summary: str, session_id: str, trigger: str, s_dir: Path) -> None:
    """Append compact_summary to cycle_history.jsonl for PreCompact auto-fill."""
    history_path = s_dir / "cycle_history.jsonl"
    try:
        ensure_dir(history_path.parent)
        entry = json.dumps({
            "timestamp": format_timestamp(),
            "trigger": trigger,
            "summary": summary[:5000],
        }, ensure_ascii=False)
        with open(history_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception as e:
        print(f"[post_compact] WARNING: failed to save cycle history: {e}", file=sys.stderr)


def log_event(event_type, data):
    """Append a JSONL entry to session-specific events.jsonl (append-only)."""
    log_file = Path.home() / ".claude" / "sessions" / data.get("session_id", "unknown") / "events.jsonl"
    try:
        ensure_dir(log_file.parent)
        entry = json.dumps({
            "type": event_type,
            "timestamp": format_timestamp(),
            **{k: v for k, v in data.items() if k != "session_id"},
        }, ensure_ascii=False)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception as e:
        print(f"[post_compact] WARNING: failed to log event: {e}", file=sys.stderr)


def main():
    input_data = read_stdin()

    session_id = input_data.get("session_id", "unknown")
    trigger = input_data.get("trigger", "unknown")
    compact_summary = input_data.get("compact_summary", "")

    # Per-session directory
    s_dir = session_dir(session_id)
    ensure_dir(s_dir)

    log_event("post_compact", {
        "session_id": session_id,
        "trigger": trigger,
        "summary_length": len(compact_summary),
    })

    if compact_summary:
        append_to_context(compact_summary, s_dir)
        save_cycle_summary(compact_summary, session_id, trigger, s_dir)

    sys.exit(0)


if __name__ == "__main__":
    main()

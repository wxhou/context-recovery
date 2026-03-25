#!/usr/bin/env python3
"""
ContextRecoveryHook - SessionEnd Handler
Runs when the session terminates. Performs final cleanup/sync of session state.
Note: Default timeout is 1.5s — keep this hook fast.
reason values: clear|resume|logout|prompt_input_exit|bypass_permissions_disabled|other
"""
import json
import sys
from datetime import datetime
from pathlib import Path


def format_timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def session_dir(session_id: str) -> Path:
    """Return the per-session directory for this session_id."""
    return Path.home() / ".claude" / "sessions" / session_id


def read_stdin() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


def log_event(event_type, data):
    """Append a JSONL entry to session-specific events.jsonl (append-only)."""
    log_file = Path.home() / ".claude" / "sessions" / data.get("session_id", "unknown") / "events.jsonl"
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps({
            "type": event_type,
            "timestamp": format_timestamp(),
            **{k: v for k, v in data.items() if k != "session_id"},
        }, ensure_ascii=False)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception as e:
        print(f"[session_end] WARNING: failed to log event: {e}", file=sys.stderr)


def main():
    input_data = read_stdin()

    session_id = input_data.get("session_id", "unknown")
    reason = input_data.get("reason", "unknown")

    # Log the session end event
    log_event("session_end", {
        "session_id": session_id,
        "reason": reason,
    })

    # Sync: ensure events.jsonl is flushed by touching the session dir
    s_dir = session_dir(session_id)
    marker = s_dir / ".session_ended"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"ended={format_timestamp()}\nreason={reason}\n", encoding="utf-8")
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()

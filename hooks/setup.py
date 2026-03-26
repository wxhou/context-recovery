#!/usr/bin/env python3
"""
ContextRecoveryHook - Setup Handler
Runs once on first session to initialize directory structure and template files.
Safe to run multiple times (idempotent).
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


def ensure_dir(path):
    path.mkdir(parents=True, exist_ok=True)


def write_if_missing(path, content):
    if not path.exists():
        try:
            _atomic_write(path, content)
            print(f"[setup] Created: {path}", file=sys.stdout)
            return True
        except Exception as e:
            print(f"[setup] WARNING: failed to create {path}: {e}", file=sys.stderr)
    return False


def log_event(event_type, data):
    """Append a JSONL entry to global events.jsonl."""
    log_file = Path.home() / ".claude" / "logs" / "events.jsonl"
    try:
        ensure_dir(log_file.parent)
        entry = json.dumps({
            "type": event_type,
            "timestamp": format_timestamp(),
            **data,
        }, ensure_ascii=False)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception as e:
        print(f"[setup] WARNING: failed to log event: {e}", file=sys.stderr)


def setup_init():
    """Initialize .claude/ directory structure and template files."""
    home = Path.home()
    claude_dir = home / ".claude"

    # Create directory structure
    ensure_dir(claude_dir / "logs")
    # Per-session structure (individual sessions created on demand)
    ensure_dir(claude_dir / "sessions")

    # Global TODO.md (user manual — shared across all sessions)
    todo_path = claude_dir / "TODO.md"
    write_if_missing(todo_path, f"""# TODO

<!-- last-updated: {format_timestamp()} -->

- [ ] Add your active work items here
- [ ] Format: - [ ] Task description

---

> Add unchecked items above. Completed items can be marked with [x].
""")

    # Marker file to track that setup has run
    marker_path = claude_dir / ".context-recovery-setup"
    write_if_missing(marker_path, f"setup_completed={format_timestamp()}\n")

    log_event("setup", {
        "initialized": True,
        "claude_dir": str(claude_dir),
    })

    # Output additionalContext to welcome the user
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": f"""## ContextRecoveryHook Initialized

Welcome! ContextRecoveryHook has been set up in `~/.claude/`.

**Directory structure (per-session isolation):**
- `~/.claude/sessions/<session_id>/context.md` — auto-generated context (per window)
- `~/.claude/sessions/<session_id>/events.jsonl` — event log (per window)
- `~/.claude/sessions/<session_id>/transcript_backups/` — transcript backups (per window)
- `~/.claude/TODO.md` — manual TODO list (global, shared)

**What it does:**
- PreCompact: backs up transcript + generates session-specific context.md
- PostCompact: saves compaction summary to session context
- SessionStart: injects saved context on resume/compact/startup
- Stop: appends session summary to context for future recovery

**Next:** Edit `~/.claude/TODO.md` to add your active work items!
""",
        }
    }
    print(json.dumps(output), file=sys.stdout)
    sys.exit(0)


def main():
    # Check if already set up
    marker = Path.home() / ".claude" / ".context-recovery-setup"
    if marker.exists():
        sys.exit(0)  # Already initialized, do nothing
    setup_init()


if __name__ == "__main__":
    main()

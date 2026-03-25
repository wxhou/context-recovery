#!/usr/bin/env python3
"""
ContextRecoveryHook - SessionStart Handler
Runs AFTER session starts (including resume from compaction) to:
  1. Load and inject session-specific context.md as additionalContext
  2. Load ~/.claude/TODO.md for active work items (global)
  3. Load recent transcript backup for context continuity
  4. Log the session start event to events.jsonl
"""
import json
import subprocess
import sys
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


# ── helpers ──────────────────────────────────────────────────────────────────

def session_dir(session_id: str) -> Path:
    """Return the per-session directory for this session_id."""
    return Path.home() / ".claude" / "sessions" / session_id


def read_stdin() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def safe_read(path: Path, limit: int = 0) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
        return text if limit == 0 else text[:limit]
    except Exception:
        return ""


def format_timestamp() -> None:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_git_status() -> dict:
    """Get current git branch and change info."""
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        changed = len(status.stdout.strip().splitlines()) if status.stdout.strip() else 0

        return {"branch": branch, "changed_files": changed}
    except Exception:
        return {"branch": None, "changed_files": 0}


def get_recent_backup(s_dir: Path) -> Optional[Path]:
    """Find the most recent transcript backup in the session directory."""
    backup_root = s_dir / "transcript_backups"
    if not backup_root.exists():
        return None

    backups = sorted(backup_root.glob("transcript_*.jsonl"),
                     key=lambda p: p.stat().st_mtime,
                     reverse=True)
    if not backups:
        return None

    # Only return if backup is less than 7 days old
    newest = backups[0]
    age = datetime.now() - datetime.fromtimestamp(newest.stat().st_mtime)
    if age > timedelta(days=7):
        return None
    return newest


def extract_context_from_backup(backup_path: Path, max_chars: int = 3000) -> str:
    """Extract last few exchanges from transcript backup for context continuity."""
    try:
        content = backup_path.read_text(encoding="utf-8", limit=max_chars * 2)
        lines = content.strip().splitlines()
        # Take last 20 lines max
        recent = lines[-20:] if len(lines) > 20 else lines

        # Extract just the last user/assistant exchanges
        result = []
        for line in recent:
            try:
                obj = json.loads(line)
                role = obj.get("message", {}).get("role", "")
                content_text = obj.get("message", {}).get("content", "")
                if isinstance(content_text, list):
                    for block in content_text:
                        if block.get("type") == "text":
                            text = block.get("text", "")[:300]
                            if text:
                                prefix = "👤 " if role == "user" else "🤖 "
                                result.append(f"{prefix}{text}")
                elif isinstance(content_text, str):
                    if content_text[:300]:
                        prefix = "👤 " if role == "user" else "🤖 "
                        result.append(f"{prefix}{content_text[:300]}")
            except Exception:
                pass
        return "\n".join(result[-10:])  # last 10 exchanges
    except Exception:
        return ""


def load_context_files(session_id: str) -> dict:
    """Load all context files and return a structured summary."""
    s_dir = session_dir(session_id)
    claude_dir = Path.home() / ".claude"

    context_md = safe_read(s_dir / "context.md")
    todo_md = safe_read(claude_dir / "TODO.md")  # Global TODO
    recent_backup = get_recent_backup(s_dir)

    backup_snippet = ""
    if recent_backup:
        backup_snippet = extract_context_from_backup(recent_backup)

    return {
        "context_md": context_md,
        "todo_md": todo_md,
        "recent_backup": str(recent_backup) if recent_backup else None,
        "backup_snippet": backup_snippet,
    }


def build_additional_context(data: dict, source: str, session_id: str) -> str:
    """Build the additionalContext string for SessionStart injection."""
    parts = []
    parts.append(f"## ContextRecovery: Session Start ({format_timestamp()})")
    parts.append(f"Session source: `{source}`")
    parts.append(f"Session ID: `{session_id[:8]}...`")

    # Git status
    git = get_git_status()
    if git["branch"]:
        parts.append(f"Git branch: `{git['branch']}` | Changed files: {git['changed_files']}")

    parts.append("")

    # Session-specific context.md content
    if data["context_md"]:
        parts.append("### 📋 Previous Session Context")
        parts.append("_Last updated before last compaction._")
        parts.append(data["context_md"][:2000])
        parts.append("")

    # Global TODO.md content
    if data["todo_md"]:
        parts.append("### 📌 Active TODO Items")
        # Extract just the unchecked items
        todo_lines = []
        for line in data["todo_md"].splitlines():
            line = line.strip()
            if line.startswith("- [ ]") or line.startswith("- [x]") or line.startswith("* [ ]"):
                todo_lines.append(line)
            elif line.startswith("#") or (line and not line.startswith(">")):
                if todo_lines:
                    break  # stop at next section
        if todo_lines:
            parts.extend(todo_lines[:15])
        else:
            parts.append(data["todo_md"][:500])
        parts.append("")

    # Recent transcript context
    if data["backup_snippet"]:
        parts.append("### 💬 Recent Conversation (from last backup)")
        parts.append("_Context preserved before last compaction._")
        parts.append("```")
        parts.append(data["backup_snippet"][:1500])
        parts.append("```")
        parts.append("")

    # Recovery instructions
    parts.append("### 🔄 Recovery Guidance")
    parts.append(
        "Review the context above. If the previous session was working on specific files or tasks, "
        "continue from where it left off. Check git status to understand current state."
    )

    return "\n".join(parts)


def log_event(event_type: str, data: dict) -> None:
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
        print(f"[session_start] WARNING: failed to log event: {e}", file=sys.stderr)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    input_data = read_stdin()

    session_id = input_data.get("session_id", "unknown")
    source = input_data.get("source", "unknown")

    # Ensure session directory exists
    s_dir = session_dir(session_id)
    ensure_dir(s_dir)

    # Load context files (session-specific context.md + global TODO.md)
    data = load_context_files(session_id)

    # Log the event
    log_event("session_start", {
        "session_id": session_id,
        "source": source,
        "has_context": bool(data["context_md"]),
        "has_todo": bool(data["todo_md"]),
        "recent_backup": data["recent_backup"],
    })

    # Inject additionalContext on resume and after compaction.
    # source values: startup (new), resume (--resume), clear (/clear), compact (after compaction)
    if source in ("resume", "compact", "startup") and (data["context_md"] or data["todo_md"]):
        additional = build_additional_context(data, source, session_id)
        output = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": additional,
            }
        }
        sys.stdout.flush()
        sys.stderr.flush()
        print(json.dumps(output), flush=True)
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
ContextRecoveryHook - Stop Handler
Runs when Claude finishes responding (session end). Extracts a structured
session summary and writes it to session-specific context.md as a permanent record.
"""
import json
import os
import re
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


def read_stdin() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def safe_read(path, limit=0):
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
        return text if limit == 0 else text[:limit]
    except Exception:
        return ""


def safe_write(path, content):
    try:
        _atomic_write(path, content)
    except Exception as e:
        print(f"[stop] WARNING: failed to write {path}: {e}", file=sys.stderr)


def extract_recent_work(transcript_path, session_id):
    """Extract recent prompts and files from the current session transcript."""
    transcript = safe_read(Path(transcript_path), limit=30_000)
    if not transcript:
        return [], []

    # Parse JSONL properly — each line is a JSON object
    prompts = []
    files = []

    for line in transcript.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue

        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            continue

        role = msg.get("role", "")
        if role != "user":
            continue

        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if len(text) > 10:
                        prompts.append(text[:400])
        elif isinstance(content, str):
            if len(content) > 10:
                prompts.append(content[:400])

    # Deduplicate consecutive duplicates
    cleaned = []
    for p in prompts:
        if not cleaned or p != cleaned[-1]:
            cleaned.append(p)

    # Extract file paths from full transcript
    file_exts = r'[\w\-\./]+\.(py|ts|tsx|js|jsx|md|json|yaml|yml|go|rs|java|cpp|c|h|toml)\b'
    found_files = re.findall(file_exts, transcript)
    seen = {}
    for f in found_files:
        if f not in seen:
            seen[f] = True
    unique_files = list(seen.keys())[-15:]

    return cleaned, unique_files


def extract_from_last_message(msg: str) -> list:
    """Extract meaningful content from the last assistant message."""
    if not msg:
        return [], []
    # Extract file paths
    files = re.findall(
        r'[\w\-\./]+\.(py|ts|tsx|js|jsx|md|json|yaml|yml|go|rs|java|cpp|c|h|toml)\b',
        msg
    )
    # Extract key action hints (tool names, error mentions, etc.)
    actions = []
    # Look for what was done
    if "Created" in msg or "created" in msg:
        actions.append("_Files were created_")
    if "Edited" in msg or "edited" in msg:
        actions.append("_Files were edited_")
    if "Deleted" in msg or "deleted" in msg:
        actions.append("_Files were deleted_")
    if "Error" in msg or "error" in msg:
        actions.append("_Errors occurred_")
    if "Completed" in msg or "completed" in msg:
        actions.append("_Tasks were completed_")
    return list(dict.fromkeys(files))[:10], actions


def build_session_summary(session_id, transcript_path, source="unknown", last_message=""):
    """Build a structured session summary."""
    prompts, files = extract_recent_work(transcript_path, session_id)
    msg_files, msg_actions = extract_from_last_message(last_message)

    # Merge files from transcript and last message
    all_files = list(dict.fromkeys(files + msg_files))[-15:]

    lines = [
        f"## Session Summary ({format_timestamp()})",
        f"_Session: {session_id[:8]}..._",
        f"_Triggered by: {source}_",
        "",
    ]

    if prompts:
        lines.append("### Recent Activity")
        for p in prompts[-5:]:
            escaped = p.replace("|", "\\|").replace("\n", " ")[:300]
            lines.append(f"- {escaped}")

    if msg_actions:
        lines.append("")
        lines.append("### Session Outcomes")
        for action in msg_actions:
            lines.append(f"- {action}")

    if all_files:
        lines.append("")
        lines.append("### Files Touched")
        lines.append(", ".join(f"`{f}`" for f in all_files))

    return "\n".join(lines)


def append_to_context(summary, s_dir):
    """Append session summary to session-specific context.md."""
    context_path = s_dir / "context.md"
    if not context_path.exists():
        return

    try:
        content = context_path.read_text(encoding="utf-8")

        # Remove old session summary section if exists
        if "## Session Summary" in content:
            parts = content.split("## Session Summary")
            content = parts[0].rstrip()

        # Keep header + recovery notes, append summary
        content = content.rstrip() + "\n\n" + summary
        _atomic_write(context_path, content)
    except Exception as e:
        print(f"[stop] WARNING: failed to update context.md: {e}", file=sys.stderr)


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
        print(f"[stop] WARNING: failed to log event: {e}", file=sys.stderr)


def main():
    input_data = read_stdin()

    session_id = input_data.get("session_id", "unknown")
    transcript_path = input_data.get("transcript_path", "")

    # Per-session directory
    s_dir = session_dir(session_id)

    # Check stop_hook_active to avoid infinite loop
    stop_hook_active = input_data.get("stop_hook_active", False)
    if stop_hook_active:
        sys.exit(0)

    # Capture last_assistant_message for richer summary
    last_message = input_data.get("last_assistant_message", "")

    # Extract session summary
    summary = build_session_summary(session_id, transcript_path, last_message=last_message)
    append_to_context(summary, s_dir)

    log_event("stop", {
        "session_id": session_id,
        "transcript_path": transcript_path,
        "last_message_length": len(last_message),
    })

    sys.exit(0)


if __name__ == "__main__":
    main()

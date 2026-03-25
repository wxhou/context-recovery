#!/usr/bin/env python3
"""
ContextRecoveryHook - Stop Handler
Runs when Claude finishes responding (session end). Extracts a structured
session summary and writes it to CONTEXT.md as a permanent record.
"""
import json
import sys
import re
from datetime import datetime
from pathlib import Path


def format_timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_stdin() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except Exception as e:
        print(f"[stop] WARNING: failed to write {path}: {e}", file=sys.stderr)


def extract_recent_work(transcript_path, session_id):
    """Extract recent prompts and files from the current session transcript."""
    transcript = safe_read(Path(transcript_path), limit=30_000)
    if not transcript:
        return [], []

    # Extract user prompts
    prompts = re.findall(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', transcript)
    recent = []
    for p in prompts[-8:]:
        p = p.encode().decode("unicode_escape", errors="replace")
        p = p.replace("\\n", "\n").replace('\\"', '"')
        if len(p) > 10:
            recent.append(p[:400])
    # Deduplicate consecutive duplicates
    cleaned = []
    for p in recent:
        if not cleaned or p != cleaned[-1]:
            cleaned.append(p)

    # Extract file paths
    files = re.findall(
        r'[\w\-\./]+\.(py|ts|tsx|js|jsx|md|json|yaml|yml|go|rs|java|cpp|c|h|toml)\b',
        transcript
    )
    unique_files = list(dict.fromkeys(files))[-15:]

    return cleaned, unique_files


def build_session_summary(session_id, transcript_path, source="unknown"):
    """Build a structured session summary."""
    prompts, files = extract_recent_work(transcript_path, session_id)

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

    if files:
        lines.append("")
        lines.append("### Files Touched")
        lines.append(", ".join(f"`{f}`" for f in files))

    return "\n".join(lines)


def append_to_context(summary):
    """Append session summary to CONTEXT.md."""
    context_path = Path.home() / ".claude" / "CONTEXT.md"
    if not context_path.exists():
        # Only create if we're in a real session with transcript
        return

    try:
        content = context_path.read_text(encoding="utf-8")

        # Remove old session summary section if exists
        if "## Session Summary" in content:
            parts = content.split("## Session Summary")
            content = parts[0].rstrip()

        # Keep header + recovery notes, append summary
        content = content.rstrip() + "\n\n" + summary
        context_path.write_text(content, encoding="utf-8")
    except Exception as e:
        print(f"[stop] WARNING: failed to update CONTEXT.md: {e}", file=sys.stderr)


def log_event(event_type, data):
    log_root = Path.home() / ".claude" / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
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
    transcript_path = input_data.get("transcript_path", "")

    # Check stop_hook_active to avoid infinite loop
    stop_hook_active = input_data.get("stop_hook_active", False)
    if stop_hook_active:
        sys.exit(0)

    # Extract session summary
    summary = build_session_summary(session_id, transcript_path)
    append_to_context(summary)

    log_event("stop", {
        "session_id": session_id,
        "transcript_path": transcript_path,
    })

    sys.exit(0)


if __name__ == "__main__":
    main()

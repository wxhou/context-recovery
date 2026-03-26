#!/usr/bin/env python3
"""
ContextRecoveryHook - PreCompact Handler
Runs BEFORE context compaction to:
  1. Backup the full transcript
  2. Generate a structured context summary (session-specific context.md)
  3. Update TODO.md with current work state
  4. Log the event to events.jsonl (append-only)
"""
import argparse
import json
import re
import shutil
import sys
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
    try:
        return json.loads(raw)
    except Exception:
        return {}


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def safe_read(path: Path, limit: int = 0) -> str:
    """Read file safely. If limit > 0, truncate to that many chars."""
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
        return text if limit == 0 else text[:limit]
    except Exception:
        return ""


def safe_write(path: Path, content: str) -> None:
    try:
        ensure_dir(path.parent)
        path.write_text(content, encoding="utf-8")
    except Exception as e:
        print(f"[pre_compact] WARNING: failed to write {path}: {e}", file=sys.stderr)


def format_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── transcript path derivation ────────────────────────────────────────────────

def find_transcript(session_id: str, cwd: str) -> Optional[Path]:
    """
    Find the transcript file for a session.

    Claude Code stores transcripts at:
      ~/.claude/projects/{cwd.replace('/','-')}/{session_id}.jsonl

    Fallback: search all project directories for matching session_id.
    """
    home = Path.home()
    projects_dir = home / ".claude" / "projects"

    # Method 1: derive from cwd
    if cwd:
        sanitized = cwd.replace("/", "-")
        derived = projects_dir / sanitized / f"{session_id}.jsonl"
        if derived.exists():
            return derived

    # Method 2: search all project directories
    if projects_dir.exists():
        for proj_dir in projects_dir.iterdir():
            if proj_dir.is_dir():
                for f in proj_dir.glob("*.jsonl"):
                    # session_id is the filename without extension
                    if f.stem == session_id:
                        return f
                    # Also check ses_{session_id} pattern
                    if f.name == f"ses_{session_id}.jsonl":
                        return f

    return None


# ── core logic ────────────────────────────────────────────────────────────────

def backup_transcript(transcript_path: Path, trigger: str, s_dir: Path) -> Optional[Path]:
    """Copy transcript to session-specific backup dir with timestamp."""
    if not transcript_path.exists():
        return None

    backup_root = s_dir / "transcript_backups"
    ensure_dir(backup_root)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"transcript_{trigger}_{ts}.jsonl"
    dst = backup_root / backup_name

    try:
        shutil.copy2(transcript_path, dst)
        return dst
    except Exception as e:
        print(f"[pre_compact] WARNING: backup failed: {e}", file=sys.stderr)
        return None


def rotate_backups(backup_root, max_count=10, max_age_days=7):
    """Keep newest max_count backups and any from last max_age_days. Delete rest."""
    try:
        backups = sorted(backup_root.glob("transcript_*.jsonl"),
                         key=lambda p: p.stat().st_mtime,
                         reverse=True)
        if len(backups) <= max_count:
            return

        cutoff = datetime.now() - timedelta(days=max_age_days)
        kept = set(backups[:max_count])  # Always keep newest max_count

        # Also keep any from last max_age_days
        for p in backups:
            if datetime.fromtimestamp(p.stat().st_mtime) >= cutoff:
                kept.add(p)

        to_delete = [p for p in backups if p not in kept]
        for p in to_delete:
            try:
                p.unlink()
            except Exception:
                pass

        if to_delete:
            print(f"[pre_compact] Cleaned {len(to_delete)} old backup(s)", file=sys.stdout)
    except Exception:
        pass  # Rotation is best-effort


def extract_key_content(transcript_path: Path) -> dict:
    """Extract meaningful content from transcript for context generation."""
    transcript = safe_read(transcript_path, limit=50_000)
    if not transcript:
        return {"prompts": [], "files": []}

    # Parse JSONL properly — each line is a JSON object
    # Format: {"message": {"role": "user/assistant", "content": "..."}}
    prompts = []
    seen_files = {}

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
                        prompts.append(text[:500])
        elif isinstance(content, str):
            if len(content) > 10:
                prompts.append(content[:500])

    # Extract file paths from full transcript text
    file_exts = r'[\w\-\./]+\.(py|ts|tsx|js|jsx|md|json|yaml|yml|go|rs|java|cpp|c|h|toml)\b'
    found_files = re.findall(file_exts, transcript)
    for f in found_files:
        if f not in seen_files:
            seen_files[f] = True
    unique_files = list(seen_files.keys())[-20:]

    return {
        "prompts": prompts[-10:],
        "files": unique_files,
    }


def extract_recovery_notes(old_context: str) -> str:
    """Extract Recovery Notes section from previous context.md."""
    marker = "## Recovery Notes"
    if marker not in old_context:
        return ""

    notes_section = old_context.split(marker, 1)[1]
    # Take content until next ## or end of file
    if "## " in notes_section:
        notes_section = notes_section.split("## ", 1)[0]
    notes_section = notes_section.strip()

    # Remove template prompt lines
    lines = []
    for line in notes_section.splitlines():
        stripped = line.strip()
        if stripped.startswith("_") or stripped.startswith("<!--"):
            continue
        if stripped in ("_Add your notes here before the next session — what was in progress,",
                        "_what files to revisit, what decisions were made, etc._"):
            continue
        lines.append(line)

    result = "\n".join(lines).strip()
    return result[:500]  # Limit preserved notes to 500 chars


def generate_context_summary(data: dict, session_id: str, s_dir: Path) -> str:
    """Generate a structured context summary for session-specific context.md."""
    lines = [
        f"# Claude Code Session Context",
        f"> Auto-generated by ContextRecoveryHook at {format_timestamp()}",
        f"> **DO NOT EDIT** — this file is auto-generated before compaction.",
        "",
        "---",
        "",
        "## Session Info",
        f"- **Session ID**: `{session_id[:8]}...`",
        f"- **Generated**: {format_timestamp()}",
        "",
        "## Recent Work",
    ]

    if data["prompts"]:
        lines.append("### Recent User Requests")
        for p in data["prompts"][-5:]:
            escaped = p.replace("|", "\\|").replace("\n", " ")[:300]
            lines.append(f"- {escaped}")
    else:
        lines.append("_No recent prompts extracted._")

    if data["files"]:
        lines.append("")
        lines.append("### Files Recently Touched")
        lines.append("```")
        lines.append(", ".join(f"`{f}`" for f in data["files"]))
        lines.append("```")

    # Preserve Recovery Notes from previous cycle
    old_context = safe_read(s_dir / "context.md")
    preserved_notes = extract_recovery_notes(old_context)
    if preserved_notes:
        lines.extend([
            "",
            "### 📝 Previous Recovery Notes",
            "_Preserved from previous session — do not delete._",
            preserved_notes,
        ])

    lines.extend([
        "",
        "## Recovery Notes",
        "",
        "_Add your notes here before the next session — what was in progress,",
        "_what files to revisit, what decisions were made, etc._",
        "",
        "---",
        f"> Generated by ContextRecoveryHook · {format_timestamp()}",
    ])

    return "\n".join(lines)


def update_todo_state() -> bool:
    """Update ~/.claude/TODO.md with current timestamp to signal session continuity."""
    todo_path = Path.home() / ".claude" / "TODO.md"
    if not todo_path.exists():
        return False

    content = todo_path.read_text(encoding="utf-8")
    if "<!-- last-updated:" not in content:
        marker = f"\n<!-- last-updated: {format_timestamp()} -->\n"
        todo_path.write_text(content.rstrip() + marker, encoding="utf-8")
    else:
        content = re.sub(
            r"<!-- last-updated: [^>]+ -->",
            f"<!-- last-updated: {format_timestamp()} -->",
            content,
        )
        todo_path.write_text(content, encoding="utf-8")
    return True


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
        print(f"[pre_compact] WARNING: failed to log event: {e}", file=sys.stderr)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="ContextRecoveryHook PreCompact")
    parser.add_argument("--backup", action="store_true",
                        help="Create transcript backup")
    parser.add_argument("--generate-context", action="store_true",
                        help="Generate context.md summary")
    parser.add_argument("--verbose", action="store_true",
                        help="Print verbose output")
    args = parser.parse_args()

    input_data = read_stdin()

    session_id = input_data.get("session_id", "unknown")
    transcript_path = input_data.get("transcript_path", "")
    cwd = input_data.get("cwd", "")
    trigger = input_data.get("trigger", "unknown")
    custom_instructions = input_data.get("custom_instructions", "")

    # Per-session directory
    s_dir = session_dir(session_id)
    ensure_dir(s_dir)

    results = {}

    # Resolve transcript path: prefer explicit path, derive if empty
    resolved_transcript: Optional[Path] = None
    if transcript_path:
        p = Path(transcript_path).expanduser()
        if p.exists():
            resolved_transcript = p

    if resolved_transcript is None and session_id != "unknown":
        resolved_transcript = find_transcript(session_id, cwd)

    if args.backup and resolved_transcript:
        backup_path = backup_transcript(resolved_transcript, trigger, s_dir)
        if backup_path:
            results["backup"] = str(backup_path)
            if args.verbose:
                print(f"Transcript backed up to: {backup_path}", file=sys.stdout)
            rotate_backups(s_dir / "transcript_backups")
        else:
            if args.verbose:
                print(f"Transcript not found: tried {resolved_transcript}", file=sys.stdout)

    if args.generate_context and resolved_transcript:
        data = extract_key_content(resolved_transcript)
        summary = generate_context_summary(data, session_id, s_dir)
        context_path = s_dir / "context.md"
        safe_write(context_path, summary)
        results["context_generated"] = str(context_path)
        if args.verbose:
            print(f"Context summary written to: {context_path}", file=sys.stdout)
    elif args.generate_context:
        # No transcript: still generate from existing context (preserve notes)
        data = {"prompts": [], "files": []}
        summary = generate_context_summary(data, session_id, s_dir)
        context_path = s_dir / "context.md"
        safe_write(context_path, summary)
        results["context_generated"] = str(context_path)
        if args.verbose:
            print(f"No transcript found — context.md updated (Recovery Notes preserved)", file=sys.stdout)

    # 3. Update TODO state
    update_todo_state()

    # 4. Log event
    log_event("pre_compact", {
        "session_id": session_id,
        "trigger": trigger,
        "custom_instructions": custom_instructions,
        "results": results,
        "transcript_found": resolved_transcript is not None,
    })

    if args.verbose:
        print(f"PreCompact handler done. trigger={trigger}, session={session_id[:8]}...",
              file=sys.stdout)

    sys.exit(0)


if __name__ == "__main__":
    main()

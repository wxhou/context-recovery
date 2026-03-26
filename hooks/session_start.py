#!/usr/bin/env python3
"""
ContextRecoveryHook - SessionStart Handler
Runs AFTER session starts (including resume from compaction) to:
  1. Load and inject session-specific context.md as additionalContext
  2. Load ~/.claude/TODO.md for active work items (global)
  3. Load recent transcript backup for context continuity
  4. On /clear transition: restore handoff from previous session
  5. Log the session start event to events.jsonl
"""
import json
import subprocess
import sys
import re
from datetime import datetime
from pathlib import Path
from typing import Optional


# ── helpers ──────────────────────────────────────────────────────────────────

def session_dir(session_id: str) -> Path:
    """Return the per-session directory for this session_id."""
    return Path.home() / ".claude" / "sessions" / session_id


def latest_dir() -> Path:
    """Return the latest-handoff directory keyed by project."""
    return Path.home() / ".claude" / "sessions" / "latest"


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
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
        return text if limit == 0 else text[:limit]
    except Exception:
        return ""


def format_timestamp() -> str:
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


def _extract_section(context_md: str, section_start: str) -> str:
    """Extract a named section from context.md.

    Finds the section heading, returns content until the next top-level
    ## heading (i.e., ``\n## `` = newline + ## + space at line start).
    Returns empty string if not found.
    """
    marker = f"\n{section_start}"
    if marker not in context_md:
        return ""
    after_marker = context_md.split(marker, 1)[1]

    # Split on \n##  — each top-level heading creates a new part
    parts = after_marker.split("\n## ")
    # The first part (parts[0]) is the section body
    # If parts[1] starts with ##, that's the next section — our section ends here
    return parts[0].strip()


def _extract_user_notes(context_md: str) -> str:
    """Extract the user's handwritten notes from the ## Recovery Notes section.

    Strips template prompts, auto-fill blocks, and the Previous Recovery Notes
    subsection (which is shown separately as "Notes from Previous Cycle").
    Returns the user's actual notes for the current cycle.
    """
    notes_section = _extract_section(context_md, "## Recovery Notes")
    if not notes_section:
        return ""

    # Cut off at Previous Recovery Notes subsection boundary
    prev_notes_marker = "\n### 📝 Previous Recovery Notes"
    if prev_notes_marker in notes_section:
        notes_section = notes_section.split(prev_notes_marker, 1)[0]

    # If auto-fill is present, take everything after the --- separator
    has_auto_fill = "_**Auto-filled" in notes_section or "_Auto-filled" in notes_section
    if has_auto_fill and "---" in notes_section:
        notes_section = notes_section.split("---", 1)[1]
    elif has_auto_fill:
        notes_section = ""

    lines = []
    for line in notes_section.splitlines():
        stripped = line.strip()
        # Skip template prompts, section headers, horizontal rules, auto-fill sections
        if stripped.startswith("_Add your notes here") or stripped.startswith("_what files"):
            continue
        if stripped.startswith("_**") or stripped.startswith("_<"):
            continue
        if stripped.startswith("---") or stripped.startswith(">"):
            continue
        if stripped.startswith("**Files") or stripped.startswith("**What"):
            continue
        if stripped.startswith("**Suggested") or stripped.startswith("**Decisions"):
            continue
        if stripped.startswith("_") and stripped.endswith("_"):
            continue  # skip markdown italic lines like "_user wrote:_"
        if stripped.startswith("### 📝"):
            continue  # skip Previous Recovery Notes subsection header
        lines.append(line)

    result = "\n".join(lines).strip()
    return result[:600]


def _extract_files_touched(context_md: str) -> str:
    """Extract the Files Recently Touched section from context.md.

    Files appear under ### Files Recently Touched anywhere in the document.
    Uses reverse search so we find the most recent occurrence.
    """
    # Search for the heading anywhere in the document (reverse to find last occurrence)
    for heading in ("### Files Recently Touched",):
        # heading starts with ###; markdown headings are on their own lines
        needle = f"\n{heading}"  # "\n### Files Recently Touched"
        # Find all occurrences
        start = 0
        last_pos = -1
        while True:
            pos = context_md.find(needle, start)
            if pos == -1:
                break
            last_pos = pos
            start = pos + 1

        if last_pos >= 0:
            section = context_md[last_pos + len(needle):]
            # Skip the heading line itself
            first_newline = section.find("\n")
            if first_newline >= 0:
                section = section[first_newline:]
            # Stop at next ## heading (top-level) or ### sibling subsection
            # Use find on both to capture whichever comes first
            next_top = section.find("\n## ")
            next_sub = section.find("\n### ")
            if next_top != -1 and next_sub != -1:
                section = section[:min(next_top, next_sub)]
            elif next_top != -1:
                section = section[:next_top]
            elif next_sub != -1:
                section = section[:next_sub]
            section = section.strip()
            # Remove code fences: strip ``` pairs from start/end, collect middle content
            code_content_lines = []
            for line in section.splitlines():
                stripped = line.strip()
                if stripped == "```":
                    continue  # skip fence lines
                code_content_lines.append(stripped)
            return "\n".join(code_content_lines)
    return ""


def _extract_previous_recovery_notes(context_md: str) -> str:
    """Extract Previous Recovery Notes (from previous cycle, preserved across cycles)."""
    section = _extract_section(context_md, "### 📝 Previous Recovery Notes")
    if not section:
        return ""
    # Skip the preservation header
    lines = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("_Preserved from"):
            continue
        if stripped.startswith("---") or stripped.startswith(">"):
            continue
        lines.append(line)
    result = "\n".join(lines).strip()
    return result[:500]


def load_session_context(session_id: str) -> dict:
    """
    Load session context for SessionStart injection.

    Returns only what ContextRecoveryHook provides that claude-mem doesn't:
    - recovery_notes: user's handwritten notes from previous session
    - files_touched: files worked on (raw fact, not AI-compressed)
    - previous_notes: notes preserved from even earlier cycles
    - context_md_raw: full context.md for reference
    """
    s_dir = session_dir(session_id)
    context_md = safe_read(s_dir / "context.md")

    return {
        "recovery_notes": _extract_user_notes(context_md),
        "files_touched": _extract_files_touched(context_md),
        "previous_notes": _extract_previous_recovery_notes(context_md),
        "context_md_raw": context_md,
    }


def build_additional_context(data: dict, source: str, session_id: str) -> str:
    """
    Build compact additionalContext for SessionStart injection.

    Design: claude-mem handles semantic compression and progressive disclosure.
    ContextRecoveryHook provides: raw facts (files) + user decision records (notes).

    Claude Code's LLM handles any minor overlap between the two sources.
    """
    parts = [
        f"## ContextRecovery: Session Resume ({format_timestamp()})",
        f"_ContextRecovery: raw facts + user notes (claude-mem handles semantic layer)_",
        "",
    ]

    # Git status — always useful, never overlapping
    git = get_git_status()
    if git["branch"]:
        parts.append(f"Git: `{git['branch']}` | {git['changed_files']} changed file(s)")
    parts.append("")

    # Previous cycle's notes (preserved across compaction cycles)
    if data["previous_notes"]:
        parts.extend([
            "### 📝 Notes from Previous Cycle",
            "_Preserved across compaction — do not discard._",
            data["previous_notes"],
            "",
        ])

    # User's handwritten recovery notes
    if data["recovery_notes"]:
        parts.extend([
            "### 📋 Your Recovery Notes",
            data["recovery_notes"],
            "",
        ])

    # Files touched (raw fact, not AI-compressed)
    if data["files_touched"]:
        parts.extend([
            "### 📁 Files Recently Touched",
            data["files_touched"],
            "",
        ])

    # Compact guidance
    parts.extend([
        "### 🔄 Resume Guidance",
        "Files above are raw facts. claude-mem provides semantic understanding. "
        "If claude-mem is active, check its memory for the full picture. "
        "Otherwise, use the facts above to continue from where the last session left off.",
    ])

    return "\n".join(parts)


def load_latest_pointer(cwd: str) -> Optional[dict]:
    """Load the latest handoff pointer for a project."""
    if not cwd:
        return None
    sanitized = cwd.replace("/", "_")
    ptr = latest_dir() / sanitized / "latest.json"
    if not ptr.exists():
        return None
    try:
        return json.loads(ptr.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_handoff(session_id: str) -> str:
    """Load the handoff.md for a given session_id."""
    handoff_path = session_dir(session_id) / "handoff.md"
    if not handoff_path.exists():
        return ""
    try:
        return handoff_path.read_text(encoding="utf-8")
    except Exception:
        return ""


def build_clear_handoff_context(handoff_content: str, old_session_id: str) -> str:
    """Build compact additionalContext from a /clear handoff.

    /clear is unique to ContextRecoveryHook — claude-mem doesn't handle it.
    Include the full handoff content for continuity.
    """
    parts = [
        "## ContextRecovery: /clear Transition",
        f"_Restored from previous session `{old_session_id[:8]}...`_",
        "_claude-mem does not handle /clear — use this context to continue._",
        "",
    ]

    # Parse and format the handoff markdown
    lines = handoff_content.splitlines()
    skip_header = True  # Skip the "# Context Handoff" header
    for line in lines:
        if skip_header and line.startswith("#"):
            skip_header = False
            continue
        parts.append(line)

    parts.extend([
        "",
        "### 🔄 Resume from /clear",
        "Review the context above. The previous session captured this before /clear was issued. "
        "Continue from where it left off. claude-mem's memory may also provide semantic context — "
        "use both to get the full picture.",
    ])

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
    cwd = input_data.get("cwd", "")

    # Ensure session directory exists
    s_dir = session_dir(session_id)
    ensure_dir(s_dir)

    # ── /clear transition: restore handoff from previous session ────────────
    if source == "clear":
        ptr = load_latest_pointer(cwd)
        if ptr:
            old_session_id = ptr.get("session_id", "")
            handoff_content = load_handoff(old_session_id)
            if handoff_content:
                clear_context = build_clear_handoff_context(handoff_content, old_session_id)
                log_event("session_start", {
                    "session_id": session_id,
                    "source": source,
                    "restored_from": old_session_id,
                    "handoff_cwd": ptr.get("cwd", ""),
                    "type": "clear_handoff",
                })
                output = {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": clear_context,
                    }
                }
                sys.stdout.flush()
                sys.stderr.flush()
                print(json.dumps(output), flush=True)
                sys.exit(0)

        # No handoff found — fall through to normal startup logging
        log_event("session_start", {
            "session_id": session_id,
            "source": source,
            "handoff_found": False,
        })
        sys.exit(0)

    # Load session context: only Recovery Notes + Files (claude-mem handles semantic layer)
    data = load_session_context(session_id)

    # Log the event
    log_event("session_start", {
        "session_id": session_id,
        "source": source,
        "has_recovery_notes": bool(data["recovery_notes"]),
        "has_files": bool(data["files_touched"]),
        "has_previous_notes": bool(data["previous_notes"]),
    })

    # Inject additionalContext on resume and after compaction.
    # source values: startup (new), resume (--resume), compact (after compaction)
    has_content = data["recovery_notes"] or data["files_touched"] or data["previous_notes"]
    if source in ("resume", "compact", "startup") and has_content:
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

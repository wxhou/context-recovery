#!/usr/bin/env python3
"""
ContextRecoveryHook - SessionEnd Handler
Runs when the session terminates. Performs final cleanup/sync of session state.
reason values: clear|resume|logout|prompt_input_exit|bypass_permissions_disabled|other

/clear transition: when source=="clear", captures full context from the old transcript
and writes a handoff so the new session (after /clear) can restore it.
"""
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Set


# ── helpers ──────────────────────────────────────────────────────────────────

def format_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via temp file + rename.

    Creates parent dirs if needed. Cleans up temp file on failure.
    """
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


# ── /clear handoff extraction ────────────────────────────────────────────────

def _collect_text_from_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                t = part.get("text", "")
                if t:
                    parts.append(t)
        return "\n".join(parts)
    return ""


def _looks_like_real_file_path(value: str) -> bool:
    if not value or not value.startswith("/"):
        return False
    if "\n" in value or "\r" in value:
        return False
    for token in ("&&", "||", "|", ";", "$(", "`"):
        if token in value:
            return False
    return True


def _collect_paths_recursive(obj, paths: Set[str]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in ("file_path", "path") and isinstance(value, str):
                if _looks_like_real_file_path(value):
                    paths.add(value.strip())
            else:
                _collect_paths_recursive(value, paths)
    elif isinstance(obj, list):
        for item in obj:
            _collect_paths_recursive(item, paths)


JUNK_PATTERNS = (
    "API Error:",
    "rate_limit",
    "invalid_request_error",
    "overloaded",
    "No response requested",
    "(no content)",
)
USER_JUNK_PATTERNS = ("[Request interrupted by user]",)


def extract_handoff_context(transcript_path: Path) -> dict:
    """
    Extract structured handoff context from transcript (used for /clear transitions).
    Returns user messages, assistant snippets, and file paths.
    """
    if not transcript_path.exists():
        return {"user_messages": [], "assistant_snippets": [], "files_touched": []}

    user_messages: list = []
    assistant_snippets: list = []
    files_touched: Set[str] = set()

    try:
        content = transcript_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {"user_messages": [], "assistant_snippets": [], "files_touched": []}

    for line in content.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = obj.get("type", "")
        message = obj.get("message", {})
        if not isinstance(message, dict):
            continue

        if msg_type == "user":
            text = _collect_text_from_content(message.get("content", "")).strip()
            if text and not any(p in text for p in USER_JUNK_PATTERNS):
                user_messages.append(text)

        elif msg_type == "assistant":
            content_list = message.get("content", [])
            if isinstance(content_list, list):
                for part in content_list:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        text = part.get("text", "").strip()
                        if text and not any(p in text for p in JUNK_PATTERNS):
                            assistant_snippets.append(text[:800])
                    if part.get("type") == "tool_use":
                        _collect_paths_recursive(part.get("input", {}), files_touched)

    # Deduplicate consecutive near-duplicate messages (simple hash-based dedup)
    seen_hashes: set = set()
    deduped: list = []
    for msg in user_messages:
        h = hash(msg[:200])
        if h not in seen_hashes:
            seen_hashes.add(h)
            deduped.append(msg)

    return {
        "user_messages": deduped[-15:],
        "assistant_snippets": assistant_snippets[-10:],
        "files_touched": sorted(files_touched)[-20:],
    }


def write_clear_handoff(
    context: dict,
    session_id: str,
    cwd: str,
    transcript_path: str,
) -> Optional[Path]:
    """Write handoff.md for a /clear transition and update latest pointer."""
    s_dir = session_dir(session_id)
    s_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().isoformat()
    handoff_path = s_dir / "handoff.md"

    lines = [
        "# Context Handoff",
        "",
        f"- **Generated**: {timestamp}",
        f"- **Session**: {session_id}",
        f"- **Trigger**: SessionEnd(clear)",
        f"- **Transcript**: `{transcript_path or '(unknown)'}`",
        f"- **CWD**: `{cwd}`",
        "",
        "## Recent User Requests",
        "",
    ]

    for idx, msg in enumerate(context.get("user_messages", []), start=1):
        if len(msg) > 500:
            msg = msg[:500] + "..."
        lines.append(f"### Turn {idx}")
        lines.append("```")
        lines.append(msg)
        lines.append("```")
        lines.append("")

    files = context.get("files_touched", [])
    if files:
        lines.append("## Files Touched")
        lines.append("")
        for path in files:
            lines.append(f"- `{path}`")
        lines.append("")

    snippets = context.get("assistant_snippets", [])
    if snippets:
        lines.append("## Recent Assistant Context")
        lines.append("")
        for snippet in snippets[-5:]:
            if len(snippet) > 300:
                snippet = snippet[:300] + "..."
            lines.append(f"> {snippet}")
            lines.append("")

    try:
        _atomic_write(handoff_path, "\n".join(lines))
    except Exception as e:
        print(f"[session_end] WARNING: failed to write handoff: {e}", file=sys.stderr)
        return None

    # Update latest pointer keyed by project (cwd)
    _update_latest_pointer(session_id, cwd)

    return handoff_path


def _update_latest_pointer(session_id: str, cwd: str) -> None:
    """Update the latest-handoff pointer for this project."""
    if not cwd:
        return
    sanitized = cwd.replace("/", "_")
    latest_subdir = latest_dir() / sanitized
    latest_subdir.mkdir(parents=True, exist_ok=True)
    meta = {
        "session_id": session_id,
        "generated_at": datetime.now().isoformat(),
        "cwd": cwd,
    }
    try:
        _atomic_write(
            latest_subdir / "latest.json",
            json.dumps(meta, ensure_ascii=False, indent=2),
        )
    except Exception as e:
        print(f"[session_end] WARNING: failed to update latest pointer: {e}", file=sys.stderr)


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


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    input_data = read_stdin()

    session_id = input_data.get("session_id", "unknown")
    reason = input_data.get("reason", "unknown")
    source = input_data.get("source", "")
    transcript_path = input_data.get("transcript_path", "")
    cwd = input_data.get("cwd", "")

    # Log the session end event
    log_event("session_end", {
        "session_id": session_id,
        "reason": reason,
        "source": source,
    })

    # ── /clear transition: capture context for next session ──────────────────
    if source == "clear" and transcript_path:
        context = extract_handoff_context(Path(transcript_path))
        has_content = bool(context["user_messages"] or context["files_touched"])
        if has_content:
            handoff_path = write_clear_handoff(
                context, session_id, cwd, transcript_path
            )
            if handoff_path:
                log_event("clear_handoff", {
                    "session_id": session_id,
                    "cwd": cwd,
                    "handoff": str(handoff_path),
                    "prompts": len(context["user_messages"]),
                    "files": len(context["files_touched"]),
                })
                print(
                    f"[session_end] /clear handoff saved: {len(context['user_messages'])} prompts, "
                    f"{len(context['files_touched'])} files",
                    file=sys.stderr,
                )

    # Sync: ensure events.jsonl is flushed by touching the session dir
    s_dir = session_dir(session_id)
    marker = s_dir / ".session_ended"
    try:
        _atomic_write(
            marker,
            f"ended={format_timestamp()}\nreason={reason}\n",
        )
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()

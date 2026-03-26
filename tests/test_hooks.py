#!/usr/bin/env python3
"""
ContextRecoveryHook — test suite (stdlib unittest, no external deps).

Run with: python3 -m unittest tests.test_hooks -v
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


HOOKS_DIR = Path(__file__).parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

# Import shared utilities and hook modules
import _safe_write
import session_end
import session_start
import pre_compact
import post_compact
import stop
import setup


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

def write_jsonl(path: Path, lines: list[dict]) -> None:
    """Write a JSONL transcript file (one JSON object per line)."""
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Atomic write
# ═══════════════════════════════════════════════════════════════════════════════

class TestAtomicWrite(unittest.TestCase):
    """Verify _atomic_write in each hook produces correct atomic output."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _s_dir(self, session_id="s"):
        d = self.tmp / ".claude" / "sessions" / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_atomic_write_basic(self):
        """Content written correctly and no temp files left behind."""
        target = self._s_dir() / "atomic.txt"
        _safe_write.safe_write(target, "hello 世界\n")
        self.assertEqual(target.read_text(encoding="utf-8"), "hello 世界\n")
        leftover = list(self._s_dir().glob(".tmp_*"))
        self.assertEqual(leftover, [], f"Temp files leaked: {leftover}")

    def test_atomic_write_overwrites(self):
        """Atomic write correctly replaces existing content."""
        target = self._s_dir() / "overwrite.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("old", encoding="utf-8")
        _safe_write.safe_write(target, "new")
        self.assertEqual(target.read_text(), "new")

    def test_atomic_write_creates_parents(self):
        """Atomic write creates parent directories if missing."""
        target = self._s_dir() / "a" / "b" / "c.txt"
        _safe_write.safe_write(target, "nested")
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(), "nested")

    def test_atomic_write_large_content(self):
        """Atomic write handles large content (1 MB+)."""
        target = self._s_dir() / "large.txt"
        _safe_write.safe_write(target, "x" * (1024 * 1024))
        self.assertEqual(len(target.read_text()), 1024 * 1024)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SessionStart extraction helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionStartHelpers(unittest.TestCase):

    def test_extract_user_notes_clean(self):
        from session_start import _extract_user_notes
        ctx = "\n\n## Recovery Notes\n\n_**Auto-filled.**\n\n**Files worked on:**\n\n---\n\n_Add your notes_\nUser wrote this.\n"
        self.assertEqual(_extract_user_notes(ctx), "User wrote this.")

    def test_extract_user_notes_no_previous_bleed(self):
        from session_start import _extract_user_notes
        ctx = "\n\n## Recovery Notes\n\n_**Auto-filled.**\n\n---\n\n_Add your notes_\nUser notes here.\n\n### 📝 Previous Recovery Notes\n_Preserved._\nOld notes.\n"
        result = _extract_user_notes(ctx)
        self.assertEqual(result, "User notes here.")
        self.assertNotIn("Old notes", result)

    def test_extract_files_touched_simple(self):
        from session_start import _extract_files_touched
        ctx = "\n\n### Files Recently Touched\n```\nsrc/a.py, src/b.py\n```\n"
        self.assertEqual(_extract_files_touched(ctx), "src/a.py, src/b.py")

    def test_extract_files_touched_stops_at_sibling_subsection(self):
        from session_start import _extract_files_touched
        ctx = "\n\n### Files Recently Touched\n```\nsrc/main.py\n```\n\n### Recent Assistant Responses\n1. Added X\n\n## Recovery Notes\n"
        result = _extract_files_touched(ctx)
        self.assertEqual(result, "src/main.py")
        self.assertNotIn("Recent Assistant", result)

    def test_extract_previous_recovery_notes(self):
        from session_start import _extract_previous_recovery_notes
        # Real context.md structure: ### inside ## Recovery Notes
        ctx = "\n\n## Recovery Notes\n\n### 📝 Previous Recovery Notes\n_Preserved from previous session — do not delete._\nPrevious cycle note.\n\n## Session Info\n"
        result = _extract_previous_recovery_notes(ctx)
        self.assertEqual(result, "Previous cycle note.")
        self.assertFalse(result.startswith("_Preserved"))

    def test_session_start_injects_correct_context(self):
        """SessionStart injects Git + Notes + Files + Guidance, nothing extra."""
        tmp = Path(tempfile.mkdtemp())
        try:
            ctx_md = tmp / ".claude" / "sessions" / "test-session" / "context.md"
            ctx_md.parent.mkdir(parents=True)
            ctx_md.write_text("""

## Recovery Notes

_**Auto-filled.**

---
_Add your notes_
User recovery note here.

### 📝 Previous Recovery Notes
_Preserved._
Previous note here.

## Recent Work

### Files Recently Touched
```
src/main.py
```

### Recent Assistant Responses
1. Added something.

""", encoding="utf-8")

            # Mark setup as done so Setup hook doesn't run (SessionStart runs after Setup)
            marker = tmp / ".claude" / ".context-recovery-setup"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("initialized\n", encoding="utf-8")

            # Set HOME so subprocess's Path.home() returns tmp
            env = {**os.environ, "HOME": str(tmp), "PYTHONPATH": str(HOOKS_DIR)}
            proc = subprocess.Popen(
                [sys.executable, str(HOOKS_DIR / "session_start.py")],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(HOOKS_DIR),
                env=env,
            )
            stdout, stderr = proc.communicate(
                input=json.dumps({
                    "session_id": "test-session",
                    "source": "compact",
                    "cwd": "/tmp",
                }).encode()
            )
            self.assertEqual(proc.returncode, 0, f"stderr={stderr}")
            data = json.loads(stdout)
            ctx = data["hookSpecificOutput"]["additionalContext"]

            self.assertIn("Git:", ctx)
            self.assertIn("User recovery note here", ctx)
            self.assertIn("Previous note here", ctx)
            self.assertIn("src/main.py", ctx)
            self.assertIn("Files Recently Touched", ctx)
            self.assertIn("Resume Guidance", ctx)
            self.assertNotIn("Recent Assistant Responses", ctx)
        finally:
            shutil.rmtree(tmp)

    def test_session_start_clear_restores_handoff(self):
        """SessionStart(source=clear) restores handoff from previous session."""
        tmp = Path(tempfile.mkdtemp())
        try:
            # Create handoff in old session
            old_s = tmp / ".claude" / "sessions" / "old-session"
            old_s.mkdir(parents=True)
            (old_s / "handoff.md").write_text(
                "## Handoff\n\nWork in progress on auth.\n", encoding="utf-8")

            # Create latest pointer (cwd /tmp → sanitized as _tmp)
            latest = tmp / ".claude" / "sessions" / "latest" / "_tmp"
            latest.mkdir(parents=True)
            (latest / "latest.json").write_text(
                json.dumps({"session_id": "old-session", "cwd": "/tmp"}),
                encoding="utf-8",
            )

            # Mark setup as done so Setup hook doesn't run
            marker = tmp / ".claude" / ".context-recovery-setup"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("initialized\n", encoding="utf-8")

            # Set HOME so subprocess's Path.home() returns tmp
            env = {**os.environ, "HOME": str(tmp), "PYTHONPATH": str(HOOKS_DIR)}
            proc = subprocess.Popen(
                [sys.executable, str(HOOKS_DIR / "session_start.py")],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(HOOKS_DIR),
                env=env,
            )
            stdout, stderr = proc.communicate(
                input=json.dumps({
                    "session_id": "new-session",
                    "source": "clear",
                    "cwd": "/tmp",
                }).encode()
            )
            self.assertEqual(proc.returncode, 0, f"stderr={stderr}")
            data = json.loads(stdout)
            ctx = data["hookSpecificOutput"]["additionalContext"]
            self.assertIn("/clear Transition", ctx)
            self.assertIn("Work in progress on auth", ctx)
        finally:
            shutil.rmtree(tmp)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. PreCompact
# ═══════════════════════════════════════════════════════════════════════════════

class TestPreCompact(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _s_dir(self, session_id="s"):
        d = self.tmp / ".claude" / "sessions" / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_precompact_backup_transcript(self):
        """PreCompact backs up transcript to transcript_backups/."""
        transcript = self.tmp / "transcript.jsonl"
        write_jsonl(transcript, [
            {"type": "user", "message": {"role": "user", "content": "Hello"}},
        ])

        import pre_compact
        with patch("pre_compact.Path.home", return_value=self.tmp):
            pre_compact.backup_transcript(transcript, "auto", self._s_dir())
            backups = self._s_dir() / "transcript_backups"
            self.assertTrue(backups.exists())
            self.assertGreater(len(list(backups.glob("*.jsonl"))), 0)

    def test_precompact_generates_context_md(self):
        """PreCompact generates context.md from transcript."""
        transcript = self.tmp / "transcript.jsonl"
        write_jsonl(transcript, [
            {"type": "user", "message": {"role": "user", "content": "Build auth system"}},
            {"type": "assistant", "message": {"role": "assistant", "content": "I'll build it."}},
        ])

        import pre_compact
        with patch("pre_compact.Path.home", return_value=self.tmp):
            data = pre_compact.extract_key_content(transcript)
            self.assertIn("Build auth system", data["prompts"])
            self.assertIn("I'll build it.", data["snippets"])

            s_dir = self._s_dir()
            summary = pre_compact.generate_context_summary(data, "s", s_dir)
            self.assertIn("Build auth system", summary)
            self.assertIn("Claude Code Session Context", summary)

            pre_compact.safe_write(s_dir / "context.md", summary)
            ctx = (s_dir / "context.md").read_text()
            self.assertIn("Build auth system", ctx)

    def test_precompact_extracts_assistant_snippets(self):
        """PreCompact extracts assistant snippets from transcript."""
        transcript = self.tmp / "transcript.jsonl"
        write_jsonl(transcript, [
            {"type": "assistant", "message": {
                "role": "assistant",
                "content": "Added structured logging.",
                "tool_use": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "npm test"}}
                ]
            }},
        ])

        import pre_compact
        with patch("pre_compact.Path.home", return_value=self.tmp):
            data = pre_compact.extract_key_content(transcript)
            self.assertIn("Added structured logging.", data["snippets"])

    def test_precompact_logs_event(self):
        """PreCompact appends event to events.jsonl."""
        transcript = self.tmp / "transcript.jsonl"
        write_jsonl(transcript, [
            {"type": "user", "message": {"role": "user", "content": "Hello"}},
        ])

        import pre_compact
        with patch("pre_compact.Path.home", return_value=self.tmp):
            pre_compact.backup_transcript(transcript, "auto", self._s_dir())
            pre_compact.log_event("pre_compact", {
                "session_id": "s",
                "trigger": "auto",
                "custom_instructions": "",
                "results": {},
                "transcript_found": True,
            })
            log = self._s_dir() / "events.jsonl"
            self.assertTrue(log.exists())
            entry = json.loads(log.read_text().strip().splitlines()[-1])
            self.assertEqual(entry["type"], "pre_compact")
            self.assertTrue(entry["transcript_found"])


# ═══════════════════════════════════════════════════════════════════════════════
# 4. SessionEnd /clear
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionEnd(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _s_dir(self, session_id="s"):
        d = self.tmp / ".claude" / "sessions" / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_clear_handoff_extracts_user_prompts(self):
        """SessionEnd extracts user prompts from transcript on /clear."""
        transcript = self.tmp / "transcript.jsonl"
        write_jsonl(transcript, [
            {"type": "user", "message": {"role": "user", "content": "Fix login bug"}},
            {"type": "assistant", "message": {"role": "assistant", "content": "Done."}},
        ])

        import session_end
        with patch("session_end.Path.home", return_value=self.tmp):
            ctx = session_end.extract_handoff_context(transcript)
            self.assertIn("Fix login bug", ctx["user_messages"])
            session_end.write_clear_handoff(ctx, "old-session", "/tmp", str(transcript))

            handoff = self._s_dir("old-session") / "handoff.md"
            self.assertTrue(handoff.exists(), "handoff.md not created")
            self.assertIn("Fix login bug", handoff.read_text())

    def test_clear_creates_latest_pointer(self):
        """SessionEnd updates sessions/latest/{project}/latest.json on /clear."""
        transcript = self.tmp / "transcript.jsonl"
        write_jsonl(transcript, [
            {"type": "user", "message": {"role": "user", "content": "Test"}},
        ])

        import session_end
        with patch("session_end.Path.home", return_value=self.tmp):
            session_end.write_clear_handoff(
                {"user_messages": ["Test"], "assistant_snippets": [], "files_touched": []},
                "old-session", "/tmp", str(transcript),
            )
            session_end._update_latest_pointer("old-session", "/tmp")

            latest = self.tmp / ".claude" / "sessions" / "latest" / "_tmp" / "latest.json"
            self.assertTrue(latest.exists(), f"latest.json not at {latest}")
            data = json.loads(latest.read_text())
            self.assertEqual(data["session_id"], "old-session")

    def test_session_end_logs_event(self):
        """SessionEnd logs event for all terminations."""
        import session_end
        with patch("session_end.Path.home", return_value=self.tmp):
            session_end.log_event("session_end", {
                "session_id": "test-s",
                "reason": "logout",
                "source": "",
            })
            log = self._s_dir("test-s") / "events.jsonl"
            self.assertTrue(log.exists())
            entry = json.loads(log.read_text().strip().splitlines()[-1])
            self.assertEqual(entry["type"], "session_end")
            self.assertEqual(entry["reason"], "logout")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. PostCompact
# ═══════════════════════════════════════════════════════════════════════════════

class TestPostCompact(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _s_dir(self, session_id="s"):
        d = self.tmp / ".claude" / "sessions" / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_postcompact_appends_summary(self):
        """PostCompact appends compact summary to context.md."""
        ctx_md = self._s_dir() / "context.md"
        ctx_md.write_text(
            "# Claude Code Session Context\n\n## Recovery Notes\n\n_No notes yet._\n",
            encoding="utf-8",
        )

        import post_compact
        with patch("post_compact.Path.home", return_value=self.tmp):
            post_compact.append_to_context(
                "Compressed 5000 tokens to 500.",
                self._s_dir(),
            )
            content = ctx_md.read_text()
            self.assertIn("Compaction Summary", content)
            self.assertIn("Compressed 5000 tokens", content)

    def test_postcompact_removes_old_summary(self):
        """PostCompact replaces old Compaction Summary, doesn't accumulate."""
        ctx_md = self._s_dir() / "context.md"
        ctx_md.write_text(
            "# Claude Code Session Context\n\n"
            "## Compaction Summary\n\n> Old summary.\n",
            encoding="utf-8",
        )

        import post_compact
        with patch("post_compact.Path.home", return_value=self.tmp):
            post_compact.append_to_context("New summary.", self._s_dir())
            content = ctx_md.read_text()
            self.assertEqual(content.count("Compaction Summary"), 1)
            self.assertIn("New summary", content)
            self.assertNotIn("Old summary", content)

    def test_postcompact_saves_cycle_history(self):
        """PostCompact appends to cycle_history.jsonl for auto-fill."""
        ctx_md = self._s_dir() / "context.md"
        ctx_md.write_text("# Claude Code Session Context\n", encoding="utf-8")

        import post_compact
        with patch("post_compact.Path.home", return_value=self.tmp):
            post_compact.save_cycle_summary(
                "Summary for cycle.", "test-s", "auto", self._s_dir(),
            )
            history = self._s_dir() / "cycle_history.jsonl"
            self.assertTrue(history.exists())
            entry = json.loads(history.read_text().strip().splitlines()[-1])
            self.assertEqual(entry["trigger"], "auto")
            self.assertIn("Summary for cycle", entry["summary"])


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Stop hook
# ═══════════════════════════════════════════════════════════════════════════════

class TestStop(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _s_dir(self, session_id="s"):
        d = self.tmp / ".claude" / "sessions" / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_stop_handles_empty_stdin(self):
        """Stop hook exits 0 with empty stdin (no crash)."""
        import stop
        with patch("stop.Path.home", return_value=self.tmp):
            orig = sys.stdin
            sys.stdin = io.StringIO("")
            try:
                try:
                    stop.main()
                except SystemExit:
                    pass  # hook calls sys.exit(0)
            finally:
                sys.stdin = orig

    def test_stop_extracts_recent_work(self):
        """Stop extracts recent user requests from transcript."""
        transcript = self.tmp / "transcript.jsonl"
        write_jsonl(transcript, [
            {"type": "user", "message": {"role": "user", "content": "Add authentication system"}},
        ])

        import stop
        with patch("stop.Path.home", return_value=self.tmp):
            prompts, files = stop.extract_recent_work(str(transcript), "s")
            self.assertIn("Add authentication system", prompts)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Stdin parsing edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestStdinParsing(unittest.TestCase):

    def test_empty_stdin_returns_empty_dict(self):
        """Each hook's read_stdin returns {} for empty input."""
        for module in ["session_start", "session_end", "pre_compact", "post_compact", "stop"]:
            with self.subTest(module=module):
                mod = __import__(module)
                orig = sys.stdin
                sys.stdin = io.StringIO("")
                try:
                    result = mod.read_stdin()
                    self.assertEqual(result, {}, f"{module}.read_stdin failed on empty stdin")
                finally:
                    sys.stdin = orig

    def test_invalid_json_returns_empty_dict(self):
        """Each hook's read_stdin returns {} for invalid JSON."""
        for module in ["session_start", "session_end", "pre_compact", "post_compact", "stop"]:
            with self.subTest(module=module):
                mod = __import__(module)
                orig = sys.stdin
                sys.stdin = io.StringIO("not json at all")
                try:
                    result = mod.read_stdin()
                    self.assertEqual(result, {}, f"{module}.read_stdin failed on invalid JSON")
                finally:
                    sys.stdin = orig


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Setup idempotency
# ═══════════════════════════════════════════════════════════════════════════════

class TestSetup(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_setup_idempotent(self):
        """Setup can be run multiple times without error."""
        import setup
        with patch("setup.Path.home", return_value=self.tmp):
            try:
                setup.setup_init()
            except SystemExit:
                pass
            files_first = set(self.tmp.glob("**/*.md"))
            try:
                setup.setup_init()  # second run
            except SystemExit:
                pass
            files_second = set(self.tmp.glob("**/*.md"))
            self.assertEqual(files_first, files_second, "Setup created different files on second run")


if __name__ == "__main__":
    unittest.main(verbosity=2)

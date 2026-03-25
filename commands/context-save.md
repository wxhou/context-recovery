# /context-save

Manually save current work context before a long operation or before ending a session.

## Usage
```
/context-save
```

## What it does

1. Reads current transcript and generates a structured `.claude/CONTEXT.md`
2. Updates `.claude/TODO.md` with current timestamp
3. Backs up transcript to `.claude/logs/transcript_backups/`
4. Logs the save event

## When to use

- Before a long-running task
- Before ending your work session
- Before triggering `/compact` manually
- When switching to a different project

## Notes

This command invokes the PreCompact handler with `--backup --generate-context`.
The saved context will be automatically injected by `/context-restore` or on session resume.

## Related

- `/context-restore` — View and restore saved context
- `/compact` — Trigger context compaction (PreCompact hook fires automatically on auto-compact)

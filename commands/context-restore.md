# /context-restore

View and restore saved context from previous sessions.

## Usage
```
/context-restore
/context-restore --latest
/context-restore --show-backups
/context-restore --clear
```

## Options

- `--latest` — Show the most recent backup
- `--show-backups` — List all available transcript backups
- `--clear` — Clear the current CONTEXT.md

## What it does

1. Reads `.claude/CONTEXT.md` and displays the saved context
2. Lists recent transcript backups from `.claude/logs/transcript_backups/`
3. Shows the last TODO items
4. Optionally clears the context

## Related

- `/context-save` — Save current work context

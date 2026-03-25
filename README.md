# ContextRecoveryHook

> **PreCompact backup + SessionStart recovery — the lightest Claude Code context preservation plugin.**

ContextRecoveryHook prevents **context amnesia** during Claude Code's auto-compaction. Two hooks, zero dependencies (besides Python 3.11), pure `/plugin install`.

---

## Installation

```bash
/plugin install wxhou/context-recovery
```

Then restart Claude Code.

---

## How It Works

```
自动压缩触发 (70% 上下文满)
    ↓ PreCompact (matcher: "auto")
    ├─ 备份 transcript → ~/.claude/logs/transcript_backups/
    ├─ 生成 CONTEXT.md (最近 prompts + 文件)
    ├─ 更新时间戳 TODO.md
    └─ 记录事件 → logs/events.json

下次会话启动
    ↓ SessionStart
    ├─ 读取 CONTEXT.md
    ├─ 读取 TODO.md
    ├─ 加载最近 backup snippet
    └─ 注入 additionalContext → Claude 自动获得上下文
```

---

## Features

| Feature | Description |
|---------|-------------|
| **Auto-backup** | Transcript backed up to `~/.claude/logs/transcript_backups/` before every auto-compaction |
| **Context generation** | Extracts recent prompts and file paths into `~/.claude/CONTEXT.md` |
| **Session recovery** | SessionStart hook injects saved context as `additionalContext` on resume |
| **TODO tracking** | Timestamps `~/.claude/TODO.md` for work continuity |
| **Event logging** | All events logged to `~/.claude/logs/events.json` |
| **Slash commands** | `/context-save`, `/context-restore` |

---

## Requirements

- Python 3.11+
- [uv](https://astral.sh/uv) — auto-installed if not present

---

## File Structure (installed)

```
~/.claude/
├── hooks/
│   ├── pre_compact.py     # PreCompact hook handler
│   └── session_start.py   # SessionStart hook handler
├── CONTEXT.md             # Auto-generated context summary
├── TODO.md                # Manual TODO items
└── logs/
    ├── events.json        # All hook events
    └── transcript_backups/  # Transcript backups
```

---

## Context Files

### CONTEXT.md

Auto-generated before each compaction. **Do not edit** — it's overwritten.

### TODO.md

**Manually maintained.** Add your active work items:

```markdown
- [ ] Fix authentication flow
- [ ] Write tests for user model
- [x] Set up database schema
```

---

## Comparison

| | ContextRecoveryHook | mono |
|-|--------------------|------|
| Scope | 2 hooks | 23 commands |
| Learning curve | Low | High |
| Dependencies | Python 3.11 + uv | Python + uv |
| Context files | CONTEXT.md + TODO.md | memory/*.md |
| Weight | **~515 lines** | ~2000+ lines |

---

## Uninstall

```bash
# Remove hooks from settings.local.json, then:
rm ~/.claude/hooks/pre_compact.py
rm ~/.claude/hooks/session_start.py
# Restart Claude Code
```

---

## License

MIT

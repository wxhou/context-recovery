# ContextRecoveryHook

> **PreCompact backup + SessionStart recovery — the lightest Claude Code context preservation plugin.**

ContextRecoveryHook prevents **context amnesia** during Claude Code's auto-compaction. Three hooks, pure stdlib Python, zero external dependencies, pure `/plugin install`.

---

## Installation

```bash
/plugin install wxhou/context-recovery
```

Then restart Claude Code.

---

## How It Works

```
首次运行
    ↓ Setup
    ├─ 创建 ~/.claude/logs/
    ├─ 创建 ~/.claude/logs/transcript_backups/
    ├─ 生成模板 CONTEXT.md
    ├─ 生成模板 TODO.md
    └─ 记录事件 → logs/events.json

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

- Python 3 (stdlib only — no external dependencies)

---

## File Structure (installed)

```
~/.claude/
├── hooks/
│   ├── setup.py          # Setup hook — first-run initialization
│   ├── pre_compact.py    # PreCompact hook handler
│   └── session_start.py  # SessionStart hook handler
├── CONTEXT.md            # Auto-generated context summary
├── TODO.md               # Manual TODO items
└── logs/
    ├── events.json       # All hook events
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
| Scope | 3 hooks | 23 commands |
| Learning curve | Low | High |
| Dependencies | Python stdlib only | Python + uv |
| Context files | CONTEXT.md + TODO.md | memory/*.md |
| Weight | **~480 lines** | ~2000+ lines |

---

## Uninstall

```bash
/plugin uninstall context-recovery
# Or manually: remove hook entries from settings.local.json, then:
rm ~/.claude/hooks/setup.py
rm ~/.claude/hooks/pre_compact.py
rm ~/.claude/hooks/session_start.py
# Restart Claude Code
```

---

## License

MIT

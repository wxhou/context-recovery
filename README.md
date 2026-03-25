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

压缩触发 (自动 /manual 或 auto)
    ↓ PreCompact (matcher: "auto|manual")
    ├─ 备份 transcript → ~/.claude/logs/transcript_backups/
    ├─ 清理旧备份 (保留最新 10 个 + 最近 7 天)
    ├─ 生成 CONTEXT.md (最近 prompts + 文件)
    ├─ 更新时间戳 TODO.md
    └─ 记录事件 → logs/events.json

压缩完成
    ↓ PostCompact
    ├─ 保存压缩摘要 compact_summary 到 CONTEXT.md
    └─ 记录事件 → logs/events.json

会话恢复
    ↓ SessionStart (source: "compact")
    ├─ 读取 CONTEXT.md
    ├─ 读取 TODO.md
    ├─ 加载最近 backup snippet
    └─ 注入 additionalContext → Claude 自动获得上下文

会话结束
    ↓ Stop
    ├─ 从 transcript 提取结构化 session summary
    └─ 追加到 CONTEXT.md
```

---

## Features

| Feature | Description |
|---------|-------------|
| **Auto-backup** | Transcript backed up to `~/.claude/logs/transcript_backups/` before every compaction (auto + manual) |
| **Context generation** | Extracts recent prompts and file paths into `~/.claude/CONTEXT.md` |
| **Compact summary** | Captures `compact_summary` after compaction and appends to CONTEXT.md |
| **Session recovery** | SessionStart injects saved context on resume/compact/startup |
| **TODO tracking** | Timestamps `~/.claude/TODO.md` for work continuity |
| **Backup rotation** | Auto-cleanup: keep newest 10 + last 7 days |
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
│   ├── post_compact.py   # PostCompact hook handler
│   ├── session_start.py  # SessionStart hook handler
│   └── stop.py           # Stop hook handler
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
| Scope | 5 hooks | 23 commands |
| Learning curve | Low | High |
| Dependencies | Python stdlib only | Python + uv |
| Context files | CONTEXT.md + TODO.md | memory/*.md |
| Weight | **~650 lines** | ~2000+ lines |

---

## Uninstall

```bash
/plugin uninstall context-recovery
# Or manually: remove hook entries from settings.local.json, then:
rm ~/.claude/hooks/setup.py
rm ~/.claude/hooks/pre_compact.py
rm ~/.claude/hooks/post_compact.py
rm ~/.claude/hooks/session_start.py
rm ~/.claude/hooks/stop.py
# Restart Claude Code
```

---

## License

MIT

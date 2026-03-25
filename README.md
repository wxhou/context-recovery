# ContextRecoveryHook

> **PreCompact backup + SessionStart recovery — the lightest Claude Code context preservation plugin.**

ContextRecoveryHook prevents **context amnesia** during Claude Code's auto-compaction. Five hooks, pure stdlib Python, zero external dependencies, multi-window safe with per-session isolation.

---

## Installation

**Two steps: add the marketplace, then install the plugin.**

```bash
# Step 1: Add the marketplace (registers this GitHub repo as a plugin source)
/plugin marketplace add wxhou/context-recovery

# Step 2: Install the plugin
/plugin install context-recovery@wxhou-context-recovery
```

Then restart Claude Code.

---

## How It Works

```
首次运行
    ↓ Setup
    ├─ 创建 ~/.claude/logs/
    ├─ 创建 ~/.claude/sessions/
    └─ 生成模板 ~/.claude/TODO.md

压缩触发 (自动 /manual 或 auto)
    ↓ PreCompact (matcher: "auto|manual")
    ├─ 备份 transcript → ~/.claude/sessions/<session_id>/transcript_backups/
    ├─ 清理旧备份 (保留最新 10 个 + 最近 7 天)
    ├─ 生成 session-specific context.md
    ├─ 更新时间戳 TODO.md (全局)
    └─ 记录事件 → sessions/<session_id>/events.jsonl

压缩完成
    ↓ PostCompact
    ├─ 保存压缩摘要 compact_summary 到 context.md
    └─ 记录事件 → events.jsonl

会话恢复
    ↓ SessionStart (source: "compact")
    ├─ 读取 session-specific context.md
    ├─ 读取全局 TODO.md
    ├─ 加载最近 backup snippet
    └─ 注入 additionalContext → Claude 自动获得上下文

会话结束
    ↓ Stop
    ├─ 从 transcript 提取结构化 session summary
    └─ 追加到 context.md
```

---

## Features

| Feature | Description |
|---------|-------------|
| **Auto-backup** | Transcript backed up per session before every compaction (auto + manual) |
| **Context generation** | Extracts recent prompts and file paths into session-specific `context.md` |
| **Compact summary** | Captures `compact_summary` after compaction and appends to context.md |
| **Session recovery** | SessionStart injects saved context on resume/compact/startup |
| **TODO tracking** | Timestamps `~/.claude/TODO.md` for work continuity (global) |
| **Backup rotation** | Auto-cleanup: keep newest 10 + last 7 days |
| **Multi-window safe** | Per-session isolation via `session_id` — no cross-project pollution |
| **Append-only logging** | `events.jsonl` prevents concurrent write races |

---

## Requirements

- Python 3 (stdlib only — no external dependencies)

---

## File Structure (installed)

```
~/.claude/
├── TODO.md                    # Manual TODO items (global, shared)
├── logs/
│   └── events.jsonl           # Setup/global events (JSONL)
└── sessions/
    └── {session_id}/          # Per-window isolation
        ├── context.md          # Auto-generated context summary
        ├── events.jsonl        # Per-window hook events (JSONL)
        └── transcript_backups/ # Transcript backups
```

---

## Context Files

### `sessions/{session_id}/context.md`

Auto-generated before each compaction per session window. **Do not edit** — it's overwritten on each PreCompact.

### `~/.claude/TODO.md`

**Globally shared, manually maintained.** Add your active work items:

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
| Context files | sessions/{id}/context.md + TODO.md | memory/*.md |
| Multi-window | Yes (session_id isolation) | Unknown |
| Weight | **~750 lines** | ~2000+ lines |

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

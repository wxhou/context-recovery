#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# ContextRecoveryHook — Install Script
# ──────────────────────────────────────────────────────────────────────────────
# This script installs the ContextRecoveryHook plugin into the current
# Claude Code project (or home directory for global use).
#
# Usage:
#   bash install.sh [project-dir]
#
#   project-dir  — optional, defaults to current directory
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${1:-$PWD}/.claude"
HOOKS_DIR="$CLAUDE_DIR/hooks"
PLUGIN_ROOT="$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

echo ""
echo "ContextRecoveryHook Installer"
echo "=============================="

# Detect uv
if ! command -v uv &>/dev/null; then
    warn "uv not found. Installing via official script..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if ! command -v uv &>/dev/null; then
        fail "uv installation failed. Install manually: https://astral.sh/uv/install.sh"
    fi
fi
info "uv found: $(uv --version)"

# Check Python 3.11+
PYVER=$(python3 --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' || echo "0.0")
MAJOR=$(echo "$PYVER" | cut -d. -f1)
MINOR=$(echo "$PYVER" | cut -d. -f2)
if [ "$MAJOR" -lt 3 ] || ([ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 11 ]); then
    fail "Python 3.11+ required, found: $PYVER"
fi
info "Python version OK: $PYVER"

# Create .claude directory
info "Setting up in: $CLAUDE_DIR"
mkdir -p "$CLAUDE_DIR"

# Copy hooks handlers
info "Installing hook handlers..."
mkdir -p "$CLAUDE_DIR/hooks-handlers"
cp "$SCRIPT_DIR/hooks-handlers/pre_compact.py" "$CLAUDE_DIR/hooks-handlers/"
cp "$SCRIPT_DIR/hooks-handlers/session_start.py" "$CLAUDE_DIR/hooks-handlers/"
chmod +x "$CLAUDE_DIR/hooks-handlers/"*.py
info "Hook handlers installed."

# Create logs directory
mkdir -p "$CLAUDE_DIR/logs/transcript_backups"

# Copy example context files
if [ ! -f "$CLAUDE_DIR/CONTEXT.md" ]; then
    info "Creating example CONTEXT.md..."
    cat > "$CLAUDE_DIR/CONTEXT.md" << 'EOF'
# Claude Code Session Context
> Edit this file to store session context. The PreCompact hook updates it automatically.

## Current Work
<!-- Add your current work summary here -->

## Active Files
<!-- List files you're currently working on -->

## Recent Decisions
<!-- Note important decisions made in this session -->

## Recovery Notes
<!-- What needs to be done in the next session -->
EOF
    info "Created .claude/CONTEXT.md"
fi

if [ ! -f "$CLAUDE_DIR/TODO.md" ]; then
    info "Creating example TODO.md..."
    cat > "$CLAUDE_DIR/TODO.md" << 'EOF'
# TODO — Work Items

- [ ] Task 1
- [ ] Task 2
- [x] Completed task

<!-- last-updated: -->
EOF
    info "Created .claude/TODO.md"
fi

# Create logs dir
mkdir -p "$CLAUDE_DIR/logs/transcript_backups"

# Create settings.local.json with hooks
SETTINGS_FILE="$CLAUDE_DIR/settings.local.json"
if [ -f "$SETTINGS_FILE" ]; then
    warn "settings.local.json already exists — merging hooks..."

    # Use python to merge JSON (jq might not be available)
    python3 - "$SETTINGS_FILE" "$SCRIPT_DIR/hooks/hooks.json" << 'PYEOF'
import sys, json, copy

existing_path = sys.argv[1]
plugin_path = sys.argv[2]

with open(existing_path) as f:
    existing = json.load(f)
with open(plugin_path) as f:
    plugin = json.load(f)

# Merge hooks
if "hooks" not in existing:
    existing["hooks"] = {}

for event, hooks in plugin.get("hooks", {}).items():
    if event not in existing["hooks"]:
        existing["hooks"][event] = hooks
    else:
        # Merge hook entries, avoid duplicates by command string
        existing_cmds = {h.get("command","") for h in existing["hooks"][event]}
        for hook_list in hooks:
            for h in hook_list.get("hooks", []):
                if h.get("command", "") not in existing_cmds:
                    existing["hooks"][event].append(h)

with open(existing_path, "w") as f:
    json.dump(existing, f, indent=2)
print("Merged successfully")
PYEOF
else
    cp "$SCRIPT_DIR/hooks/hooks.json" "$SETTINGS_FILE"
    # Fix command paths to point to installed location
    python3 - "$SETTINGS_FILE" "$CLAUDE_DIR" << 'PYEOF'
import sys, json

path = sys.argv[1]
root = sys.argv[2]

with open(path) as f:
    data = json.load(f)

# Replace ${CLAUDE_PLUGIN_ROOT} with actual path
import json as j
def fix_cmd(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "command" and isinstance(v, str):
                obj[k] = v.replace("${CLAUDE_PLUGIN_ROOT}", root + "/hooks-handlers")
            else:
                fix_cmd(v)
    elif isinstance(obj, list):
        for item in obj:
            fix_cmd(item)

fix_cmd(data)

with open(path, "w") as f:
    json.dump(data, f, indent=2)
print("Paths fixed")
PYEOF
fi
info "Hooks configured in settings.local.json"

# Copy slash commands
if [ ! -d "$CLAUDE_DIR/commands" ]; then
    mkdir -p "$CLAUDE_DIR/commands"
fi
cp "$SCRIPT_DIR/commands/"*.md "$CLAUDE_DIR/commands/" 2>/dev/null || true

# Verify installation
echo ""
echo "=============================="
echo "  Installation Complete!"
echo "=============================="
echo ""
echo "  Context files:"
echo "    $CLAUDE_DIR/CONTEXT.md"
echo "    $CLAUDE_DIR/TODO.md"
echo ""
echo "  Logs:"
echo "    $CLAUDE_DIR/logs/"
echo "    $CLAUDE_DIR/logs/transcript_backups/"
echo "    $CLAUDE_DIR/logs/events.json"
echo ""
echo "  Commands:"
echo "    /context-save   — Manual context backup"
echo "    /context-restore — View restored context"
echo ""
echo "  How it works:"
echo "    1. Auto-compact triggers → PreCompact backs up transcript + CONTEXT.md"
echo "    2. Next session start    → SessionStart injects context as additionalContext"
echo ""
echo "  Restart Claude Code to activate hooks."

#!/usr/bin/env bash
# MCP server launcher for claude-session-replay
# Used by systemd unit deploy/claude-session-replay-mcp.service

set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PORT="${PORT:-9241}"

exec python3 "${SCRIPT_DIR}/mcp_server.py"

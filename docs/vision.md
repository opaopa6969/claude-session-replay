[日本語版](ja/vision.md)

# Vision

## Why this tool should exist

The motivating pain is not merely "reading session logs."
The deeper problem is that AI coding sessions produce valuable work records — design decisions, debugging strategies, tool orchestration patterns — but those records are trapped in opaque, agent-specific log formats that no human can review or share.

Today, terminal-native coding agents (Claude Code, Codex CLI, Gemini CLI) produce detailed session logs, but the operating model around those logs is broken:

- session transcripts are locked in vendor-specific JSONL/JSON formats
- reviewing what happened in a session requires re-reading raw JSON
- sharing a session with a colleague means sending a multi-megabyte log file
- there is no way to "replay" a coding session like you would replay a chess game
- time-based analysis (when did the agent work? how long did each step take?) is impossible without custom parsing
- cross-agent comparison is not feasible because each agent uses a different schema

This tool exists to solve that visibility gap.

## Core motivation

The system should let an operator move from this mode:

> "I have a folder of JSONL files I cannot read. I vaguely remember what happened in yesterday's session. I cannot show my work to anyone."

to this mode:

> "I select a session, hit play, and watch the full coding conversation unfold — with tool invocations, thinking blocks, and timestamps — in a format I can share, archive, or record to video."

That is the constitutional purpose of this tool.

## Problem

A developer using AI coding agents wants to:
- review past sessions to understand what decisions were made and why
- share sessions with teammates for knowledge transfer and code review
- verify that agent work actually happened at claimed times (alibi verification)
- compare sessions across different agents (Claude, Codex, Gemini)
- produce video recordings of sessions for documentation or demonstration

The raw log formats do not support any of these use cases directly.

## Product vision

Build a tool that acts as a universal session log player:
- **Agent Adapters**: convert vendor-specific logs into a common model
- **Common Model**: a single, agent-agnostic JSON schema for session data
- **Renderers**: transform the common model into multiple output formats
- **Interactive Players**: replay sessions with full fidelity — messages, tools, thinking, timestamps
- **Video Export**: record interactive replays to MP4 for sharing

## Outcome

The user can:
- select any session from any supported agent via CLI or Web UI
- convert it to a common model that preserves all meaningful content
- render it as Markdown, HTML, interactive player, or terminal-style player
- replay sessions with speed control, seeking, and keyboard navigation
- visualize timestamps with analog clocks and multiple playback modes (Alibai Mode)
- export replays to MP4 video
- do all of this without vendor lock-in, because the common model is the single source of truth

## Design principles

1. The common model is the single source of truth — renderers never read raw logs directly.
2. Agent adapters are isolated — adding a new agent does not touch the renderer.
3. Output formats are independent — each renderer reads the same common model.
4. No external dependencies for basic functionality — standard library Python only for CLI.
5. Interactive features degrade gracefully — static formats (md, html) work without JavaScript.
6. Timestamps are first-class data, not an afterthought.
7. The tool is a pipeline, not a monolith — each stage can be used independently.

## Success criteria

The tool is succeeding when:
- a developer can replay any session from any supported agent within 30 seconds
- the output faithfully represents the original session (no lost messages, tools, or thinking)
- a colleague who was not present can understand the full session from the replay
- Alibai Mode timestamps can verify when work actually occurred
- adding support for a new agent requires only writing a new adapter, not modifying existing code

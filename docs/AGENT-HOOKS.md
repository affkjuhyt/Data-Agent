# Agent hooks

The project keeps one dependency-free hook implementation in
`.agent-hooks/agent_hook.py` and thin adapters for Claude and Codex-compatible
hook runners. The hooks are deliberately fast: they do not build Docker images,
install packages, run the full test suite, or read secrets.

## What runs

- Session start injects the repository working rules and the current change scope.
- After an edit, `git diff --check` catches whitespace errors and records the changed files.
- At stop, code changes receive a reminder to run `make test-changed`.

The checks fail open. A hook process error must never prevent the agent from working.
Run `make test-changed` explicitly before handing off a code change; hooks are a fast
feedback aid, not a replacement for the repository gates in `AGENTS.md`.

## Adapters

- Claude Code: `.claude/settings.json`
- Codex/Cursor-compatible hook runtime: `.cursor/hooks.json`

The existing `.codex/config.toml` is MCP configuration and is intentionally left
unchanged. If the installed Codex client exposes a different project-hook location,
keep the shared script and copy only the adapter format to that client’s settings.

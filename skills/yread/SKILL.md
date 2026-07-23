---
name: yread
description: Use when a user wants to generate an architecture-first Markdown wiki from a local source repository with yread, export that wiki to a notes directory such as Obsidian, resume or regenerate yread output, configure yread, or browse an existing yread wiki.
---

# yread

Use this skill when the user wants to turn a local source repository into an architecture-first Markdown wiki, export that wiki to a notes directory such as Obsidian, resume a previous yread run, or browse an existing yread wiki.

## What yread Does

yread analyzes a local repository with a lightweight project profile, an LLM-driven catalog phase, and per-page writing phase. It writes a v2 Markdown wiki with `wiki.json`, `manifest.json`, and `SUMMARY.md` in the output root, plus one Markdown file per architecture-first topic under `wiki/`.

Default output is:

```bash
<repo>/.yread
```

Persistent user config is:

```bash
~/.yread/config.env
```

## Basic Commands

Check the CLI:

```bash
yread --help
yread generate --help
```

Generate a wiki:

```bash
yread generate /path/to/repo
```

Generate to a specific export directory (set in config):

```bash
yread config set OUTPUT_DIR "/path/to/Obsidian Vault/Code Wikis/project"
yread generate /path/to/repo
```

Resume the current wiki output:

```bash
yread generate /path/to/repo --resume
```

Regenerate one page:

```bash
yread generate /path/to/repo --page <slug-or-title>
```

Browse a wiki (source repo auto-resolved from wiki.json; `--repo` overrides):

```bash
yread browse /path/to/wiki
```

## Configuration

Set up interactively, or show/edit individual keys:

```bash
yread config init
yread config path
yread config show
yread config set DOC_LANG zh
yread config set OUTPUT_DIR "/path/to/Obsidian Vault/Code Wikis"
```

Useful keys (bare in the config file / `--env-file`; as a shell environment
variable prefix with `YREAD_`, e.g. `YREAD_MODE=ml`):

```text
PROVIDER=minimax-cn | deepseek | openai-compatible
BASE_URL=https://api.example.com/v1
API_KEY=...
MODEL=...
DOC_LANG=zh | en
DEPTH=brief | standard | deep
MODE=software | ml
OUTPUT_DIR=/path/to/export/wiki
MAX_STEPS=24
MAX_TOPICS=30
CONCURRENCY=1
ENABLE_SHELL=1
```

Precedence is: process environment, `--env-file`, `~/.yread/config.env`, defaults.

## Agent Workflow

1. Confirm the target repository path exists.
2. Check `yread config show` when the user expects saved provider, language, or export settings.
3. For Obsidian export, set `yread config set OUTPUT_DIR ...` (config-driven, no per-run flag).
4. Use `--resume` when an existing v2 output exists and the user wants incremental completion.
5. Use `yread browse` only when the user wants browser inspection; generation itself writes Markdown files directly.

## Privacy Note

yread runs locally, but source snippets read by its tools are sent to the configured LLM provider. Do not run it on private or sensitive repositories unless the selected provider is acceptable for that code.

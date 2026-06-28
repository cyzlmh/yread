---
name: yread
description: Use when a user wants to generate a Markdown wiki from a local source repository with yread, export that wiki to a notes directory such as Obsidian, resume or regenerate yread output, configure yread, or browse an existing yread wiki.
---

# yread

Use this skill when the user wants to turn a local source repository into a readable Markdown wiki, export that wiki to a notes directory such as Obsidian, resume a previous yread run, or browse an existing yread wiki.

## What yread Does

yread analyzes a local repository with an LLM-driven catalog phase and per-page writing phase. It writes a versioned Markdown wiki with `wiki.json`, `manifest.json`, `SUMMARY.md`, and one Markdown file per topic.

Default output is:

```bash
<repo>/.yread/wiki
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

Generate to a specific export directory:

```bash
yread generate /path/to/repo --output-dir "/path/to/Obsidian Vault/Code Wikis/project"
```

Resume the current wiki version:

```bash
yread generate /path/to/repo --resume
```

Regenerate one page:

```bash
yread generate /path/to/repo --page <slug-or-title>
```

Browse a wiki:

```bash
yread view /path/to/wiki --repo /path/to/repo
```

## Configuration

Show or edit config:

```bash
yread config path
yread config show
yread config set DOC_LANG Chinese
yread config set OUTPUT_DIR "/path/to/Obsidian Vault/Code Wikis"
```

Useful keys:

```text
PROVIDER=minimax-cn | deepseek | openai-compatible
BASE_URL=https://api.example.com/v1
API_KEY=...
MODEL=...
DOC_LANG=Chinese | English
OUTPUT_DIR=/path/to/export/wiki
MAX_STEPS=24
MAX_TOPICS=30
CONCURRENCY=1
ENABLE_SHELL=1
```

Precedence is: CLI flags, process environment, `--env-file`, `~/.yread/config.env`, defaults.

## Agent Workflow

1. Confirm the target repository path exists.
2. Check `yread config show` when the user expects saved provider, language, or export settings.
3. For Obsidian export, prefer `--output-dir` for one-off runs and `yread config set OUTPUT_DIR ...` for persistent behavior.
4. Use `--resume` when a previous version exists and the user wants incremental completion.
5. Use `yread view` only when the user wants browser inspection; generation itself writes Markdown files directly.

## Privacy Note

yread runs locally, but source snippets read by its tools are sent to the configured LLM provider. Do not run it on private or sensitive repositories unless the selected provider is acceptable for that code.

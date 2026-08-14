---
name: yread
description: Use when a user wants to generate an architecture-first Markdown wiki from a local source repository with yread, build, preview, or publish it as static HTML, export that wiki to a notes directory such as Obsidian, regenerate yread output, or configure yread.
---

# yread

Use this skill when the user wants to turn a local source repository into an architecture-first Markdown wiki, build or publish its static HTML, export that wiki to a notes directory such as Obsidian, or regenerate the output.

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

Running the command again rebuilds the catalog and all pages, overwriting the
previous generated output.

Build a completed wiki as flat, self-contained HTML files:

```bash
yread build /path/to/repo/.yread
```

The default output is `.yread-dist` beside the input. Build only renders local
HTML; it does not generate search indexes or publish the site.

Publish the built site to a preconfigured static Hub:

```bash
yread config set HUB_TARGET deploy@docs:/var/www/yread-hub
yread publish
```

With no argument, publish uses `.yread-dist` when present, otherwise builds from
`.yread`, and runs generate first only when neither artifact exists. It checks
existence only and does not detect source repository changes. Passing an
explicit built directory publishes it directly without preparing prerequisites.

Preview a built wiki directly:

```bash
yread build /path/to/repo/.yread
open /path/to/repo/.yread-dist/index.html
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
HUB_TARGET=deploy@docs:/var/www/yread-hub
```

Precedence is: process environment, `--env-file`, `~/.yread/config.env`, defaults.

## Agent Workflow

1. Confirm the target repository path exists.
2. Check `yread config show` when the user expects saved provider, language, or export settings.
3. For Obsidian export, set `yread config set OUTPUT_DIR ...` (config-driven, no per-run flag).
4. Before publish, require a configured `HUB_TARGET`; let argument-free publish
   prepare missing local artifacts.
5. For local inspection, build first and open `.yread-dist/index.html` directly.

## Privacy Note

yread runs locally, but source snippets read by its tools are sent to the configured LLM provider. Do not run it on private or sensitive repositories unless the selected provider is acceptable for that code.

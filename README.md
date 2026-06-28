# yread

> Turn a local source repository into a structured Markdown wiki.

`yread` is a lightweight, installable Python CLI inspired by zread. It uses a two-phase LLM workflow to inspect a local repository, plan a wiki outline, and write one Markdown page per topic.

The `y` is intentional: it is zread's alphabet neighbor, and it also hints at "yet another" small local implementation.

## Why

Zread popularized the idea of generating a developer guide from a GitHub repository. `yread` keeps the same core idea, but narrows the scope:

- Local repositories only
- Direct OpenAI-compatible provider calls
- A small, readable Python implementation
- Markdown output that can be checked into notes, opened in Obsidian, or served locally

This is not a hosted wiki platform. It is a local code-reading tool.

## How It Works

`yread` runs two LLM-driven phases:

```text
Phase 1: Catalog Agent
  Inspect the repository
  Plan sections, groups, and topics
  Attach relevant source paths to each topic

Phase 2: Page Agents
  Start one fresh conversation per topic
  Inspect the relevant source files
  Write Markdown wrapped as a wiki page
```

Agents only receive three read-only tools:

| Tool | Purpose |
| --- | --- |
| `get_dir_structure` | Show a filtered directory tree |
| `view_file_in_detail` | Read source files by line range |
| `run_bash` | Run conservative read-only commands; can be disabled with `--no-shell` |

## Install and Run

From a checkout:

```bash
uv run yread generate /path/to/repo
```

The first positional argument also defaults to `generate`:

```bash
uv run yread /path/to/repo
```

Install as a uv tool:

```bash
uv tool install .
yread generate /path/to/repo
```

Output defaults to:

```text
<repo>/.yread/wiki/
├── current
└── versions/<YYYY-MM-DD-HHMMSS>/
    ├── <slug>.md
    ├── wiki.json
    ├── manifest.json
    └── SUMMARY.md
```

## Provider Configuration

`yread` can use `minimax-cn`, `deepseek`, or any OpenAI Chat Completions compatible endpoint.

For a generic OpenAI-compatible provider:

```bash
cp .env.yread.example .env.yread
$EDITOR .env.yread
uv run yread generate /path/to/repo --env-file .env.yread --concurrency 3
```

Persistent config lives at:

```text
~/.yread/config.env
```

Manage it with:

```bash
yread config path
yread config set PROVIDER openai-compatible
yread config set BASE_URL https://api.example.com/v1
yread config set API_KEY sk-...
yread config set MODEL your-model
yread config set DOC_LANG English
yread config show
```

Config precedence is:

```text
CLI flags > process environment > --env-file > ~/.yread/config.env > defaults
```

| Key | Default | Description |
| --- | --- | --- |
| `PROVIDER` | `minimax-cn` | `minimax-cn`, `deepseek`, or `openai-compatible` |
| `BASE_URL` | auto-resolved | OpenAI-compatible `/v1` endpoint |
| `API_KEY` | auto-resolved | Provider API key |
| `MODEL` | provider default | Model name |
| `DOC_LANG` | `English` | Documentation language |
| `MAX_STEPS` | `24` | Max tool-call rounds per agent |
| `MAX_TOPICS` | `30` | Catalog topic cap |
| `CONCURRENCY` | `1` | Parallel page agents |
| `ENABLE_SHELL` | `1` | Expose `run_bash` to agents |
| `OUTPUT_DIR` | `<repo>/.yread/wiki` | Default export directory |

For `minimax-cn` and `deepseek`, missing credentials are resolved from `~/.pi/agent/models.json` and `~/.pi/agent/auth.json` when available.

## Export to Obsidian

Set `OUTPUT_DIR` to a directory inside your vault:

```bash
yread config set OUTPUT_DIR "/path/to/Obsidian Vault/Code Wikis/yread"
yread generate /path/to/repo
```

Or override it for one run:

```bash
yread generate /path/to/repo --output-dir "/path/to/Obsidian Vault/Code Wikis/project-a"
```

## Resume and Regenerate

Resume the current wiki version and regenerate only missing, failed, or source-affected pages:

```bash
yread generate /path/to/repo --resume
```

Regenerate one page by slug, title, or Markdown filename:

```bash
yread generate /path/to/repo --page 1-overview
```

Disable shell access for agents:

```bash
yread generate /path/to/repo --no-shell
```

## Browse Locally

```bash
uv run yread view /path/to/repo/.yread/wiki --repo /path/to/repo --port 8000
```

Installed command:

```bash
yread-view /path/to/repo/.yread/wiki --repo /path/to/repo --port 8000
```

## Codex Skill

A companion Codex skill is available at [skills/yread/SKILL.md](skills/yread/SKILL.md).

Install it for local discovery:

```bash
cp -R skills/yread "${CODEX_HOME:-$HOME/.codex}/skills/"
```

## Example Output

See [examples/sample-wiki](examples/sample-wiki) for a static sample of the output layout. It demonstrates `wiki.json`, `manifest.json`, and Markdown page files; it is not a real model-generated run.

## Development

```bash
uv run --dev pytest -q
uv build
```

## Privacy

`yread` runs locally, but source snippets read by its tools are sent to the configured LLM provider. Do not run it on private or sensitive repositories unless that provider is acceptable for the code.

The file-reading tools block common secret files such as `.env`, private keys, and credential files. `run_bash` uses an allowlist and does not invoke a shell.

## Design Notes

- No hosted service: output is local Markdown.
- No AST parser: repository understanding is LLM-driven.
- No symbolic incremental engine: resume uses a file-level manifest plus per-page associated paths.
- Standard package layout: `src/yread/core.py` for generation, `src/yread/cli.py` for CLI/config, and `src/yread/viewer.py` for the local browser.

## Related Projects

- [zread.ai](https://zread.ai)
- [ZreadAI/zread_cli](https://github.com/ZreadAI/zread_cli)
- [bb-boy680/open-zread](https://github.com/bb-boy680/open-zread)
- [ejfkdev/zread](https://github.com/ejfkdev/zread)

## License

MIT

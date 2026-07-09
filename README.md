# yread

> Turn a local source repository into an architecture-first Markdown wiki.

`yread` is a lightweight, installable Python CLI — turn a local repository into an architecture-first Markdown wiki, powered by LLMs. Inspired by [zread](https://zread.ai).

## Why

Zread popularized the idea of generating a developer guide from a GitHub repository. `yread` keeps the same core idea, but narrows the scope:

- Local repositories only
- Direct OpenAI-compatible provider calls
- A small, readable Python implementation
- Markdown output focused on human architectural understanding

This is not a hosted wiki platform. It is a local project-understanding tool.

## How It Works

`yread` runs two LLM-driven phases:

```text
Phase 1: Catalog Agent
  Inspect the repository
  Build a lightweight project profile
  Plan architecture-first topics with evidence paths

Phase 2: Page Agents
  Start one fresh conversation per topic
  Inspect evidence files
  Write human-oriented architecture and maintenance guidance
```

Agents only receive three read-only tools:

| Tool | Purpose |
| --- | --- |
| `get_dir_structure` | Show a filtered directory tree |
| `view_file_in_detail` | Read source files by line range |
| `run_bash` | Run conservative read-only commands; disable with `ENABLE_SHELL=0` |

## Install and Run

Install from PyPI:

```bash
uv tool install yread        # or: pipx install yread, pip install yread
```

`generate` defaults to the current directory:

```bash
cd /path/to/repo
yread generate               # or: yread generate /path/to/repo
```

From a checkout, run without installing:

```bash
uv run yread generate /path/to/repo
```

Output defaults to:

```text
<repo>/.yread/
├── wiki/
│   └── <slug>.md
├── wiki.json
├── manifest.json
└── SUMMARY.md
```

## Project Profile

Inspect a repository's profile without calling an LLM — a dense, at-a-glance read
on a project's size, languages, and activity:

```bash
yread profile               # or: yread profile /path/to/repo
```

```text
Project    /path/to/repo
Files      237 source · 320 total · depth 6
Code       14,695 loc · avg 62/file · tests 53 loc (0.00x)

Languages
  Swift             7,673   73 files
  Objective-C       4,888   70 files
  C/C++               831   80 files
  Go                  692    5 files

Git        18 commits · 3 contributors · 2025-09-25→2026-07-06 · 5 in 30d · v0.3.0
GitHub     owner/repo · 12★ · MIT · pushed 2026-07-06
```

Every line count is **core code** — blank and comment-only lines are excluded, and
bundled dependencies (`Pods`, `Carthage`, `vendor`, `3rdparty`, build output, …)
are skipped, so the numbers track the team's own logic. The `Languages` table lists
each language's core code lines and sums to the `Code` total. Tests are counted
separately and shown as a ratio of core code.

For any git repository it adds a `git:` section — commit count, history span
(first/last commit dates), commits in the last 30 days, contributor count,
latest tag, and whether the working tree is dirty — all from local git, no
network.

When the repo's `origin` remote points to GitHub, it adds a `github:` section
from a single API call: description, stars, forks, watchers, open issues,
topics, license, homepage, default branch, last push, and `archived`/`fork`
flags. Set `GITHUB_TOKEN` for higher rate limits and private repos. On failure
the star line shows `n/a` with the reason (`offline` or `HTTP <code>`, e.g. a
rate-limited or private repo).

## Provider Configuration

`yread` can use `minimax-cn`, `deepseek`, or any OpenAI Chat Completions compatible endpoint.

For a generic OpenAI-compatible provider:

```bash
cp .env.yread.example .env.yread
$EDITOR .env.yread
uv run yread generate /path/to/repo --env-file .env.yread
```

All tunables (provider, model, language, depth, concurrency, output) live in config rather than on the `generate` command, keeping the command surface lean.

Persistent config lives at:

```text
~/.yread/config.env
```

Set it up interactively:

```bash
yread config init
```

Or manage individual keys:

```bash
yread config path
yread config set PROVIDER openai-compatible
yread config set BASE_URL https://api.example.com/v1
yread config set API_KEY sk-...
yread config set MODEL your-model
yread config set DOC_LANG en
yread config set DOC_DEPTH standard
yread config show
```

Config precedence is:

```text
process environment > --env-file > ~/.yread/config.env > defaults
```

| Key | Default | Description |
| --- | --- | --- |
| `PROVIDER` | `minimax-cn` | `minimax-cn`, `deepseek`, or `openai-compatible` |
| `BASE_URL` | auto-resolved | OpenAI-compatible `/v1` endpoint |
| `API_KEY` | auto-resolved | Provider API key |
| `MODEL` | provider default | Model name |
| `DOC_LANG` | `en` | Documentation language code, e.g. `zh`, `en` |
| `DOC_DEPTH` | `auto` | `auto`, `brief`, `standard`, or `deep`; controls topic budget and page breadth |
| `MAX_STEPS` | `24` | Max tool-call rounds per agent |
| `MAX_TOPICS` | `30` | Catalog topic cap |
| `CONCURRENCY` | `1` | Parallel page agents |
| `ENABLE_SHELL` | `1` | Expose `run_bash` to agents |
| `OUTPUT_DIR` | `<repo>/.yread` | Default export directory |
| `GITHUB_TOKEN` | unset | GitHub token for `profile` — raises API rate limits, unlocks private repos |

For `minimax-cn` and `deepseek`, missing credentials are resolved from `~/.pi/agent/models.json` and `~/.pi/agent/auth.json` when available.

## Export to Obsidian

Set `OUTPUT_DIR` to a directory inside your vault:

```bash
yread config set OUTPUT_DIR "/path/to/Obsidian Vault/Code Wikis/yread"
yread generate /path/to/repo
```

## Overwrite and Resume

A plain `yread generate` rebuilds the catalog and overwrites the current output
under `.yread/`. Markdown pages live in `.yread/wiki/`; `wiki.json`,
`manifest.json`, and `SUMMARY.md` live directly under `.yread/`.

If a previous run was interrupted or left failed pages, explicitly resume the
current output, regenerating only missing, failed, or source-affected pages:

```bash
yread generate /path/to/repo --resume
```

Resume and browse require the current v2 wiki schema.

Regenerate one page by slug, title, or Markdown filename:

```bash
yread generate /path/to/repo --page 1-overview
```

Disable shell access for agents (config-only):

```bash
yread config set ENABLE_SHELL 0
```

## Browse Locally

The source repository is recorded in `wiki.json` at generation time, so source
citations resolve automatically — from inside the repo, just run:

```bash
yread browse                       # serves ./.yread
```

Or point at a wiki explicitly; `--repo` overrides the recorded source root:

```bash
uv run yread browse /path/to/repo/.yread --host localhost --port 8000
```

## Codex Skill

A companion Codex skill is available at [skills/yread/SKILL.md](skills/yread/SKILL.md).

Install it for local discovery:

```bash
cp -R skills/yread "${CODEX_HOME:-$HOME/.codex}/skills/"
```

## Example Output

See [examples/sample-wiki](examples/sample-wiki) for a static sample of the v2 output layout. It demonstrates `wiki.json`, `manifest.json`, and Markdown page files; it is not a real model-generated run.

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
- Architecture-first pages: source paths are evidence, not the page structure.
- No symbolic incremental engine: resume uses a file-level manifest plus per-page evidence paths.
- Standard package layout: `src/yread/core.py` for generation, `src/yread/cli.py` for CLI/config, and `src/yread/viewer.py` for the local browser.

## Related Projects

- [zread.ai](https://zread.ai)
- [ZreadAI/zread_cli](https://github.com/ZreadAI/zread_cli)
- [bb-boy680/open-zread](https://github.com/bb-boy680/open-zread)
- [ejfkdev/zread](https://github.com/ejfkdev/zread)

## License

MIT

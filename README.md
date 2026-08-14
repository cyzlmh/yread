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
  repo                                               /path/to/repo
  A small tool that does one thing well

  CODE ───────────────────────────────────────────────────────────
    Lines of code  14,695    excludes blanks and comments
    Source files      237    of 320 files · 6 levels deep
    Avg per file       62    lines
    Test lines        531    0.04× of source · 3 files
    Structure                Package.swift, Podfile
    Entry                    Sources/App/main.swift

  LANGUAGES ──────────────────────────────────────────────────────
    Swift           7,673    ██████████████  54%
    Objective-C     4,888    ████████▉       35%
    C/C++             831    █▌               6%
    Go                692    █▎               5%

  REPOSITORY ─────────────────────────────────────────────────────
    Commits            18    5 in the last 30 days
    Contributors        3
    History                  2025-09-25 → 2026-07-06
    Version                  v0.3.0
    GitHub                   owner/repo · MIT
    Stars             128    12 forks · 4 open issues
    Pushed                   2026-07-06
```

Every row is the same three columns — what it is, the figure, and the detail — so
each section's numbers stack into one column you can scan straight down.

Every line count is **core code** — blank and comment-only lines are excluded, and
bundled dependencies (`Pods`, `Carthage`, `vendor`, `3rdparty`, build output, …)
are skipped, so the numbers track the team's own logic. The `LANGUAGES` section
lists each language's core code lines and sums to `Lines of code`. Tests are
counted separately and shown as a ratio of core code.

For any git repository the `REPOSITORY` section adds commit count, history span
(first/last commit dates), commits in the last 30 days, contributor count,
latest tag, and whether the working tree is dirty — all from local git, no
network.

When the repo's `origin` remote points to GitHub, the same section grows a few
rows from a single API call: description (shown under the title), stars, forks,
open issues, license, last push, and `archived`/`fork` flags. Set `GITHUB_TOKEN`
for higher rate limits and private repos. On failure the `Stars` row shows `n/a`
with the reason (`offline` or `HTTP <code>`, e.g. a rate-limited or private repo).

## Documentation Mode

Not every repository is a conventional software project. For ML / model projects —
training, fine-tuning, conversion, or deployment — the substance lives in configs,
training recipes, and model artifacts, which a pure architecture view under-weights.

`MODE` selects the documentation mode. It is explicit — there is no
auto-detection:

- `software` (default) — the standard architecture-first lens.
- `ml` — **model-first**. The profile detects the repo's model families (grouping
  weights and their `config.json` under each model directory), and the catalog plans
  **one page per model** — its architecture, provenance, input/output tensors, and
  label taxonomy (`id2label`) — with the serving/conversion code as supporting pages,
  not the headline. It unlocks the topic kinds `model-architecture`, `data-pipeline`,
  `model-conversion`, `model-serving`, `training`, and `evaluation`. Binary weights are
  never read as text — the agent infers each model from its `config.json`, modeling
  code, and export/convert scripts. Generic software pages are demoted to at most one.
- `skill` — **skill-first**, for agent-skill repositories (one or more `SKILL.md`
  files with `name`/`description` frontmatter, optionally bundling `scripts/`,
  `references/`, and `assets/`). There is no LLM catalog-planning step: the profile
  detects every `SKILL.md` and the catalog is built deterministically — **one page
  per skill**, plus one collection-overview page for multi-skill repos. Each skill
  page answers, for a reader who wants to grasp the skill in minutes: what it does,
  when an agent should invoke it, and how it works (the instruction flow plus what
  each bundled script and reference provides). Depth tiers do not apply — a page's
  size is bounded by the skill itself.

```bash
yread config set MODE ml        # switch to the ML lens
yread generate /path/to/repo --mode ml   # or per run
yread generate /path/to/skill-repo --mode skill
```

To decide which mode a repo needs, run `yread profile`. An `ASSETS` section surfaces
model weights and datasets — the files a source-line count ignores — and, when it finds
them, a `MODELS` section names each detected model family and its architecture:

```text
  ASSETS ─────────────────────────────────────────────────────────
    weights             9    files · 2.9 GB · .om×4 .onnx×4 .pth×1
    data                3    files · 40.0 MB · .wav×3

  MODELS ─────────────────────────────────────────────────────────
    audio_model              ASTForAudioClassification · .om .onnx .pth
    image_model              ? · .om .onnx
```

If it lists models, reach for `--mode ml`; if it lists skills (`SKILLS` section),
reach for `--mode skill`.

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
yread config set DEPTH standard
yread config show
```

Config precedence is:

```text
YREAD_* environment variable > --env-file > ~/.yread/config.env > defaults
```

Keys are **unprefixed** in the config file, in `--env-file`, and with `config set`
(a dedicated file can't clash with anything). As a **shell environment variable**,
prefix the key with `YREAD_` so common bare names never collide with unrelated
variables:

```bash
YREAD_MODE=ml YREAD_DEPTH=deep yread generate /path/to/repo
```

| Key | Default | Description |
| --- | --- | --- |
| `PROVIDER` | `minimax-cn` | `minimax-cn`, `deepseek`, or `openai-compatible` |
| `BASE_URL` | auto-resolved | OpenAI-compatible `/v1` endpoint |
| `API_KEY` | auto-resolved | Provider API key |
| `MODEL` | provider default | Model name |
| `DOC_LANG` | `en` | Documentation language code, e.g. `zh`, `en` |
| `DEPTH` | `brief` | `brief`, `standard`, or `deep`; controls topic budget and page breadth |
| `MODE` | `software` | `software` or `ml`; documentation mode (see [Documentation Mode](#documentation-mode)) |
| `MAX_STEPS` | `24` | Max tool-call rounds per agent |
| `MAX_TOPICS` | `30` | Catalog topic cap |
| `CONCURRENCY` | `1` | Parallel page agents |
| `ENABLE_SHELL` | `1` | Expose `run_bash` to agents |
| `OUTPUT_DIR` | `<repo>/.yread` | Default export directory |
| `GITHUB_TOKEN` | unset | GitHub token for `profile` — raises API rate limits, unlocks private repos. Also honors the standard, unprefixed `GITHUB_TOKEN` environment variable |
| `HUB_TARGET` | unset | Publish destination in `user@host:/absolute/path` form |

For `minimax-cn` and `deepseek`, missing credentials are resolved from `~/.pi/agent/models.json` and `~/.pi/agent/auth.json` when available.

## Export to Obsidian

Set `OUTPUT_DIR` to a directory inside your vault:

```bash
yread config set OUTPUT_DIR "/path/to/Obsidian Vault/Code Wikis/yread"
yread generate /path/to/repo
```

## Regenerate

Every `yread generate` run rebuilds the catalog and all pages, overwriting the
current generated output under `.yread/`. Markdown pages live in `.yread/wiki/`;
`wiki.json`, `manifest.json`, and `SUMMARY.md` live directly under `.yread/`.

`wiki.json` records an automatic `project_id`: GitHub repositories use
`owner/repo`, while other repositories use their directory name. Its `status` is
`building` during generation, `complete` when every page succeeds, and
`incomplete` when any page fails. Generated artifacts contain repository-relative
source paths, never the local repository's absolute path.

## Build Static HTML

Render a completed `.yread` artifact as static HTML:

```bash
yread build
# or: yread build /path/to/repo/.yread
```

The default output is `.yread-dist` beside the input. Use `--output-dir` to
choose another local directory. Each run replaces that directory and writes a
flat, self-contained site:

```text
.yread-dist/
├── index.html
├── 1-overview.html
└── 2-architecture.html
```

CSS and page behavior are embedded in every HTML file, so the pages can be
opened directly or served by any static file server. Mermaid diagrams currently
load Mermaid from a CDN. The project metadata required by publish is embedded
in `index.html`, making `.yread-dist` independently movable. Build does not call
an LLM, read the source repository, generate search indexes, or publish files.
Built pages use a centered reading column, responsive navigation, page-local
contents, and previous/next links. Source citations link to GitHub when the
project ID is a GitHub `owner/repo`; local-project citations remain plain text.
Build sanitizes generated HTML, strips executable raw markup, and only preserves
safe link and image URL schemes before pages can be published.

Disable shell access for agents (config-only):

```bash
yread config set ENABLE_SHELL 0
```

## Preview Locally

Build the static site, then open its entry page directly:

```bash
yread build
open .yread-dist/index.html        # macOS
```

No local HTTP server is required. Generated artifacts deliberately do not record
the local source repository path.

## Publish to a Hub

Prepare the server once with the pure-static
[Caddy scaffold](https://github.com/cyzlmh/yread/tree/main/deploy/caddy).
Configure its SSH destination locally, then publish the current project:

```bash
yread config set HUB_TARGET deploy@docs:/var/www/yread-hub
yread publish
# or publish an explicit built site without preparing prerequisites:
yread publish /path/to/repo/.yread-dist
```

With no directory argument, `publish` uses `.yread-dist` when it exists. If it
does not, publish builds it from `.yread`; if neither artifact exists, publish
runs generate first. These are existence checks only: publish does not compare
the source repository, timestamps, or content to decide whether to regenerate.
Passing a directory publishes that built site directly without running generate
or build.

The final publish step reads build metadata embedded in
`.yread-dist/index.html`, uploads the flat HTML site to
`projects/<project_id>/`, and adds a deployment-only `project.json` for the Hub
home page. It does not alter Caddy or restart a service. It requires `ssh` and
`rsync` on the client and `rsync` on the server;
authentication and custom ports belong in the normal SSH configuration. The
remote project directory is owned by publish and synchronized with
`rsync --delete`, so do not keep unrelated files in it.

The supplied Hub homepage discovers projects through Caddy's JSON directory
listing and filters them in the browser. There is no shared `projects.json`, so
independent repositories can publish without contending on a global index.
Built pages link back to the Hub root, while direct browser navigation to
`/projects/` redirects there instead of showing Caddy's directory page.

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

- No hosted service: output is local Markdown and static HTML.
- No AST parser: repository understanding is LLM-driven.
- Architecture-first pages: source paths are evidence, not the page structure.
- Full regeneration: each run rebuilds the catalog and every page.
- Standard package layout: `src/yread/core.py` for generation,
  `src/yread/builder.py` for static HTML, `src/yread/publisher.py` for SSH
  deployment, `src/yread/cli.py` for CLI/config, and `src/yread/viewer.py` for
  HTML rendering.

## Related Projects

- [zread.ai](https://zread.ai)
- [ZreadAI/zread_cli](https://github.com/ZreadAI/zread_cli)
- [bb-boy680/open-zread](https://github.com/bb-boy680/open-zread)
- [ejfkdev/zread](https://github.com/ejfkdev/zread)

## License

MIT

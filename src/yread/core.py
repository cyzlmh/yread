"""yread — turn a local repo into a structured wiki, the way zread does.

A two-phase LLM pipeline (reverse-engineered from a zread proxy log):

    Phase 1  catalog agent   -> a <section>/<group>/<topic> document outline
    Phase 2  per-page agent  -> one fresh conversation per topic, emits <blog>

Both agents see only three read-only tools (dir tree / read file / read-only
bash) and explore the repo themselves before writing.

Usage:
    yread generate [repo_path] [--env-file .env.yread]   (repo_path defaults to .)

Config via env:
    PROVIDER    which LLM provider to use     (minimax-cn | deepseek | openai-compatible)
    BASE_URL    override the provider endpoint (else resolved per provider)
    API_KEY     override the key              (else resolved per provider)
    MODEL       override the model name       (else the provider's default)
    DOC_LANG    documentation language code  (default en; e.g. zh | en; NOT $LANG)
    MAX_STEPS   tool-call rounds per agent   (default 24)
    MAX_TOPICS  catalog topic cap            (default 30)
    CONCURRENCY parallel page agents          (default 1)
    ENABLE_SHELL expose run_bash to agents    (default 1)
    OUTPUT_DIR  default wiki output directory (else <repo>/.yread/wiki)

Providers:
    minimax-cn  MiniMax-M3 called directly at api.minimaxi.com, credentials from ~/.pi
    deepseek    deepseek-chat called directly, credentials from ~/.pi
    openai-compatible  any Chat Completions compatible endpoint, configured explicitly

yread talks to the provider directly. It runs locally, but code snippets read by
the tools are sent to the configured provider.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI


IGNORE = {".git", "node_modules", "vendor", ".venv", "venv", "__pycache__",
          ".mypy_cache", ".pytest_cache", "dist", "build", ".idea", ".yread", ".zread",
          ".env", ".env.local", ".env.yread"}

SENSITIVE_NAMES = {
    ".env", ".env.local", ".env.production", ".env.development", ".npmrc", ".pypirc",
    ".netrc", "id_rsa", "id_ed25519", "auth.json", "credentials.json",
}
SENSITIVE_GLOBS = ("*.pem", "*.key", "*.p12", "*.pfx")


@dataclass(frozen=True)
class RuntimeConfig:
    provider: str
    base_url: str | None
    api_key: str | None
    model: str | None
    doc_lang: str
    max_steps: int
    max_topics: int
    concurrency: int
    enable_shell: bool
    output_dir: Path | None = None
    env_file: Path | None = None


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    base_url: str
    api_key: str
    model: str


def load_pi_provider(name: str) -> tuple[str, str]:
    """Resolve (base_url, api_key) for a provider from ~/.pi, the same files the
    llm-proxy reads."""
    home = Path.home() / ".pi" / "agent"
    prov = json.loads((home / "models.json").read_text())["providers"][name]
    base = prov["baseUrl"].rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    # auth.json is the live credential store; models.json apiKey can be stale.
    try:
        key = json.loads((home / "auth.json").read_text())[name]["key"]
    except (FileNotFoundError, KeyError):
        key = prov["apiKey"]
    return base, key


# Selectable LLM providers. The first two keep the original local workflow:
# credentials can be resolved from ~/.pi/agent. openai-compatible is the public
# escape hatch for any endpoint that speaks Chat Completions.
PROVIDERS = {
    "minimax-cn": {"base_url": None, "model": "MiniMax-M3",   "from_pi": True},
    "deepseek":   {"base_url": None, "model": "deepseek-chat", "from_pi": True},
    "openai-compatible": {"base_url": None, "model": None, "from_pi": False},
}


def _parse_env_file(path: Path | None) -> dict[str, str]:
    if not path:
        return {}
    if not path.exists():
        raise SystemExit(f"env file not found: {path}")
    env: dict[str, str] = {}
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            env[key] = value
    return env


def _parse_env_files(paths: list[Path]) -> dict[str, str]:
    env: dict[str, str] = {}
    for path in paths:
        if path.exists():
            env.update(_parse_env_file(path))
    return env


def _env_get(file_env: dict[str, str], name: str, default: str | None = None) -> str | None:
    # Precedence: process env > env file > default.
    return os.environ.get(name, file_env.get(name, default))


def _env_bool(file_env: dict[str, str], name: str, default: bool) -> bool:
    raw = _env_get(file_env, name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(file_env: dict[str, str], name: str, default: int) -> int:
    raw = _env_get(file_env, name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from e


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Turn a local repository into a lightweight structured wiki.",
    )
    p.add_argument("repo_path", nargs="?", default=".",
                   help="Repository to analyze (default: current directory)")
    p.add_argument("--env-file", type=Path, default=None,
                   help="Optional dotenv-style config file, e.g. .env.yread")
    p.add_argument("--resume", action="store_true", help="Resume latest incomplete wiki version")
    p.add_argument("--page", default=None,
                   help="Generate one page by slug, title, or markdown filename")
    p.add_argument("--force", action="store_true",
                   help="Regenerate pages even when output files already exist")
    return p


def config_from_args(args: argparse.Namespace, config_files: list[Path] | None = None) -> RuntimeConfig:
    config_files = list(config_files or [])
    file_env = _parse_env_files(config_files)
    if args.env_file:
        file_env.update(_parse_env_file(args.env_file))
    # All tunables now live in config (~/.yread/config.env, --env-file, or env vars),
    # mirroring zread: the generate command stays lean and config holds the knobs.
    # NB: do NOT read os.environ["LANG"] — that's the POSIX locale (e.g. en_US.UTF-8)
    # and would silently force English output. Use the dedicated DOC_LANG name.
    provider = _env_get(file_env, "PROVIDER", "minimax-cn") or "minimax-cn"
    if provider not in PROVIDERS:
        raise SystemExit(f"unknown PROVIDER {provider!r}; choose one of: {', '.join(PROVIDERS)}")
    concurrency = _env_int(file_env, "CONCURRENCY", 1)
    if concurrency < 1:
        raise SystemExit("CONCURRENCY must be >= 1")
    return RuntimeConfig(
        provider=provider,
        base_url=_env_get(file_env, "BASE_URL"),
        api_key=_env_get(file_env, "API_KEY"),
        model=_env_get(file_env, "MODEL"),
        doc_lang=_env_get(file_env, "DOC_LANG", "en") or "en",
        max_steps=_env_int(file_env, "MAX_STEPS", 24),
        max_topics=_env_int(file_env, "MAX_TOPICS", 30),
        concurrency=concurrency,
        enable_shell=_env_bool(file_env, "ENABLE_SHELL", True),
        output_dir=(
            Path(output_dir).expanduser() if (output_dir := _env_get(file_env, "OUTPUT_DIR")) else None
        ),
        env_file=args.env_file,
    )


def resolve_provider(config: RuntimeConfig) -> LLMSettings:
    """Resolve provider settings. Explicit BASE_URL / API_KEY / MODEL values win;
    ~/.pi is only a convenience fallback for local providers."""
    name = config.provider
    if name not in PROVIDERS:
        raise SystemExit(f"unknown PROVIDER {name!r}; choose one of: {', '.join(PROVIDERS)}")
    spec = PROVIDERS[name]
    base, key = config.base_url, config.api_key
    if spec["from_pi"] and (not base or not key):
        try:
            pi_base, pi_key = load_pi_provider(name)
            base = base or pi_base
            key = key or pi_key
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            pass
    model = config.model or spec["model"]
    missing = [label for label, value in (
        ("BASE_URL", base), ("API_KEY", key), ("MODEL", model),
    ) if not value]
    if missing:
        raise SystemExit(
            "missing LLM config: "
            + ", ".join(missing)
            + ". Set env vars, pass CLI flags, use --env-file .env.yread, "
              "or configure ~/.pi/agent for minimax-cn/deepseek."
        )
    return LLMSettings(name, str(base).rstrip("/"), str(key), str(model))


def make_client(settings: LLMSettings) -> OpenAI:
    return OpenAI(base_url=settings.base_url, api_key=settings.api_key, max_retries=8, timeout=180)


# --------------------------------------------------------------------------- #
# Tools — the three read-only capabilities the agents are given.              #
# --------------------------------------------------------------------------- #

def _gitignore_names(repo: Path) -> set[str]:
    names = set()
    gi = repo / ".gitignore"
    if gi.exists():
        for line in gi.read_text(errors="replace").splitlines():
            line = line.strip().strip("/")
            if line and not line.startswith("#") and "*" not in line:
                names.add(line.split("/")[0])
    return names


def is_sensitive_path(path: str | Path) -> bool:
    parts = Path(path).parts
    for part in parts:
        if part in SENSITIVE_NAMES or part.startswith(".env."):
            return True
        if any(fnmatch.fnmatch(part, pat) for pat in SENSITIVE_GLOBS):
            return True
    return False


def _is_ignored_entry(entry: Path, ignore: set[str]) -> bool:
    return entry.name in ignore or is_sensitive_path(entry.name)


def _relative_source_path(repo: Path, path: str | Path) -> str | None:
    raw = str(path).strip().strip("`'\"")
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute() or _arg_may_escape_repo(raw) or is_sensitive_path(raw):
        return None
    resolved = (repo / candidate).resolve()
    try:
        rel = resolved.relative_to(repo.resolve())
    except ValueError:
        return None
    if ".yread" in rel.parts or ".zread" in rel.parts:
        return None
    return rel.as_posix()


def normalize_source_paths(repo: Path, paths: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        rel = _relative_source_path(repo, path)
        if rel and rel not in seen:
            seen.add(rel)
            result.append(rel)
    return result


def iter_source_files(repo: Path) -> list[Path]:
    ignore = IGNORE | _gitignore_names(repo)
    files: list[Path] = []

    def walk(directory: Path) -> None:
        for entry in sorted(directory.iterdir(), key=lambda e: (e.is_file(), e.name.lower())):
            if _is_ignored_entry(entry, ignore):
                continue
            if entry.is_dir():
                walk(entry)
            elif entry.is_file():
                rel = _relative_source_path(repo, entry.relative_to(repo))
                if rel:
                    files.append(repo / rel)

    walk(repo.resolve())
    return files


def build_file_manifest(repo: Path) -> dict:
    repo = repo.resolve()
    files = []
    for path in iter_source_files(repo):
        rel = path.relative_to(repo).as_posix()
        try:
            data = path.read_bytes()
        except OSError:
            continue
        files.append({
            "path": rel,
            "hash": hashlib.sha256(data).hexdigest()[:16],
            "size": len(data),
        })
    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "files": files,
    }


def load_manifest(version_dir: Path) -> dict | None:
    path = version_dir / "manifest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def write_manifest(version_dir: Path, manifest: dict) -> None:
    (version_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def diff_manifests(previous: dict | None, current: dict | None) -> dict[str, list[str]]:
    if not previous or not current:
        return {"added": [], "modified": [], "removed": []}
    old = {f["path"]: f["hash"] for f in previous.get("files", [])}
    new = {f["path"]: f["hash"] for f in current.get("files", [])}
    added = sorted(path for path in new if path not in old)
    removed = sorted(path for path in old if path not in new)
    modified = sorted(path for path, digest in new.items() if path in old and old[path] != digest)
    return {"added": added, "modified": modified, "removed": removed}


def _path_affects_source(source: str, changed_path: str) -> bool:
    source = source.rstrip("/")
    return changed_path == source or changed_path.startswith(source + "/")


def page_sources_changed(page: dict, diff: dict[str, list[str]]) -> bool:
    sources = page.get("associatedFiles") or []
    if not sources:
        return False
    changed = diff["added"] + diff["modified"] + diff["removed"]
    return any(_path_affects_source(source, path) for source in sources for path in changed)


def get_dir_structure(repo: Path, dir_path: str = ".", max_depth: int = 3) -> str:
    ignore = IGNORE | _gitignore_names(repo)
    root = (repo / dir_path).resolve()
    if not str(root).startswith(str(repo.resolve())) or not root.exists():
        return f"[error] no such directory: {dir_path}"

    lines = ["."]

    def walk(d: Path, depth: int, prefix: str) -> None:
        if depth > max_depth:
            return
        entries = sorted(
            (e for e in d.iterdir() if not _is_ignored_entry(e, ignore) and e.name != ".git"),
            key=lambda e: (e.is_file(), e.name.lower()),
        )
        for i, e in enumerate(entries):
            last = i == len(entries) - 1
            lines.append(f"{prefix}{'└── ' if last else '├── '}{e.name}")
            if e.is_dir():
                walk(e, depth + 1, prefix + ("    " if last else "│   "))

    walk(root, 1, "")
    return "\n".join(lines)


def view_file_in_detail(repo: Path, file_path: str, start_line: int = 1,
                        end_line: int | None = None, show_line_numbers: bool = False) -> str:
    f = (repo / file_path).resolve()
    if not str(f).startswith(str(repo.resolve())) or not f.is_file():
        return f"[error] no such file: {file_path}"
    if is_sensitive_path(file_path):
        return f"[error] sensitive file is not readable through this tool: {file_path}"
    if ".yread" in f.parts or ".zread" in f.parts:
        return f"[error] generated wiki output is not part of the source repo: {file_path}"
    if end_line is None:
        end_line = start_line + 199
    out = []
    for n, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
        if n < start_line:
            continue
        if n > end_line:
            break
        out.append(f"{n:>6} | {line}" if show_line_numbers else line)
    return f"[file content] {file_path}\n" + "\n".join(out)


_SHELL_META = re.compile(r"[|&;<>()`$\\\n\r]")
_ALLOWED_COMMANDS = {
    "pwd", "ls", "find", "cat", "grep", "rg", "head", "tail", "wc", "sed", "git",
    "du", "stat", "file",
}
_ALLOWED_GIT_SUBCOMMANDS = {
    "status", "log", "show", "diff", "grep", "ls-files", "rev-parse", "branch",
    "describe", "tag", "remote",
}


def _arg_may_escape_repo(arg: str) -> bool:
    return arg.startswith("/") or arg == ".." or arg.startswith("../") or "/../" in arg


def _validate_readonly_argv(argv: list[str]) -> str:
    if not argv:
        return "empty command"
    exe = Path(argv[0]).name
    if exe != argv[0] or exe not in _ALLOWED_COMMANDS:
        return f"command not allowed: {argv[0]}"
    for arg in argv[1:]:
        if _arg_may_escape_repo(arg):
            return f"path outside repository is not allowed: {arg}"
        if is_sensitive_path(arg):
            return f"sensitive path is not allowed: {arg}"
    if exe == "git":
        sub = next((a for a in argv[1:] if not a.startswith("-")), "")
        if sub not in _ALLOWED_GIT_SUBCOMMANDS:
            return f"git subcommand not allowed: {sub or '<none>'}"
    if exe == "find" and any(a in {"-delete", "-exec", "-execdir", "-ok", "-okdir"} for a in argv[1:]):
        return "mutating find actions are not allowed"
    if exe == "sed" and any(a == "-i" or a.startswith("-i") or a == "--in-place" for a in argv[1:]):
        return "in-place sed edits are not allowed"
    return ""


def run_bash(repo: Path, command: str, enabled: bool = True) -> str:
    if not enabled:
        return "[error] run_bash is disabled by configuration"
    if _SHELL_META.search(command):
        return "[error] shell metacharacters are not allowed"
    try:
        argv = shlex.split(command)
    except ValueError as e:
        return f"[error] invalid shell syntax: {e}"
    reason = _validate_readonly_argv(argv)
    if reason:
        return f"[error] only conservative read-only commands are allowed: {reason}"
    try:
        r = subprocess.run(argv, shell=False, cwd=repo, timeout=30,
                           capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return "[error] command timed out (30s)"
    out = (r.stdout + r.stderr)
    return out[:8000] if out else "[no output]"


def tools_spec(enable_shell: bool) -> list[dict]:
    tools = [
        {"type": "function", "function": {
            "name": "get_dir_structure",
            "description": "Inspect the local directory structure as a tree. Supports a relative subdirectory "
                           "and maximum recursion depth. Automatically filters .gitignore entries, sensitive "
                           'files, and common dependency directories such as node_modules and vendor. Use "." '
                           "for the repository root.",
            "parameters": {"type": "object", "properties": {
                "dir_path": {"type": "string", "description": 'Relative directory path; "." means repository root'},
                "max_depth": {"type": "number", "description": "Maximum recursion depth; default 3"},
            }, "required": ["dir_path"]}}},
        {"type": "function", "function": {
            "name": "view_file_in_detail",
            "description": "Read local file content by relative path. Supports start/end line numbers "
                           "(default: first 200 lines) and optional line numbers. Sensitive files are blocked.",
            "parameters": {"type": "object", "properties": {
                "file_path": {"type": "string", "description": "Relative file path to read"},
                "start_line": {"type": "number", "description": "Start line, 1-based; default 1"},
                "end_line": {"type": "number", "description": "End line; default start_line + 199"},
                "show_line_numbers": {"type": "boolean", "description": "Whether to show line numbers; default false"},
            }, "required": ["file_path"]}}},
    ]
    if enable_shell:
        tools.append({"type": "function", "function": {
            "name": "run_bash",
            "description": "Run conservative read-only commands in the local repository. Allows query commands "
                           "such as ls/find/cat/grep/rg/head/tail/wc/sed and selected git read commands. "
                           "Shell pipes, redirects, network access, writes, and sensitive paths are blocked.",
            "parameters": {"type": "object", "properties": {
                "command": {"type": "string", "description": "Read-only command to execute"},
            }, "required": ["command"]}}})
    return tools


MAX_TOOL_CHARS = 24000  # keep any single tool result from blowing past the upstream body limit


def dispatch(repo: Path, name: str, args: dict, enable_shell: bool) -> str:
    try:
        if name == "get_dir_structure":
            result = get_dir_structure(repo, args.get("dir_path", "."), int(args.get("max_depth", 3)))
        elif name == "view_file_in_detail":
            result = view_file_in_detail(
                repo, args["file_path"], int(args.get("start_line", 1)),
                int(args["end_line"]) if args.get("end_line") is not None else None,
                bool(args.get("show_line_numbers", False)))
        elif name == "run_bash":
            result = run_bash(repo, args["command"], enabled=enable_shell)
        else:
            return f"[error] unknown tool: {name}"
    except Exception as e:  # noqa: BLE001 — tool errors must reach the model, not crash the run
        return f"[error] {e!r}"
    if len(result) > MAX_TOOL_CHARS:
        result = result[:MAX_TOOL_CHARS] + f"\n[... truncated, {len(result) - MAX_TOOL_CHARS} more chars]"
    return result


# --------------------------------------------------------------------------- #
# Agent loop                                                                   #
# --------------------------------------------------------------------------- #

def run_agent(client: OpenAI, repo: Path, messages: list, label: str,
              settings: LLMSettings, config: RuntimeConfig) -> str:
    specs = tools_spec(config.enable_shell)
    for _step in range(config.max_steps):
        resp = client.chat.completions.create(model=settings.model, messages=messages, tools=specs)
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            return msg.content or ""
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = dispatch(repo, tc.function.name, args, config.enable_shell)
            print(f"    [{label}] {tc.function.name}({json.dumps(args, ensure_ascii=False)})", flush=True)
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": "Tool execute results: " + result})
    # Out of tool budget: force a final answer with no tools. Some models keep
    # emitting tool-call syntax as raw text unless told explicitly to stop, so
    # add a directive to switch from gathering to producing the final output.
    messages.append({"role": "user", "content":
        "You have used your entire tool budget. Do NOT call or mention any more "
        "tools. Based solely on what you have already gathered, produce your "
        "complete final answer now, exactly in the required output format."})
    resp = client.chat.completions.create(model=settings.model, messages=messages)
    return resp.choices[0].message.content or ""


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def tool_usage_description(enable_shell: bool) -> str:
    lines = [
        "- get_dir_structure: Inspect the local directory structure as a tree. Supports a relative subdirectory and maximum recursion depth. Automatically filters .gitignore entries, sensitive files, and common dependency directories such as node_modules and vendor. dir_path is relative to the working directory; \".\" means repository root.",
        "- view_file_in_detail: Read local file content by relative path. Supports start/end line numbers (default: first 200 lines) and optional line numbers. Sensitive files are blocked.",
    ]
    if enable_shell:
        lines.append(
            "- run_bash: Run conservative read-only commands in the local repository. Allows query commands such as ls, find, cat, grep, rg, head, tail, wc, git log, and git show. Shell pipes, redirects, network access, writes, deletes, mutations, and sensitive paths are blocked. Commands run in the working directory with a 30-second timeout."
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Phase 1 — catalog                                                           #
# --------------------------------------------------------------------------- #

CATALOG_SYSTEM = """You are an expert software engineer and technical writer with deep experience in deconstructing complex codebases. Your specialty is not just reading code, but understanding its design philosophy, identifying its target audience, and communicating its essence in a clear, structured, and user-oriented manner.

## Environment
- Working directory: {workdir}
- Operating system: {os}

## Tool Usage Guide
You have the following tools to gather information about the local repository:
<description>
{tool_usage}
</description>
If you already have enough information, just respond without calling tools.
ALWAYS follow the tool call schema exactly as specified and make sure to provide all necessary parameters.

## Analysis Framework
You always follow these four steps meticulously to deconstructing a complex codebase.
<guidance>
### Step 1: High-Level Vision & Value (The "Why")
Begin by establishing the strategic context of the repository. Answer the foundational questions before diving into the code.
*   **Core Purpose & Value Proposition:**
    *   What specific problem does this repository solve? State it clearly and concisely.
    *   For a developer investing time to study this codebase, what are the key takeaways and transferable skills they can expect to learn?

### Step 2: Architectural Deep Dive (The "What" & "How")
Deconstruct the repository's structure and implementation. Focus on the technical design and how it achieves its purpose.
*   **Architectural Overview:**
    *   Describe the high-level architecture.
    *   What are the core modules or directories? For each one, define its single responsibility.
*   **Key Modules & Implementation:**
    *   Identify the 2-3 most critical modules that form the heart of this repository.
    *   How do these key modules interact with each other?

### Step 3: Audience-Centric Analysis (The "Who")
Tailor your analysis to the end-users of the documentation. Identify the primary audience:
*   **Frontend Developers:** UI components, framework integration, state management, performance.
*   **Backend Developers:** API design, database schemas, scalability, security, concurrency, deployment.
*   **Algorithm Engineers/Researchers:** Core algorithm correctness, efficiency, mathematical foundations.
*   **Learners/Students:** Clear explanations, step-by-step tutorials, logical progression.

### Step 4: Synthesize & Structure the Output (The "How to Present")
Now, compile your findings into a final, well-structured document catalog.
*   **Structural Rules:**
    *   **Create a Logical Hierarchy:** Use clear, descriptive headings.
    *   **Abstract, Don't Mirror:** Do not use file or folder names as headings. Create meaningful topic titles.
    *   **Be Concise and Accurate:** Ensure every title is a perfect summary of the section's content.
*   **Final Output Structure:**
1. Structure the outline into **sections** strictly as below:
   - `Get Started`: onboarding content, quick wins (tutorials, setup, usage)
     - The first two topics under this section must be:
       - `Overview`: a high-level summary of what the project does and why it matters
       - `Quick Start`: step-by-step setup to run or try out the project
   - `Deep Dive`: technical explanation and reference material (concepts, architecture, APIs)
2. Within each `<section>`, you may include `<topic level="...">` and optional `<group>` to cluster related topics.
   - Each topic must include its difficulty level: Beginner, Intermediate, or Advanced
   - Each topic should include `files="..."` with 1-4 exact relative source files or directories that best support that page.
   - Do not include generated output, dependency directories, secret files, or absolute paths in `files`.
3. **Total topic count must not exceed {max_topics}.** Prioritize the most important topics. Merge or omit less critical topics to stay within this limit.
</guidance>

### Output Example
Analyse the repository deeply first, then provide a comprehensive catalog. Your output must follow **this exact pattern**:

<section>
Section Name
<topic level="..." files="README.md, pyproject.toml">
Topic A
</topic>
<group>
Group Name
<topic level="..." files="src/main.py, src/runtime/">
Topic B
</topic>
<topic level="..." files="tests/">
Topic C
</topic>
</group>
</section>

<section>
…
</section>"""

CATALOG_USER = """Produce a comprehensive document catalog that serves as a high-quality guide for developers of this local repository.

## Instructions
1. Use `get_dir_structure` to understand the project layout. For deeply nested repos, expand folders as needed.
2. Use `view_file_in_detail` to read key source files (README, entry points, core modules).
3. If available, use `run_bash` to run read-only commands for additional insights (e.g., finding entry points, listing file types).
4. Before each tool call, think carefully about what you observed in the previous result and what you need next.

## Your Task
Information about the current repository:
<metadata>
Working directory: {workdir}
Operating system: {os}
Documentation language: {lang}

Repository structure (top levels):
{tree}
</metadata>

Output ONLY the document catalog, without any explanation or comments. Use {lang} as the language for all section names and topic titles. The total number of topics must not exceed {max_topics}. Each topic must include `files="..."` with 1-4 relative source files or directories that support the page. Structure each section like this:

<section>
Section Name
<topic level="..." files="README.md, src/main.py">
Topic Title
</topic>
<group>
Group Name
<topic level="..." files="src/runtime/">
Topic Title
</topic>
</group>
</section>"""


def _parse_tag_attrs(line: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in re.finditer(r'([A-Za-z_]+)="([^"]*)"', line)}


def _split_associated_files(raw: str) -> list[str]:
    parts = []
    for item in re.split(r"[,;\n]", raw):
        item = item.strip().strip("-* ").strip().strip("`'\"")
        if item:
            parts.append(item)
    return parts


def parse_catalog(text: str) -> list[dict]:
    """Parse the <section>/<group>/<topic> outline into an ordered page list."""
    pages: list[dict] = []
    section = group = ""
    topic: dict | None = None
    expect = None

    def finish_topic() -> None:
        nonlocal topic
        if topic and topic.get("title"):
            topic["associatedFiles"] = _split_associated_files("\n".join(topic.get("associatedFiles", [])))
            pages.append(topic)
        topic = None

    for raw in strip_think(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("<section"):
            finish_topic()
            expect, group = "section", ""
        elif line.startswith("</section"):
            finish_topic()
            section = group = ""
        elif line.startswith("<group"):
            finish_topic()
            expect = "group"
        elif line.startswith("</group"):
            finish_topic()
            group = ""
        elif line.startswith("<topic"):
            finish_topic()
            attrs = _parse_tag_attrs(line)
            files = attrs.get("files") or attrs.get("associatedFiles") or attrs.get("associated_files") or ""
            topic = {
                "section": section,
                "group": group,
                "title": "",
                "level": attrs.get("level", "Intermediate"),
                "associatedFiles": _split_associated_files(files),
            }
            expect = "topic"
        elif line.startswith("</topic"):
            finish_topic()
            expect = None
        elif line.startswith("<files"):
            expect = "files"
            inline = re.sub(r"</?files[^>]*>", "", line).strip()
            if inline and topic:
                topic.setdefault("associatedFiles", []).extend(_split_associated_files(inline))
        elif line.startswith("</files"):
            expect = None
        elif line.startswith("<"):
            continue
        else:
            if expect == "section":
                section = line
            elif expect == "group":
                group = line
            elif expect == "topic" and topic:
                topic["title"] = line
            elif expect == "files" and topic:
                topic.setdefault("associatedFiles", []).append(line)
            if expect != "files":
                expect = None
    finish_topic()
    return pages


def slugify(index: int, title: str) -> str:
    # Keep word characters (Unicode letters/digits/underscore, so CJK titles stay
    # readable instead of being romanized). Collapse any other run (spaces,
    # punctuation, filename-hostile chars like / \ : * ? " < > |) into one hyphen.
    body = re.sub(r"[^\w]+", "-", title, flags=re.UNICODE).strip("-")
    return f"{index}-{body}" if body else f"{index}"


def render_nav(pages: list[dict], current: int) -> str:
    lines: list[str] = []
    last_section = last_group = None
    for i, p in enumerate(pages):
        if p["section"] != last_section:
            lines.append(f"- **{p['section']}**")
            last_section, last_group = p["section"], None
        group = p.get("group", "")
        if group:
            if group != last_group:
                lines.append(f"  - *{group}*")
                last_group = group
            indent = "    "
        else:
            last_group, indent = None, "  "
        here = " [You are currently here]" if i == current else ""
        lines.append(f"{indent}- [{p['title']}]({p['slug']}){here}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Phase 2 — per-page documentation                                            #
# --------------------------------------------------------------------------- #

PAGE_SYSTEM = """You are an INTJ technical documentation architect with code archaeology expertise — methodical, insightful, and precision-oriented.

## Environment
- Working directory: {workdir}
- Operating system: {os}

## Identity & Methodology
- Core Approach: systematic pattern recognition, architectural clarity, logical precision.
- Documentation Framework: Diátaxis methodology + AIDA narrative (Attention -> Interest -> Desire -> Action).
- Analysis Pattern: start from first principles, identify core patterns, then examine implementation detail.

## Technical Standards
- **Content Structure**: Paragraph-driven with breaks only at cognitive boundaries
- **Visual Elements**:
  - Mermaid diagrams for architectural concepts (with prerequisite explanation)
  - Tables for multi-dimensional comparisons
  - Strategic bold for conceptual anchoring
- **Evidence Standard**:
  - Sources: [filename](relative/path/to/file#L<start>-L<end>) at paragraph boundaries
  - Zero speculation — document only verifiable patterns
- **Cross-references**: Use `[Page Title](page_slug)` syntax for linking to other pages in the wiki

## Tool Usage Protocol
Hypothesis-driven investigation: formulate specific architectural questions -> select precise tools -> verify minimal scope -> synthesize."""

PAGE_USER = """## CURRENT MISSION
**Working directory**: {workdir}
**Operating system**: {os}
**Current Page**: "{title}" documentation
**Audience**: {level} level developers
**Documentation language**: {lang}

## ENVIRONMENT
Repository structure (top 2 levels):
```
{tree}
```

## NAVIGATION CONTEXT
**Full Catalog with Your Position**:
```
{nav}
```
**Content Boundaries**:
- Write ONLY about "{title}" — avoid content that belongs to other catalog pages.
- Reference other pages by their exact catalog links when suggesting next steps.

**Associated source paths**:
```
{associated_files}
```
Use these associated paths as your primary starting points. You may inspect other files when needed to verify context, but keep the final page focused on this source scope.

## DOCUMENT TYPE REQUIREMENTS
**Global requirement**:
- Reference local files at the end of every paragraph as: `Sources: [filename](relative/path#L<start>-L<end>)`
- Use {lang} as the language for all written content

**For Overview/Getting Started docs**:
- Suggest logical reading progression based on catalog structure using exact catalog links: `[Page Name](page_slug)`
- Create architecture overview with Mermaid diagram
- Use tables for feature comparisons, configuration options, or API summaries
- Add visual project structure representation

**For How-to/Tutorial docs**:
- Include step-by-step Mermaid flowcharts
- Use tables for parameter explanations, troubleshooting guides
- Add before/after code comparison tables

**For Explanation docs**:
- Create concept relationship diagrams with Mermaid
- Use tables for pattern comparisons, pros/cons analysis
- Include class/module interaction diagrams

## OUTPUT FORMAT
Wrap your FINAL complete documentation in <blog></blog> tags:

<blog>
# {title}
Brief intro of this page's purpose and scope.
## Section Name
Content focused solely on {title}
Sources: [filename](relative/path#L123-L456)
</blog>

## EXECUTE NOW
Begin with architectural hypothesis formation. Verify through targeted code examination using the available tools. Deliver "{title}" documentation with visual elements and precise local file references. Remember to wrap your FINAL output in <blog></blog> tags."""


def extract_blog(text: str) -> str:
    m = re.search(r"<blog>(.*?)</blog>", text, flags=re.DOTALL)
    return (m.group(1) if m else strip_think(text)).strip()


# MiniMax-M3 sometimes emits tool-call syntax as raw text instead of real
# tool_calls; the SDK can't parse it, so run_agent returns the delimiter
# garbage as if it were the final answer. Detect that (and empty output) so we
# can force a clean retry instead of writing the garbage to disk.
_BAD_MARKERS = ("]<]minimax[>[", "<tool_call>", "<invoke name=", "</invoke>", "minimax[>[")


def _page_defect(raw: str) -> str:
    """Inspect the agent's RAW reply. A valid page MUST be a <blog></blog> block;
    its absence usually means the model announced the doc ("Now let me write the
    documentation.") and ended the turn without producing it."""
    if any(m in raw for m in _BAD_MARKERS):
        return "leaked raw tool-call syntax into the prose"
    m = re.search(r"<blog>(.*?)</blog>", raw, flags=re.DOTALL)
    if not m:
        return "no <blog></blog> block — you announced the doc but never produced it"
    if len(m.group(1).strip()) < 40:
        return "empty or near-empty output"
    return ""


def generate_page(client: OpenAI, repo: Path, messages: list, slug: str,
                  settings: LLMSettings, config: RuntimeConfig) -> str:
    """Run the page agent, then validate the RAW reply. On a tool-call-delimiter
    leak, a missing <blog> block (announced but never written), or empty output,
    re-prompt with tools disabled to force a clean <blog>."""
    raw = run_agent(client, repo, messages, slug, settings, config)
    for _ in range(2):
        defect = _page_defect(raw)
        if not defect:
            break
        messages.append({"role": "user", "content":
            f"Your previous reply was rejected: {defect}. Do NOT output any tool "
            "call, tool name, or tool-call delimiter token, and do NOT merely say "
            "you will write it. Using ONLY what you have already gathered, output "
            "your COMPLETE final documentation wrapped in <blog></blog> now."})
        raw = client.chat.completions.create(model=settings.model, messages=messages
                                             ).choices[0].message.content or ""
        messages.append({"role": "assistant", "content": raw})
    defect = _page_defect(raw)
    if defect:
        raise RuntimeError(f"page generation did not produce valid <blog>: {defect}")
    return extract_blog(raw)


# --------------------------------------------------------------------------- #
# Orchestration                                                               #
# --------------------------------------------------------------------------- #

def assign_page_fields(pages: list[dict], repo: Path | None = None) -> list[dict]:
    for i, p in enumerate(pages, 1):
        p["index"] = i
        p.setdefault("slug", slugify(i, p["title"]))
        p.setdefault("file", f"{p['slug']}.md")
        p["associatedFiles"] = list(p.get("associatedFiles") or [])
        if repo:
            p["associatedFiles"] = normalize_source_paths(repo, p["associatedFiles"])
    return pages


def build_catalog(client: OpenAI, repo: Path, config: RuntimeConfig,
                  settings: LLMSettings, tree: str) -> list[dict]:
    ctx = {
        "workdir": str(repo),
        "os": sys.platform,
        "lang": lang_name(config.doc_lang),
        "max_topics": config.max_topics,
        "tool_usage": tool_usage_description(config.enable_shell),
    }
    messages = [
        {"role": "system", "content": CATALOG_SYSTEM.format(**ctx)},
        {"role": "user", "content": CATALOG_USER.format(tree=tree, **ctx)},
    ]
    catalog_raw = run_agent(client, repo, messages, "catalog", settings, config)
    pages = parse_catalog(catalog_raw)
    if not pages:
        raise SystemExit("no topics parsed from catalog:\n" + catalog_raw)
    return assign_page_fields(pages, repo)


def lang_code(doc_lang: str) -> str:
    """Normalize DOC_LANG (standard code like `zh`/`en`, or a legacy name) to a code."""
    s = doc_lang.strip().lower()
    return {"chinese": "zh", "中文": "zh", "english": "en"}.get(s, s[:2])


def lang_name(doc_lang: str) -> str:
    """Human-readable language name for prompts, derived from the standard code."""
    return {"zh": "Chinese", "en": "English"}.get(lang_code(doc_lang), doc_lang)


def write_wiki_index(version_dir: Path, pages: list[dict], version_id: str,
                     started: datetime, doc_lang: str, manifest: dict,
                     source_root: Path | None = None) -> None:
    meta = {
        "id": version_id,
        "generated_at": started.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "language": lang_code(doc_lang),
        "source_root": str(source_root) if source_root else "",
        "pages": [
            {k: v for k, v in (
                ("slug", p["slug"]), ("title", p["title"]), ("file", p["file"]),
                ("section", p["section"]), ("group", p.get("group", "")),
                ("level", p.get("level", "")),
                ("associatedFiles", p.get("associatedFiles", [])),
            ) if v}
            for p in pages
        ],
    }
    (version_dir / "wiki.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = ["# Wiki\n"]
    last_section = last_group = None
    for p in pages:
        if p["section"] != last_section:
            summary.append(f"\n## {p['section']}\n")
            last_section, last_group = p["section"], None
        if p.get("group") and p["group"] != last_group:
            summary.append(f"\n**{p['group']}**\n")
            last_group = p["group"]
        summary.append(f"- [{p['title']}]({p['file']}) `{p.get('level', '')}`")
    (version_dir / "SUMMARY.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    write_manifest(version_dir, manifest)


def load_current_wiki(out_dir: Path) -> tuple[Path, list[dict], dict | None] | None:
    cur = out_dir / "current"
    if not cur.exists():
        return None
    version_dir = (out_dir / cur.read_text().strip()).resolve()
    meta_path = version_dir / "wiki.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text())
    pages = assign_page_fields([dict(p) for p in meta["pages"]])
    return version_dir, pages, load_manifest(version_dir)


def page_matches(page: dict, selector: str | None) -> bool:
    if not selector:
        return True
    needle = selector.strip().lower()
    candidates = {
        page.get("slug", ""),
        page.get("title", ""),
        page.get("file", ""),
        Path(page.get("file", "")).stem,
    }
    return any(str(c).lower() == needle for c in candidates)


def page_needs_generation(version_dir: Path, page: dict, force: bool,
                          manifest_diff: dict[str, list[str]] | None = None) -> bool:
    if force:
        return True
    path = version_dir / page["file"]
    if not path.exists():
        return True
    content = path.read_text(errors="replace").strip()
    if not content:
        return True
    if "This page failed to generate" in content[:500]:
        return True
    return page_sources_changed(page, manifest_diff or {"added": [], "modified": [], "removed": []})


def version_is_incomplete(version_dir: Path, pages: list[dict]) -> bool:
    """A version is incomplete if any page is missing, empty, or failed."""
    return any(page_needs_generation(version_dir, p, False) for p in pages)


def page_messages(repo: Path, config: RuntimeConfig, tree: str,
                  pages: list[dict], page: dict) -> list[dict]:
    ctx = {
        "workdir": str(repo),
        "os": sys.platform,
        "lang": lang_name(config.doc_lang),
        "max_topics": config.max_topics,
        "tool_usage": tool_usage_description(config.enable_shell),
    }
    nav = render_nav(pages, page["index"] - 1)
    associated = "\n".join(f"- {p}" for p in page.get("associatedFiles", [])) or "- (catalog did not bind source paths)"
    return [
        {"role": "system", "content": PAGE_SYSTEM.format(**ctx)},
        {"role": "user", "content": PAGE_USER.format(
            title=page["title"], level=page.get("level", "Intermediate"),
            tree=tree, nav=nav, associated_files=associated, **ctx)},
    ]


def write_one_page(settings: LLMSettings, config: RuntimeConfig, repo: Path,
                   version_dir: Path, tree: str, pages: list[dict],
                   page: dict) -> tuple[str, bool, str | None]:
    client = make_client(settings)
    messages = page_messages(repo, config, tree, pages, page)
    target = version_dir / page["file"]
    try:
        blog = generate_page(client, repo, messages, page["slug"], settings, config)
        ok, error = True, None
        target.write_text(blog + "\n", encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — one bad page must not abort the whole wiki
        ok, error = False, repr(e)
        existing = target.read_text(errors="replace").strip() if target.exists() else ""
        if not existing or "This page failed to generate" in existing[:500]:
            blog = f"# {page['title']}\n\n> This page failed to generate: {e!r}\n\nRun again with `--resume` to retry this page."
            target.write_text(blog + "\n", encoding="utf-8")
    return page["slug"], ok, error


def plan_pages(version_dir: Path, pages: list[dict], selector: str | None,
               force: bool, manifest_diff: dict[str, list[str]] | None = None) -> tuple[list[dict], list[dict]]:
    matched = [p for p in pages if page_matches(p, selector)]
    if selector and not matched:
        choices = ", ".join(p["slug"] for p in pages[:10])
        raise SystemExit(f"no page matched {selector!r}; available slugs include: {choices}")
    todo = [
        p for p in matched
        if page_needs_generation(version_dir, p, force or bool(selector), manifest_diff)
    ]
    skipped = [p for p in matched if p not in todo]
    return todo, skipped


def generate_pages(settings: LLMSettings, config: RuntimeConfig, repo: Path,
                   version_dir: Path, tree: str, pages: list[dict],
                   todo: list[dict]) -> tuple[int, int]:
    completed = failed = 0
    if not todo:
        return completed, failed
    workers = min(config.concurrency, len(todo))
    if workers == 1:
        for p in todo:
            print(f"  - ({p['index']}/{len(pages)}) {p['title']} -> {p['file']}", flush=True)
            _slug, ok, error = write_one_page(settings, config, repo, version_dir, tree, pages, p)
            completed += int(ok)
            failed += int(not ok)
            if error:
                print(f"    [!] page failed: {error}", flush=True)
        return completed, failed

    print(f"      concurrency: {workers}", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for p in todo:
            print(f"  - ({p['index']}/{len(pages)}) {p['title']} -> {p['file']}", flush=True)
            fut = pool.submit(write_one_page, settings, config, repo, version_dir, tree, pages, p)
            futures[fut] = p
        for fut in as_completed(futures):
            p = futures[fut]
            _slug, ok, error = fut.result()
            completed += int(ok)
            failed += int(not ok)
            status = "done" if ok else f"failed: {error}"
            print(f"    [{p['slug']}] {status}", flush=True)
    return completed, failed


def run_generate(args: argparse.Namespace, config: RuntimeConfig) -> Path:
    repo = Path(args.repo_path).resolve()
    if not repo.is_dir():
        raise SystemExit(f"not a directory: {repo}")
    out_dir = config.output_dir.resolve() if config.output_dir else repo / ".yread" / "wiki"
    out_dir.mkdir(parents=True, exist_ok=True)

    current_manifest = build_file_manifest(repo)
    tree = get_dir_structure(repo, ".", 2)
    resume = args.resume
    existing = load_current_wiki(out_dir) if (resume or args.page) else None
    # Auto-resume an interrupted run instead of silently starting a duplicate version.
    if existing is None and not resume and not args.page and not args.force:
        candidate = load_current_wiki(out_dir)
        if candidate and version_is_incomplete(candidate[0], candidate[1]):
            print(f"[!] incomplete previous run at {candidate[0]}; resuming "
                  f"(use --force to start a fresh version)", flush=True)
            existing, resume = candidate, True
    manifest_diff = {"added": [], "modified": [], "removed": []}
    changed_count = 0

    settings: LLMSettings | None = None
    prev_current: str | None = None
    if existing:
        version_dir, pages, previous_manifest = existing
        manifest_diff = diff_manifests(previous_manifest, current_manifest)
        print(f"[1/2] reusing catalog from {version_dir}", flush=True)
        print(f"      {len(pages)} topics", flush=True)
        changed_count = sum(len(v) for v in manifest_diff.values())
        if changed_count:
            print(f"      source changes: {changed_count} file(s)", flush=True)
    else:
        if resume:
            raise SystemExit(f"no resumable version found under {out_dir}; run without --resume to start a new wiki")
        cur_ptr = out_dir / "current"
        prev_current = cur_ptr.read_text().strip() if cur_ptr.exists() else None
        settings = resolve_provider(config)
        client = make_client(settings)
        print(
            f"[1/2] building catalog for {repo} via {settings.provider}:{settings.model} @ {settings.base_url}",
            flush=True,
        )
        pages = build_catalog(client, repo, config, settings, tree)
        print(f"      {len(pages)} topics", flush=True)

        started = datetime.now(timezone.utc)
        version_id = started.astimezone().strftime("%Y-%m-%d-%H%M%S")
        version_dir = out_dir / "versions" / version_id
        version_dir.mkdir(parents=True, exist_ok=True)
        write_wiki_index(version_dir, pages, version_id, started, config.doc_lang,
                         current_manifest, source_root=repo)
        (out_dir / "current").write_text(f"versions/{version_id}\n", encoding="utf-8")

    todo, skipped = plan_pages(version_dir, pages, args.page, args.force, manifest_diff)
    for p in skipped:
        print(f"  - skip existing {p['title']} -> {p['file']}", flush=True)

    # Phase 2: one fresh conversation per page -------------------------------
    print(f"[2/2] writing {len(todo)} page(s)", flush=True)
    if todo:
        settings = settings or resolve_provider(config)
        completed, failed = generate_pages(settings, config, repo, version_dir, tree, pages, todo)
    else:
        completed = failed = 0
    if existing and not args.page and failed == 0:
        if changed_count and not any(p.get("associatedFiles") for p in pages):
            print("      manifest not updated: current catalog has no associatedFiles", flush=True)
        else:
            write_manifest(version_dir, current_manifest)
    elif existing and args.page:
        print("      manifest not updated after single-page regeneration", flush=True)
    # Failure protection: a fresh build that produced no pages must not replace a
    # previously-good wiki. Restore the prior `current` pointer (or unset it).
    if not existing and completed == 0:
        cur_ptr = out_dir / "current"
        if prev_current:
            cur_ptr.write_text(prev_current + "\n", encoding="utf-8")
            print(f"      no pages generated; kept previous wiki ({prev_current}) as current", flush=True)
        else:
            cur_ptr.unlink(missing_ok=True)
            print("      no pages generated; left current unset", flush=True)
    elif failed:
        print("      re-run `yread generate` to retry failed pages (auto-resumes)", flush=True)
    print(f"\ndone -> {version_dir} ({completed} completed, {failed} failed, {len(skipped)} skipped)", flush=True)
    return version_dir


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    config = config_from_args(args)
    run_generate(args, config)


if __name__ == "__main__":
    main()

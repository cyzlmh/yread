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
    DOC_DEPTH   documentation depth          (default auto; auto | brief | standard | deep)
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
from dataclasses import asdict, dataclass, replace
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

DOC_DEPTHS = {"auto", "brief", "standard", "deep"}
TOPIC_KINDS = {
    "overview", "quickstart", "architecture", "concepts", "runtime-flow",
    "extension-points", "tradeoffs", "change-guide", "reference",
}
REQUIRED_TOPIC_KINDS = {"overview"}

SOURCE_EXTENSIONS = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".cs": "C#",
    ".cpp": "C++",
    ".cxx": "C++",
    ".cc": "C++",
    ".c": "C",
    ".h": "C/C++",
    ".hpp": "C++",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".scala": "Scala",
    ".sh": "Shell",
}

PACKAGE_FILES = (
    "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml",
    "build.gradle", "build.gradle.kts", "Gemfile", "composer.json",
)
ENTRYPOINT_PATTERNS = (
    "main.py", "app.py", "server.py", "cli.py", "__main__.py",
    "src/main.py", "src/app.py", "src/server.py",
    "src/main.ts", "src/main.tsx", "src/index.ts", "src/index.tsx",
    "src/main.js", "src/index.js", "cmd", "main.go",
)


@dataclass(frozen=True)
class RuntimeConfig:
    provider: str
    base_url: str | None
    api_key: str | None
    model: str | None
    doc_lang: str
    doc_depth: str
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


@dataclass(frozen=True)
class ProjectProfile:
    total_files: int
    source_files: int
    primary_languages: list[str]
    max_depth: int
    has_readme: bool
    has_tests: bool
    has_ci: bool
    package_files: list[str]
    entry_points: list[str]


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


def _env_choice(file_env: dict[str, str], name: str, default: str, choices: set[str]) -> str:
    raw = (_env_get(file_env, name, default) or default).strip().lower()
    if raw not in choices:
        raise SystemExit(f"{name} must be one of: {', '.join(sorted(choices))}")
    return raw


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
    p.add_argument("--output-dir", type=Path, default=None,
                   help="Wiki output directory (overrides OUTPUT_DIR config)")
    p.add_argument("--doc-depth", choices=sorted(DOC_DEPTHS), default=None,
                   help="Documentation depth: auto, brief, standard, or deep")
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
    doc_depth = _env_choice(file_env, "DOC_DEPTH", "auto", DOC_DEPTHS)
    if getattr(args, "doc_depth", None):
        doc_depth = args.doc_depth
    raw_output_dir = _env_get(file_env, "OUTPUT_DIR")
    output_dir: Path | None = Path(raw_output_dir).expanduser() if raw_output_dir else None
    if getattr(args, "output_dir", None):
        output_dir = args.output_dir.expanduser()
    return RuntimeConfig(
        provider=provider,
        base_url=_env_get(file_env, "BASE_URL"),
        api_key=_env_get(file_env, "API_KEY"),
        model=_env_get(file_env, "MODEL"),
        doc_lang=_env_get(file_env, "DOC_LANG", "en") or "en",
        doc_depth=doc_depth,
        max_steps=_env_int(file_env, "MAX_STEPS", 24),
        max_topics=_env_int(file_env, "MAX_TOPICS", 30),
        concurrency=concurrency,
        enable_shell=_env_bool(file_env, "ENABLE_SHELL", True),
        output_dir=output_dir,
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


def normalize_evidence_files(repo: Path, paths: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        rel = _relative_source_path(repo, path)
        if not rel or rel in seen:
            continue
        candidate = repo / rel
        if candidate.exists():
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


def build_project_profile(repo: Path) -> ProjectProfile:
    repo = repo.resolve()
    files = iter_source_files(repo)
    rel_paths = [p.relative_to(repo).as_posix() for p in files]
    language_counts: dict[str, int] = {}
    source_files = 0
    max_depth = 0
    for rel in rel_paths:
        path = Path(rel)
        max_depth = max(max_depth, len(path.parts))
        lang = SOURCE_EXTENSIONS.get(path.suffix.lower())
        if lang:
            source_files += 1
            language_counts[lang] = language_counts.get(lang, 0) + 1
    primary_languages = [
        lang for lang, _count in sorted(language_counts.items(), key=lambda item: (-item[1], item[0]))[:3]
    ]
    package_files = [name for name in PACKAGE_FILES if (repo / name).is_file()]
    entry_points = _detect_entry_points(repo, rel_paths, package_files)
    has_readme = any(Path(path).name.lower().startswith("readme") for path in rel_paths)
    has_tests = any(
        path == "tests" or path.startswith("tests/") or Path(path).name.startswith("test_")
        or Path(path).name.endswith(".test.ts") or Path(path).name.endswith(".test.js")
        for path in rel_paths
    )
    has_ci = (repo / ".github" / "workflows").is_dir() or any(
        path in {".gitlab-ci.yml", "azure-pipelines.yml"} for path in rel_paths
    )
    return ProjectProfile(
        total_files=len(files),
        source_files=source_files,
        primary_languages=primary_languages,
        max_depth=max_depth,
        has_readme=has_readme,
        has_tests=has_tests,
        has_ci=has_ci,
        package_files=package_files,
        entry_points=entry_points[:8],
    )


def _detect_entry_points(repo: Path, rel_paths: list[str], package_files: list[str]) -> list[str]:
    seen: set[str] = set()
    entries: list[str] = []

    def add(path: str) -> None:
        rel = _relative_source_path(repo, path)
        if rel and rel in rel_paths and rel not in seen:
            seen.add(rel)
            entries.append(rel)

    for rel in rel_paths:
        name = Path(rel).name
        if rel in ENTRYPOINT_PATTERNS or name in ENTRYPOINT_PATTERNS:
            add(rel)
        elif rel.startswith("src/") and name in {"cli.py", "__main__.py", "main.go"}:
            add(rel)

    if "pyproject.toml" in package_files:
        text = (repo / "pyproject.toml").read_text(errors="replace")
        for module in re.findall(r"=\s*\"([A-Za-z_][\w.]*):", text):
            candidate = "src/" + module.replace(".", "/") + ".py"
            add(candidate)

    if "package.json" in package_files:
        try:
            pkg = json.loads((repo / "package.json").read_text(errors="replace"))
        except json.JSONDecodeError:
            pkg = {}
        for key in ("main", "module", "browser"):
            if isinstance(pkg.get(key), str):
                add(pkg[key])
        bin_value = pkg.get("bin")
        if isinstance(bin_value, str):
            add(bin_value)
        elif isinstance(bin_value, dict):
            for value in bin_value.values():
                if isinstance(value, str):
                    add(value)

    return entries


def resolve_doc_depth(profile: ProjectProfile, requested: str) -> str:
    if requested != "auto":
        return requested
    if profile.source_files <= 20 and profile.total_files <= 40:
        return "brief"
    if profile.source_files >= 120 or profile.total_files >= 250 or profile.max_depth >= 6:
        return "deep"
    return "standard"


def topic_budget_for_depth(depth: str, max_topics: int) -> int:
    if depth == "brief":
        return min(max_topics, 5)
    if depth == "standard":
        return min(max_topics, 15)
    return max_topics


def profile_summary(profile: ProjectProfile) -> str:
    return json.dumps(asdict(profile), ensure_ascii=False, indent=2)


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
    sources = page.get("evidenceFiles") or []
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

CATALOG_SYSTEM = """You are an expert software architect and technical writer. Your job is to plan documentation for humans who need to understand a codebase: its purpose, architecture, concepts, runtime flow, extension points, design tradeoffs, and safe change paths.

## Environment
- Working directory: {workdir}
- Operating system: {os}

## Tool Usage Guide
You have the following tools to gather information about the local repository:
<description>
{tool_usage}
</description>
If you already have enough information, respond without calling tools.
Always follow the tool call schema exactly.

## Documentation Philosophy
This wiki is not a source-code encyclopedia. Do not plan pages that merely summarize files, folders, functions, parameters, or classes. Plan pages that help a developer build a correct mental model:
- What problem does the project solve?
- What are the core abstractions and domain concepts?
- What are the main control/data flows?
- Where are the architectural boundaries and adapters?
- What are the extension points and safe change paths?
- What tradeoffs, constraints, and maintenance risks should a human know?

Use source paths only as evidence for a topic. They are not the topic itself.

## Catalog Contract
1. Output v2 topics only. Every topic MUST use:
   `<topic kind="..." level="..." files="...">`
2. `kind` MUST be exactly one of:
   overview, quickstart, architecture, concepts, runtime-flow, extension-points, tradeoffs, change-guide, reference
3. `level` should be Beginner, Intermediate, or Advanced.
4. `files` must contain 1-4 exact relative files or directories that support the page as evidence.
5. Do not include generated output, dependency directories, secret files, absolute paths, or paths outside the repository.
6. Keep titles concise. Prefer architecture concepts and maintenance tasks over file names.
7. Total topic count must not exceed {topic_budget}. Merge or omit lower-value topics to stay within this budget.

## Depth Policy
Requested documentation depth: {doc_depth}
- brief: prioritize Overview, Architecture Map, and one practical change path.
- standard: add Core Concepts, Runtime Flow, Extension Points, and Design Tradeoffs where supported by evidence.
- deep: expand important subsystems and maintenance paths, but still avoid file-by-file documentation.

## Required Priority
At minimum, include one `overview` topic if supported by any evidence. Prefer also including `architecture`, `concepts`, `runtime-flow`, and `extension-points` when the project has enough substance.

## Output Example
Output ONLY the catalog. Use this exact pattern:

<section>
Section Name
<topic kind="overview" level="Beginner" files="README.md, pyproject.toml">
Project Overview
</topic>
<group>
Architecture
<topic kind="architecture" level="Intermediate" files="src/main.py, src/runtime/">
Architecture Map
</topic>
<topic kind="runtime-flow" level="Intermediate" files="src/runtime/, tests/">
Runtime Flow
</topic>
</group>
</section>"""

CATALOG_USER = """Produce a v2 architecture-first document catalog for this local repository.

## Instructions
1. Use `get_dir_structure` to understand the project layout. For deeply nested repos, expand folders as needed.
2. Use `view_file_in_detail` to read key source files (README, entry points, core modules).
3. If available, use `run_bash` to run read-only commands for additional insights.
4. Plan for human architectural understanding, not code detail reproduction.

## Your Task
Information about the current repository:
<metadata>
Working directory: {workdir}
Operating system: {os}
Documentation language: {lang}
Documentation depth: {doc_depth}
Topic budget: {topic_budget}

Repository structure (top levels):
{tree}

Project profile:
{project_profile}
</metadata>

Output ONLY the document catalog, without explanation or comments. Use {lang} for section names and topic titles. Keep `kind` values in English exactly as specified. The total number of topics must not exceed {topic_budget}. Each topic must include `kind`, `level`, and `files`.

<section>
Section Name
<topic kind="overview" level="Beginner" files="README.md, src/main.py">
Topic Title
</topic>
<group>
Group Name
<topic kind="architecture" level="Intermediate" files="src/runtime/">
Topic Title
</topic>
</group>
</section>"""


def _parse_tag_attrs(line: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in re.finditer(r'([A-Za-z_]+)="([^"]*)"', line)}


def _split_path_list(raw: str) -> list[str]:
    parts = []
    for item in re.split(r"[,;\n]", raw):
        item = item.strip().strip("-* ").strip().strip("`'\"")
        if item:
            parts.append(item)
    return parts


def parse_catalog(text: str) -> list[dict]:
    """Parse the v2 <section>/<group>/<topic> outline into an ordered page list."""
    pages: list[dict] = []
    section = group = ""
    topic: dict | None = None
    expect = None

    def finish_topic() -> None:
        nonlocal topic
        if topic:
            title = topic.get("title", "").strip()
            kind = topic.get("kind", "").strip()
            files = _split_path_list("\n".join(topic.get("evidenceFiles", [])))
            if not title:
                raise ValueError("catalog topic is missing a title")
            if kind not in TOPIC_KINDS:
                raise ValueError(f"catalog topic {title!r} has invalid or missing kind")
            if not files:
                raise ValueError(f"catalog topic {title!r} is missing evidence files")
            topic["title"] = title
            topic["evidenceFiles"] = files
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
            files = attrs.get("files") or attrs.get("evidenceFiles") or attrs.get("evidence_files") or ""
            topic = {
                "section": section,
                "group": group,
                "kind": attrs.get("kind", ""),
                "title": "",
                "level": attrs.get("level", "Intermediate"),
                "evidenceFiles": _split_path_list(files),
            }
            expect = "topic"
        elif line.startswith("</topic"):
            finish_topic()
            expect = None
        elif line.startswith("<files"):
            expect = "files"
            inline = re.sub(r"</?files[^>]*>", "", line).strip()
            if inline and topic:
                topic.setdefault("evidenceFiles", []).extend(_split_path_list(inline))
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
                topic.setdefault("evidenceFiles", []).append(line)
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


def _shorten_title(title: str, limit: int = 80) -> str:
    title = " ".join(title.split())
    if len(title) <= limit:
        return title
    return title[: limit - 3].rstrip() + "..."


def clean_catalog_pages(repo: Path, pages: list[dict], topic_budget: int) -> list[dict]:
    cleaned: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for page in pages:
        title = _shorten_title(str(page.get("title", "")).strip())
        kind = str(page.get("kind", "")).strip()
        if not title or kind not in TOPIC_KINDS:
            continue
        evidence = normalize_evidence_files(repo, list(page.get("evidenceFiles") or []))
        if not evidence:
            continue
        key = (kind, title.lower())
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({
            "section": str(page.get("section", "")).strip() or "Guide",
            "group": str(page.get("group", "")).strip(),
            "kind": kind,
            "title": title,
            "level": str(page.get("level", "Intermediate")).strip() or "Intermediate",
            "evidenceFiles": evidence[:4],
        })
        if len(cleaned) >= topic_budget:
            break
    return cleaned


def catalog_defect(pages: list[dict]) -> str:
    if not pages:
        return "no valid v2 topics with kind, title, and evidence files"
    kinds = {p.get("kind") for p in pages}
    missing = sorted(REQUIRED_TOPIC_KINDS - kinds)
    if missing:
        return "missing required topic kind(s): " + ", ".join(missing)
    return ""


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

PAGE_SYSTEM = """You are an expert software architect and technical writer. Write for humans who need a durable mental model of the project, not a line-by-line explanation of the code.

## Environment
- Working directory: {workdir}
- Operating system: {os}

## Writing Standard
- Architecture first: explain intent, core abstractions, boundaries, flows, constraints, and safe change paths.
- Evidence based: document only patterns you can verify from local files.
- Source citations: end paragraphs with `Sources: [filename](relative/path#L<start>-L<end>)`.
- Cross-references: use `[Page Title](page_slug)` syntax.
- Avoid code encyclopedias: do not summarize every function, translate source files, or mirror directory structure.
- Use diagrams and tables only when they clarify the page's architectural purpose."""

PAGE_USER = """## CURRENT MISSION
**Working directory**: {workdir}
**Operating system**: {os}
**Current Page**: "{title}" documentation
**Page kind**: {kind}
**Audience**: {level} level developers
**Documentation language**: {lang}
**Documentation depth**: {doc_depth}

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

**Evidence source paths**:
```
{evidence_files}
```
Use these paths as primary evidence. You may inspect other files when needed to verify context, but keep the final page focused on this topic.

## DOCUMENT TYPE REQUIREMENTS
- Use {lang} for all written content.
- For `overview`: explain purpose, audience, reading path, and the main architectural idea.
- For `quickstart`: focus on how to run or try the project only when supported by evidence.
- For `architecture`: explain components, responsibilities, boundaries, and relationships.
- For `concepts`: explain domain terms and core abstractions.
- For `runtime-flow`: trace the main control/data flow from entry point to output.
- For `extension-points`: explain where and how to add capabilities safely.
- For `tradeoffs`: explain verified design choices, constraints, and consequences.
- For `change-guide`: explain practical maintenance paths and risk areas.
- For `reference`: provide compact orientation material that supports the other pages.

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
Form an architectural hypothesis, verify it with targeted code examination, then write the page. Remember to wrap your FINAL output in <blog></blog> tags."""


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
        p["kind"] = p.get("kind", "reference")
        p["evidenceFiles"] = list(p.get("evidenceFiles") or [])
        if repo:
            p["evidenceFiles"] = normalize_evidence_files(repo, p["evidenceFiles"])
    return pages


def _catalog_pages_from_raw(repo: Path, raw: str, topic_budget: int) -> tuple[list[dict], str]:
    try:
        pages = clean_catalog_pages(repo, parse_catalog(raw), topic_budget)
    except ValueError as e:
        return [], str(e)
    defect = catalog_defect(pages)
    if defect:
        return pages, defect
    return assign_page_fields(pages, repo), ""


def build_catalog(client: OpenAI, repo: Path, config: RuntimeConfig,
                  settings: LLMSettings, tree: str, profile: ProjectProfile) -> list[dict]:
    topic_budget = topic_budget_for_depth(config.doc_depth, config.max_topics)
    ctx = {
        "workdir": str(repo),
        "os": sys.platform,
        "lang": lang_name(config.doc_lang),
        "doc_depth": config.doc_depth,
        "topic_budget": topic_budget,
        "project_profile": profile_summary(profile),
        "tool_usage": tool_usage_description(config.enable_shell),
    }
    messages = [
        {"role": "system", "content": CATALOG_SYSTEM.format(**ctx)},
        {"role": "user", "content": CATALOG_USER.format(tree=tree, **ctx)},
    ]
    catalog_raw = run_agent(client, repo, messages, "catalog", settings, config)
    pages, defect = _catalog_pages_from_raw(repo, catalog_raw, topic_budget)
    if defect:
        messages.append({"role": "user", "content":
            "Your catalog was rejected: "
            + defect
            + ". Output a complete corrected v2 catalog now. Do not call tools. "
              "Every topic must include kind, level, title, and 1-4 existing evidence files."})
        catalog_raw = client.chat.completions.create(model=settings.model, messages=messages
                                                     ).choices[0].message.content or ""
        messages.append({"role": "assistant", "content": catalog_raw})
        pages, defect = _catalog_pages_from_raw(repo, catalog_raw, topic_budget)
    if defect:
        raise SystemExit("invalid v2 catalog: " + defect + "\n" + catalog_raw)
    return pages


def lang_code(doc_lang: str) -> str:
    """Normalize DOC_LANG (standard code like `zh`/`en`, or a legacy name) to a code."""
    s = doc_lang.strip().lower()
    return {"chinese": "zh", "中文": "zh", "english": "en"}.get(s, s[:2])


def lang_name(doc_lang: str) -> str:
    """Human-readable language name for prompts, derived from the standard code."""
    return {"zh": "Chinese", "en": "English"}.get(lang_code(doc_lang), doc_lang)


def write_wiki_index(version_dir: Path, pages: list[dict], version_id: str,
                     started: datetime, doc_lang: str, doc_depth: str,
                     profile: ProjectProfile, manifest: dict,
                     source_root: Path | None = None) -> None:
    meta = {
        "schema_version": 2,
        "id": version_id,
        "generated_at": started.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "language": lang_code(doc_lang),
        "doc_depth": doc_depth,
        "project_profile": asdict(profile),
        "source_root": str(source_root) if source_root else "",
        "pages": [
            {k: v for k, v in (
                ("slug", p["slug"]), ("title", p["title"]), ("file", p["file"]),
                ("section", p["section"]), ("group", p.get("group", "")),
                ("kind", p.get("kind", "")),
                ("level", p.get("level", "")),
                ("evidenceFiles", p.get("evidenceFiles", [])),
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
        summary.append(f"- [{p['title']}]({p['file']}) `{p.get('kind', '')}` `{p.get('level', '')}`")
    (version_dir / "SUMMARY.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    write_manifest(version_dir, manifest)


def load_current_wiki(out_dir: Path, strict: bool = True) -> tuple[Path, list[dict], dict | None, dict] | None:
    cur = out_dir / "current"
    if not cur.exists():
        return None
    version_dir = (out_dir / cur.read_text().strip()).resolve()
    meta_path = version_dir / "wiki.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text())
    if meta.get("schema_version") != 2:
        if strict:
            raise SystemExit(
                f"unsupported wiki schema under {version_dir}: expected schema_version 2. "
                "Start a fresh v2 wiki with --force."
            )
        return None
    pages = assign_page_fields([dict(p) for p in meta["pages"]])
    return version_dir, pages, load_manifest(version_dir), meta


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
        "doc_depth": config.doc_depth,
        "tool_usage": tool_usage_description(config.enable_shell),
    }
    nav = render_nav(pages, page["index"] - 1)
    evidence = "\n".join(f"- {p}" for p in page.get("evidenceFiles", [])) or "- (catalog did not bind evidence paths)"
    return [
        {"role": "system", "content": PAGE_SYSTEM.format(**ctx)},
        {"role": "user", "content": PAGE_USER.format(
            title=page["title"], kind=page.get("kind", "reference"),
            level=page.get("level", "Intermediate"),
            tree=tree, nav=nav, evidence_files=evidence, **ctx)},
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
    current_profile = build_project_profile(repo)
    tree = get_dir_structure(repo, ".", 2)
    resume = args.resume
    existing = load_current_wiki(out_dir) if (resume or args.page) else None
    # Auto-resume an interrupted run instead of silently starting a duplicate version.
    if existing is None and not resume and not args.page and not args.force:
        candidate = load_current_wiki(out_dir, strict=False)
        if candidate and version_is_incomplete(candidate[0], candidate[1]):
            print(f"[!] incomplete previous run at {candidate[0]}; resuming "
                  f"(use --force to start a fresh version)", flush=True)
            existing, resume = candidate, True
    manifest_diff = {"added": [], "modified": [], "removed": []}
    changed_count = 0

    settings: LLMSettings | None = None
    prev_current: str | None = None
    if existing:
        version_dir, pages, previous_manifest, meta = existing
        config = replace(config, doc_depth=str(meta["doc_depth"]))
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
        config = replace(config, doc_depth=resolve_doc_depth(current_profile, config.doc_depth))
        settings = resolve_provider(config)
        client = make_client(settings)
        print(
            f"[1/2] building catalog for {repo} via {settings.provider}:{settings.model} @ {settings.base_url}",
            flush=True,
        )
        print(f"      doc depth: {config.doc_depth}", flush=True)
        pages = build_catalog(client, repo, config, settings, tree, current_profile)
        print(f"      {len(pages)} topics", flush=True)

        started = datetime.now(timezone.utc)
        version_id = started.astimezone().strftime("%Y-%m-%d-%H%M%S")
        version_dir = out_dir / "versions" / version_id
        version_dir.mkdir(parents=True, exist_ok=True)
        write_wiki_index(version_dir, pages, version_id, started, config.doc_lang,
                         config.doc_depth, current_profile, current_manifest, source_root=repo)
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
        if changed_count and not any(p.get("evidenceFiles") for p in pages):
            print("      manifest not updated: current catalog has no evidenceFiles", flush=True)
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

"""Command line entry point for yread."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from . import __version__, core, viewer


CONFIG_DIR = Path.home() / ".yread"
CONFIG_FILE = CONFIG_DIR / "config.env"

CONFIG_KEYS = {
    "PROVIDER",
    "BASE_URL",
    "API_KEY",
    "MODEL",
    "DOC_LANG",
    "DEPTH",
    "MODE",
    "MAX_STEPS",
    "MAX_TOPICS",
    "CONCURRENCY",
    "ENABLE_SHELL",
    "OUTPUT_DIR",
    "GITHUB_TOKEN",
}


def _read_config() -> dict[str, str]:
    if not CONFIG_FILE.exists():
        return {}
    return core._parse_env_file(CONFIG_FILE)


def _write_config(values: dict[str, str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# yread local configuration",
        "# Edit with `yread config set KEY VALUE` or by changing this file.",
    ]
    for key in sorted(values):
        lines.append(f"{key}={values[key]}")
    CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_generate_parser() -> argparse.ArgumentParser:
    return core.build_arg_parser()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yread",
        description="Turn a local repository into a lightweight structured wiki.",
    )
    sub = parser.add_subparsers(dest="command")

    generate = sub.add_parser("generate", help="Generate or resume a repo wiki")
    for action in _build_generate_parser()._actions:
        if action.dest == "help":
            continue
        generate._add_action(action)

    browse = sub.add_parser("browse", help="Open a generated wiki in the browser")
    browse.add_argument("wiki_dir", nargs="?", help="Yread output root")
    browse.add_argument("--host", default="localhost", help="Host to bind")
    browse.add_argument("--port", type=int, default=8000)
    browse.add_argument("--repo", default=None, help="Source repository for source links")
    browse.add_argument("--enable-source", action="store_true",
                        help="Multi-wiki mode: serve /src/ source files from each wiki's recorded source_root")

    sub.add_parser("version", help="Print the version number")

    profile = sub.add_parser("profile", help="Print the project profile without calling an LLM")
    profile.add_argument("repo_path", nargs="?", default=".",
                         help="Repository to analyze (default: current directory)")

    config = sub.add_parser("config", help=f"Manage {CONFIG_FILE}")
    config_sub = config.add_subparsers(dest="config_command")

    config_sub.add_parser("init", help="Interactively set up the config file")
    config_sub.add_parser("path", help="Print the config file path")
    config_sub.add_parser("show", help="Print current config")

    set_cmd = config_sub.add_parser("set", help="Set one config value")
    set_cmd.add_argument("key", choices=sorted(CONFIG_KEYS))
    set_cmd.add_argument("value")

    unset_cmd = config_sub.add_parser("unset", help="Remove one config value")
    unset_cmd.add_argument("key", choices=sorted(CONFIG_KEYS))

    return parser


INIT_PROMPTS = [
    ("PROVIDER", "Provider [minimax-cn/deepseek/openai-compatible]", "openai-compatible"),
    ("BASE_URL", "OpenAI-compatible /v1 endpoint", ""),
    ("API_KEY", "API key", ""),
    ("MODEL", "Model name", ""),
    ("DOC_LANG", "Documentation language code [zh/en]", "en"),
    ("DEPTH", "Documentation depth [brief/standard/deep]", "brief"),
    ("MODE", "Documentation mode [software/ml]", "software"),
    ("OUTPUT_DIR", "Output directory (blank = <repo>/.yread)", ""),
]


def _run_config_init() -> int:
    values = _read_config()
    print(f"Configuring {CONFIG_FILE} (Enter keeps the shown value)\n")
    for key, label, fallback in INIT_PROMPTS:
        current = values.get(key, fallback)
        suffix = f" [{current}]" if current else ""
        entered = input(f"{label}{suffix}: ").strip()
        chosen = entered or current
        if chosen:
            values[key] = chosen
        else:
            values.pop(key, None)
    _write_config(values)
    print(f"\nwrote {CONFIG_FILE}")
    return 0


def _run_config(args: argparse.Namespace) -> int:
    command = args.config_command or "show"
    if command == "init":
        return _run_config_init()
    if command == "path":
        print(CONFIG_FILE)
        return 0
    values = _read_config()
    if command == "show":
        if not values:
            print(f"# no config yet: {CONFIG_FILE}")
            return 0
        for key in sorted(values):
            print(f"{key}={values[key]}")
        return 0
    if command == "set":
        values[args.key] = args.value
        _write_config(values)
        print(CONFIG_FILE)
        return 0
    if command == "unset":
        values.pop(args.key, None)
        _write_config(values)
        print(CONFIG_FILE)
        return 0
    raise SystemExit(f"unknown config command: {command}")


def _run_browse(args: argparse.Namespace) -> int:
    viewer_args = []
    if args.wiki_dir:
        viewer_args.append(args.wiki_dir)
    viewer_args.extend(["--host", args.host, "--port", str(args.port)])
    if args.repo:
        viewer_args.extend(["--repo", args.repo])
    if args.enable_source:
        viewer_args.append("--enable-source")
    viewer.main(viewer_args)
    return 0


def _human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# --- profile rendering ------------------------------------------------------
# A tiny stdlib-only styling layer (no rich dependency). Every line is the same
# three columns — dim label, right-aligned figure, dim detail — so the numbers
# stack into one column the eye can scan instead of being spliced into prose
# ("2,284 loc · 571 avg/file"). Restrained ANSI that degrades to plain text when
# stdout is piped, NO_COLOR is set, or the terminal is dumb.

_COLOR = False
_WIDTH_CAP = 64
_LABEL = 14  # metric name column
_VALUE = 7   # right-aligned figure column
_BAR_BLOCKS = " ▏▎▍▌▋▊▉█"


def _supports_color() -> bool:
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty() and os.environ.get("TERM") != "dumb"


def _enable_windows_ansi() -> None:
    """Turn on virtual-terminal processing so ANSI works in the Win10+ console."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VT_PROCESSING
    except Exception:  # noqa: BLE001,S110 — best-effort console setup; never block output
        pass


def _paint(code: str, text: str) -> str:
    # Empty text stays empty: an escape pair around nothing is invisible but still
    # counts as content, which would keep _row from trimming an absent note.
    return f"\033[{code}m{text}\033[0m" if _COLOR and text else text


def _bold(s: str) -> str:
    return _paint("1", s)


def _dim(s: str) -> str:
    return _paint("2", s)


def _accent(s: str) -> str:
    return _paint("36", s)  # cyan


def _rule_width() -> int:
    return min(shutil.get_terminal_size((80, 24)).columns, _WIDTH_CAP)


def _bar(frac: float, width: int = 20) -> str:
    eighths = round(frac * width * 8)
    full, rem = divmod(eighths, 8)
    return ("█" * full + (_BAR_BLOCKS[rem] if rem else "")).ljust(width)


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _header(name: str, path: str, desc: str | None) -> None:
    pad = max(1, _rule_width() - len(name) - len(path))
    print(f"  {_bold(_accent(name))}{' ' * pad}{_dim(path)}")
    if desc:
        print(f"  {_dim(desc)}")


def _section(title: str) -> None:
    print()
    print(f"  {_bold(title)} {_dim('─' * max(0, _rule_width() - len(title) - 1))}")


def _row(label: str, value: str = "", note: str = "") -> None:
    """One profile row. ``value`` is the bare figure — no units, no punctuation —
    so a section's numbers line up in a single column; anything qualifying it goes
    in ``note``, already styled by the caller (dim for asides, plain for names and
    paths). Rows with nothing to count leave the figure column empty."""
    line = f"    {_dim(label.ljust(_LABEL))}{value.rjust(_VALUE)}"
    if note:
        line = f"{line}    {note}"
    print(line.rstrip())


def _render_languages(langs: list[dict]) -> None:
    _section("LANGUAGES")
    if len(langs) == 1:  # a lone language makes a bar and a 100% share redundant
        _row(_clip(langs[0]["language"], _LABEL), f"{langs[0]['loc']:,}", _dim("loc"))
        return
    top = max(s["loc"] for s in langs)
    total = sum(s["loc"] for s in langs) or 1
    for s in langs:
        bar = _accent(_bar(s["loc"] / top, 14)) if top else _bar(0, 14)
        pct = _dim(f"{round(s['loc'] / total * 100):>3}%")
        _row(_clip(s["language"], _LABEL), f"{s['loc']:,}", f"{bar} {pct}")


def _render_assets(assets: dict) -> None:
    _section("ASSETS")
    for bucket in ("weights", "configs", "data"):
        entry = assets.get(bucket)
        if not entry:
            continue
        exts = " ".join(f"{ext}×{n}" for ext, n in
                        sorted(entry["exts"].items(), key=lambda kv: (-kv[1], kv[0])))
        parts = [p for p in ("files", _human_bytes(entry["bytes"]) if entry["bytes"] else "", exts) if p]
        _row(bucket, f"{entry['files']:,}", _dim(" · ".join(parts)))


def _render_models(models: list[dict]) -> None:
    _section("MODELS")
    for m in models:
        fmts = _dim(" · " + " ".join(m["formats"])) if m["formats"] else ""
        _row(_clip(m["name"], _LABEL), "", f"{_clip(m['arch'] or '?', 30)}{fmts}")


def _run_profile(args: argparse.Namespace) -> int:
    global _COLOR
    _COLOR = _supports_color()
    if _COLOR:
        _enable_windows_ansi()

    repo = Path(args.repo_path).resolve()
    profile = core.build_project_profile(repo)
    config = _read_config()
    languages = core.language_stats(repo)
    code = core.code_stats(repo)
    assets = core.asset_inventory(repo)
    stats = core.git_stats(repo)
    gh = core.github_repo_info(repo, token=core._env_get(config, "GITHUB_TOKEN"))

    print()
    _header(repo.name or str(repo), str(repo), gh.get("description") if gh else None)

    _section("CODE")
    _row("Lines of code", f"{code['core_loc']:,}", _dim("excludes blanks and comments"))
    _row("Source files", f"{code['core_files']:,}",
         _dim(f"of {profile.total_files:,} files · {profile.max_depth} levels deep"))
    if code["avg_file_loc"]:
        _row("Avg per file", f"{code['avg_file_loc']:,}", _dim("lines"))
    if code["test_files"]:
        n = code["test_files"]
        _row("Test lines", f"{code['test_loc']:,}",
             _dim(f"{code['test_ratio']:.2f}× of source · {n:,} file{'s' if n != 1 else ''}"))
    if profile.package_files:
        _row("Structure", "", ", ".join(profile.package_files))
    if profile.entry_points:
        _row("Entry", "", ", ".join(profile.entry_points))

    if languages:
        _render_languages(languages)

    # Surface assets only when they carry weight — model or data artifacts. A
    # couple of config yamls in a plain software repo are noise, not signal.
    if assets.keys() & {"weights", "data"}:
        _render_assets(assets)
    # Name the model families a source-line count can't see. When this lists
    # models, reach for `--mode ml` to document them one page each.
    if profile.models:
        _render_models(profile.models)

    if stats or gh:
        _section("REPOSITORY")
    if stats:
        _row("Commits", f"{stats['commits']:,}",
             _dim(f"{stats['recent_commits_30d']:,} in the last 30 days"))
        if stats["contributors"]:
            _row("Contributors", f"{stats['contributors']:,}")
        if stats["first_commit"] and stats["last_commit"]:
            _row("History", "", _dim(f"{stats['first_commit']} → {stats['last_commit']}"))
        version = [v for v in (stats["current_version"], "dirty" if stats["dirty"] else "") if v]
        if version:
            _row("Version", "", " · ".join(version))

    if gh:
        flags = [f for f, on in (("archived", gh.get("archived")), ("fork", gh.get("is_fork"))) if on]
        named = [gh["full_name"], *([gh["license"]] if gh.get("license") else []), *flags]
        _row("GitHub", "", " · ".join(named))
        stars = gh.get("stars")
        counts = [f"{gh['forks']:,} forks" if gh.get("forks") else "",
                  f"{gh['open_issues']:,} open issues" if gh.get("open_issues") else ""]
        _row("Stars", f"{stars:,}" if stars is not None else "n/a",
             _dim(" · ".join(c for c in counts if c) or gh.get("error") or ""))
        if gh.get("pushed_at"):
            _row("Pushed", "", _dim(gh["pushed_at"][:10]))

    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.command == "version":
        print(f"yread {__version__}")
        return 0
    if args.command == "config":
        return _run_config(args)
    if args.command == "browse":
        return _run_browse(args)
    if args.command == "profile":
        return _run_profile(args)
    if args.command == "generate":
        config = core.config_from_args(args, config_files=[CONFIG_FILE])
        core.run_generate(args, config)
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

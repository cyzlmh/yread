"""Command line entry point for yread."""

from __future__ import annotations

import argparse
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
    viewer.main(viewer_args)
    return 0


def _human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _run_profile(args: argparse.Namespace) -> int:
    repo = Path(args.repo_path).resolve()
    profile = core.build_project_profile(repo)
    config = _read_config()
    languages = core.language_stats(repo)
    code = core.code_stats(repo)

    def row(label: str, value: str) -> None:
        print(f"{label:<11}{value}")

    row("Project", str(repo))
    row("Files", f"{code['core_files']} source · {profile.total_files} total · depth {profile.max_depth}")

    code_line = f"{code['core_loc']:,} loc"
    if code["core_files"]:
        code_line += f" · avg {code['avg_file_loc']}/file"
    if code["test_files"]:
        code_line += f" · tests {code['test_loc']:,} loc ({code['test_ratio']:.2f}x)"
    row("Code", code_line)

    structure = list(profile.package_files)
    if profile.entry_points:
        structure.append("entry " + ", ".join(profile.entry_points))
    if structure:
        row("Structure", " · ".join(structure))

    if languages:
        print()
        print("Languages")
        for s in languages:
            plural = "file" if s["files"] == 1 else "files"
            print(f"  {s['language']:<14}{s['loc']:>9,}  {s['files']:>3} {plural}")

    assets = core.asset_inventory(repo)
    # Surface assets only when they carry weight — model or data artifacts. A
    # couple of config yamls in a plain software repo are noise, not signal.
    if assets.keys() & {"weights", "data"}:
        print()
        print("Assets")
        for bucket in ("weights", "configs", "data"):
            entry = assets.get(bucket)
            if not entry:
                continue
            plural = "file" if entry["files"] == 1 else "files"
            exts = " ".join(f"{ext}×{n}" for ext, n in
                            sorted(entry["exts"].items(), key=lambda kv: (-kv[1], kv[0])))
            size = f" · {_human_bytes(entry['bytes'])}" if entry["bytes"] else ""
            print(f"  {bucket:<9}{entry['files']:>4} {plural}{size}   {exts}")

    # Name the model families a source-line count can't see. When this lists
    # models, reach for `--mode ml` to document them one page each.
    if profile.models:
        print()
        print("Models")
        for m in profile.models:
            arch = m["arch"] or "?"
            fmts = " ".join(m["formats"]) if m["formats"] else ""
            name = m["name"] if len(m["name"]) <= 24 else m["name"][:23] + "…"
            print(f"  {name:<26}{arch:<40} {fmts}".rstrip())

    stats = core.git_stats(repo)
    gh = core.github_repo_info(repo, token=core._env_get(config, "GITHUB_TOKEN"))
    if stats or gh:
        print()
    if stats:
        parts = [f"{stats['commits']} commits"]
        if stats["contributors"]:
            parts.append(f"{stats['contributors']} contributors")
        if stats["first_commit"] and stats["last_commit"]:
            parts.append(f"{stats['first_commit']}→{stats['last_commit']}")
        parts.append(f"{stats['recent_commits_30d']} in 30d")
        if stats["current_version"]:
            parts.append(stats["current_version"])
        if stats["dirty"]:
            parts.append("dirty")
        row("Git", " · ".join(parts))

    if gh:
        parts = [gh["full_name"]]
        stars = gh.get("stars")
        if stars is not None:
            parts.append(f"{stars}★")
        elif gh.get("error"):
            parts.append(gh["error"])
        if gh.get("forks"):
            parts.append(f"{gh['forks']} forks")
        if gh.get("open_issues"):
            parts.append(f"{gh['open_issues']} issues")
        if gh.get("license"):
            parts.append(gh["license"])
        for flag, on in (("archived", gh.get("archived")), ("fork", gh.get("is_fork"))):
            if on:
                parts.append(flag)
        if gh.get("pushed_at"):
            parts.append(f"pushed {gh['pushed_at'][:10]}")
        row("GitHub", " · ".join(parts))
        if gh.get("description"):
            row("", gh["description"])
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

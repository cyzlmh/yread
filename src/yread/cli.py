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
    "DOC_DEPTH",
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
    ("DOC_DEPTH", "Documentation depth [auto/brief/standard/deep]", "auto"),
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


def _run_profile(args: argparse.Namespace) -> int:
    repo = Path(args.repo_path).resolve()
    profile = core.build_project_profile(repo)
    config = _read_config()
    requested = (core._env_get(config, "DOC_DEPTH", "auto") or "auto").strip().lower()
    resolved = core.resolve_doc_depth(profile, requested)
    languages = core.language_stats(repo)
    code = core.code_stats(repo)
    signals = [name for name, on in (("readme", profile.has_readme),
                                     ("tests", profile.has_tests),
                                     ("ci", profile.has_ci)) if on]
    print(f"Project: {repo}")
    print(f"total_files:       {profile.total_files}")
    print(f"source_files:      {profile.source_files}")
    print(f"total_loc:         {code['total_loc']}   (code {code['code_loc']}, blank {code['blank_loc']})")
    print(f"primary_languages: {', '.join(profile.primary_languages) or '-'}")
    print(f"max_depth:         {profile.max_depth}")
    print(f"package_files:     {', '.join(profile.package_files) or '-'}")
    print(f"entry_points:      {', '.join(profile.entry_points) or '-'}")
    print(f"signals:           {', '.join(signals) or '-'}")
    if requested == resolved:
        print(f"doc_depth:         {resolved}")
    else:
        print(f"doc_depth:         {requested} -> {resolved}")
    print()
    print("code:")
    print(f"  avg_file:          {code['avg_file_loc']} loc")
    if code["test_files"]:
        print(f"  tests:             {code['test_files']} files, {code['test_loc']} loc"
              f"  ({code['test_ratio']:.2f}x source)")
    else:
        print("  tests:             none")
    if code["largest"]:
        print("  largest:")
        for f in code["largest"][:3]:
            print(f"    {f['path']:<30} {f['loc']:>6} loc")
    if languages:
        print()
        print("languages:")
        for s in languages:
            print(f"  {s['language']:<14} {s['files']:>3} files  {s['loc']:>6} loc")
    stats = core.git_stats(repo)
    if stats:
        print()
        print("git:")
        print(f"  commits:           {stats['commits']}")
        if stats["first_commit"] and stats["last_commit"]:
            print(f"  history:           {stats['first_commit']} -> {stats['last_commit']}")
        print(f"  recent_30d:        {stats['recent_commits_30d']} commits")
        print(f"  contributors:      {stats['contributors']}")
        print(f"  current_version:   {stats['current_version'] or '-'}")
        print(f"  dirty:             {str(stats['dirty']).lower()}")
    gh = core.github_repo_info(repo, token=core._env_get(config, "GITHUB_TOKEN"))
    if gh:
        stars = gh.get("stars")
        star_str = str(stars) if stars is not None else "n/a"
        if gh.get("error"):
            star_str += f" ({gh['error']})"
        print()
        print(f"github: {gh['full_name']}  stars: {star_str}")
        if gh.get("description"):
            print(f"  description:       {gh['description']}")
        forks, watchers, issues = gh.get("forks"), gh.get("watchers"), gh.get("open_issues")
        if any(v is not None for v in (forks, watchers, issues)):
            print(f"  forks: {forks if forks is not None else '-'}   "
                  f"watchers: {watchers if watchers is not None else '-'}   "
                  f"open_issues: {issues if issues is not None else '-'}")
        if gh.get("topics"):
            print(f"  topics:            {', '.join(gh['topics'])}")
        if gh.get("license"):
            print(f"  license:           {gh['license']}")
        if gh.get("homepage"):
            print(f"  homepage:          {gh['homepage']}")
        if gh.get("default_branch"):
            print(f"  default_branch:    {gh['default_branch']}")
        if gh.get("pushed_at"):
            print(f"  last_push:         {gh['pushed_at'][:10]}")
        flags = [f for f, on in (("archived", gh.get("archived")), ("fork", gh.get("is_fork"))) if on]
        if flags:
            print(f"  flags:             {', '.join(flags)}")
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

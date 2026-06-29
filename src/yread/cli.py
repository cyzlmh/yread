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
    "MAX_STEPS",
    "MAX_TOPICS",
    "CONCURRENCY",
    "ENABLE_SHELL",
    "OUTPUT_DIR",
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
    browse.add_argument("wiki_dir", nargs="?", help="Wiki root or version directory")
    browse.add_argument("--host", default="localhost", help="Host to bind")
    browse.add_argument("--port", type=int, default=8000)
    browse.add_argument("--repo", default=None, help="Source repository for source links")

    sub.add_parser("version", help="Print the version number")

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
    ("OUTPUT_DIR", "Output directory (blank = <repo>/.yread/wiki)", ""),
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
    if args.command == "generate":
        config = core.config_from_args(args, config_files=[CONFIG_FILE])
        core.run_generate(args, config)
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import json
import subprocess
from pathlib import Path

import pytest

from yread import core as yread
from yread import cli
from yread import viewer


def _init_git_repo(path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "dev@example.com")
    git("config", "user.name", "Dev Example")


def _git_commit_all(path: Path, message: str) -> None:
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", message], check=True, capture_output=True)


CONFIG_ENV_KEYS = {
    "PROVIDER", "BASE_URL", "API_KEY", "MODEL", "DOC_LANG",
    "DEPTH", "MODE", "MAX_STEPS", "MAX_TOPICS", "CONCURRENCY",
    "ENABLE_SHELL", "OUTPUT_DIR",
}


def clear_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in CONFIG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)          # bare (legacy / GITHUB_TOKEN fallback)
        monkeypatch.delenv(f"YREAD_{key}", raising=False)  # namespaced override


def write_page(root: Path, page: dict, text: str) -> None:
    target = root / page["file"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def test_env_file_config_for_openai_compatible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_config_env(monkeypatch)
    env_file = tmp_path / ".env.yread"
    env_file.write_text(
        "\n".join([
            "PROVIDER=openai-compatible",
            "BASE_URL=https://llm.example/v1",
            "API_KEY=test-key",
            "MODEL=test-model",
            "DOC_LANG=English",
            "DEPTH=deep",
            "CONCURRENCY=3",
            "ENABLE_SHELL=0",
        ])
    )

    args = yread.build_arg_parser().parse_args([".", "--env-file", str(env_file)])
    config = yread.config_from_args(args)
    settings = yread.resolve_provider(config)

    assert config.doc_lang == "English"
    assert config.depth == "deep"
    assert config.concurrency == 3
    assert config.enable_shell is False
    assert settings.base_url == "https://llm.example/v1"
    assert settings.api_key == "test-key"
    assert settings.model == "test-model"


def test_default_doc_language_is_en(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_config_env(monkeypatch)

    args = yread.build_arg_parser().parse_args(["."])
    config = yread.config_from_args(args)

    assert config.doc_lang == "en"
    assert config.depth == "brief"
    assert config.mode == "software"
    assert yread.lang_name(config.doc_lang) == "English"


def test_invalid_depth_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_config_env(monkeypatch)
    monkeypatch.setenv("YREAD_DEPTH", "full")
    args = yread.build_arg_parser().parse_args(["."])

    with pytest.raises(SystemExit, match="DEPTH must be one of"):
        yread.config_from_args(args)


def test_env_override_requires_yread_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_config_env(monkeypatch)
    config_file = tmp_path / "config.env"
    config_file.write_text("MODE=ml\n")  # config file uses the bare key
    # A bare MODE in the environment must NOT clash with yread's config...
    monkeypatch.setenv("MODE", "software")
    args = yread.build_arg_parser().parse_args(["."])
    assert yread.config_from_args(args, config_files=[config_file]).mode == "ml"
    # ...only the namespaced YREAD_MODE overrides.
    monkeypatch.setenv("YREAD_MODE", "software")
    assert yread.config_from_args(args, config_files=[config_file]).mode == "software"


def test_config_file_drives_output_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_config_env(monkeypatch)
    config_file = tmp_path / "config.env"
    configured_output = tmp_path / "obsidian" / "Yread"
    config_file.write_text(
        "\n".join([
            "PROVIDER=openai-compatible",
            "BASE_URL=https://llm.example/v1",
            "API_KEY=test-key",
            "MODEL=test-model",
            "DOC_LANG=zh",
            f"OUTPUT_DIR={configured_output}",
        ])
    )

    args = yread.build_arg_parser().parse_args(["."])
    config = yread.config_from_args(args, config_files=[config_file])

    assert config.doc_lang == "zh"
    assert yread.lang_name(config.doc_lang) == "Chinese"
    assert config.output_dir == configured_output


def test_explicit_missing_env_file_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_config_env(monkeypatch)
    args = yread.build_arg_parser().parse_args([".", "--env-file", str(tmp_path / "missing.env")])

    with pytest.raises(SystemExit, match="env file not found"):
        yread.config_from_args(args)


def test_cli_config_set_show_and_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / ".yread")
    monkeypatch.setattr(cli, "CONFIG_FILE", tmp_path / ".yread" / "config.env")

    assert cli.main(["config", "set", "DOC_LANG", "English"]) == 0
    assert "config.env" in capsys.readouterr().out

    assert cli.main(["config", "show"]) == 0
    assert "DOC_LANG=English" in capsys.readouterr().out

    assert cli.main(["config", "unset", "DOC_LANG"]) == 0
    capsys.readouterr()
    assert cli.main(["config", "show"]) == 0
    assert "DOC_LANG=English" not in capsys.readouterr().out


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    from yread import __version__

    assert cli.main(["version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_cli_config_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                         capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / ".yread")
    monkeypatch.setattr(cli, "CONFIG_FILE", tmp_path / ".yread" / "config.env")
    answers = iter(["deepseek", "https://api.deepseek.com/v1", "sk-test", "deepseek-v4-pro",
                    "zh", "standard", "ml", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert cli.main(["config", "init"]) == 0
    capsys.readouterr()
    assert cli.main(["config", "show"]) == 0
    out = capsys.readouterr().out
    assert "PROVIDER=deepseek" in out
    assert "DOC_LANG=zh" in out
    assert "DEPTH=standard" in out
    assert "MODE=ml" in out
    assert "OUTPUT_DIR" not in out  # left blank -> not written


def test_wiki_is_incomplete(tmp_path: Path) -> None:
    pages = [
        {"slug": "a", "file": "wiki/a.md", "title": "A"},
        {"slug": "b", "file": "wiki/b.md", "title": "B"},
    ]
    write_page(tmp_path, pages[0], "# A\n\nbody\n")
    assert yread.wiki_is_incomplete(tmp_path, pages) is True  # b.md missing

    write_page(tmp_path, pages[1], "# B\n\n> This page failed to generate: x\n")
    assert yread.wiki_is_incomplete(tmp_path, pages) is True  # b.md is a failure stub

    write_page(tmp_path, pages[1], "# B\n\nbody\n")
    assert yread.wiki_is_incomplete(tmp_path, pages) is False


def test_wiki_index_records_source_root(tmp_path: Path) -> None:
    import json
    from datetime import datetime, timezone

    output_root = tmp_path / ".yread"
    pages = [{"slug": "a", "file": "wiki/a.md", "title": "A", "section": "S",
              "kind": "overview", "level": "Beginner", "evidenceFiles": ["README.md"]}]
    profile = yread.ProjectProfile(
        total_files=1,
        source_files=0,
        primary_languages=[],
        max_depth=1,
        has_readme=True,
        has_tests=False,
        has_ci=False,
        package_files=[],
        entry_points=[],
    )
    yread.write_wiki_index(output_root, pages, "run1", datetime.now(timezone.utc),
                           "en", "brief", profile, {}, source_root=tmp_path / "repo")
    meta = json.loads((output_root / "wiki.json").read_text())
    assert meta["schema_version"] == 2
    assert meta["depth"] == "brief"
    assert meta["mode"] == "software"
    assert meta["project_profile"]["has_readme"] is True
    assert meta["source_root"] == str(tmp_path / "repo")
    assert meta["pages"][0]["file"] == "wiki/a.md"
    assert meta["pages"][0]["kind"] == "overview"
    assert meta["pages"][0]["evidenceFiles"] == ["README.md"]
    summary = (output_root / "SUMMARY.md").read_text()
    assert summary.count("(wiki/a.md)") == 1
    # SUMMARY carries the run meta (timestamp/mode/depth/lang) and the full profile
    # table, but no Models table when the profile lists no models.
    assert "Generated **" in summary and "`brief`" in summary and "`software`" in summary
    assert "**Profile**" in summary
    assert "| Files | 0 source · 1 total · max depth 1 |" in summary
    assert "**Models**" not in summary


def test_summary_profile_lines_includes_loc_and_git() -> None:
    profile = yread.ProjectProfile(
        total_files=10, source_files=5, primary_languages=["Python"], max_depth=3,
        has_readme=True, has_tests=True, has_ci=False, package_files=[], entry_points=[],
    )
    meta = {"generated_at": "2026-07-24T04:18:15Z", "id": "run1",
            "mode": "software", "depth": "brief", "language": "zh"}
    code = {"core_files": 5, "core_loc": 1234, "avg_file_loc": 78,
            "test_files": 2, "test_loc": 555, "test_ratio": 0.45}
    git = {"commits": 42, "contributors": 3, "first_commit": "2026-01-01",
           "last_commit": "2026-07-24", "recent_commits_30d": 5,
           "current_version": "v0.5.0", "dirty": True}
    lines = yread._summary_profile_lines(meta, profile, code=code, git=git)
    text = "\n".join(lines)
    assert "| Code | 1,234 loc · avg 78/file · tests 555 (0.45x) |" in text
    assert "| Git | 42 commits · 3 contributors · 2026-01-01→2026-07-24 · 5 in 30d · v0.5.0 · dirty |" in text


def test_summary_includes_model_inventory(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    output_root = tmp_path / ".yread"
    pages = [{"slug": "a", "file": "wiki/a.md", "title": "A", "section": "S",
              "kind": "overview", "level": "Beginner", "evidenceFiles": ["README.md"]}]
    profile = yread.ProjectProfile(
        total_files=3, source_files=1, primary_languages=["Python"], max_depth=2,
        has_readme=True, has_tests=False, has_ci=False, package_files=["pyproject.toml"],
        entry_points=["src/app/main.py"], config_files=1,
        models=[{"name": "qwen3-vl", "dir": "models/qwen3-vl",
                 "arch": "Qwen3VLForConditionalGeneration",
                 "formats": [".safetensors"], "config": "models/qwen3-vl/config.json"}],
    )
    yread.write_wiki_index(output_root, pages, "run1", datetime.now(timezone.utc),
                           "zh", "brief", profile, {}, source_root=tmp_path / "repo", mode="ml")
    summary = (output_root / "SUMMARY.md").read_text()
    assert "| Languages | Python |" in summary
    assert "| Packages | pyproject.toml |" in summary
    assert "| Entry points | src/app/main.py |" in summary
    assert "| Assets | 1 configs |" in summary
    assert "**Models**" in summary
    assert "| qwen3-vl | Qwen3VLForConditionalGeneration | .safetensors |" in summary


def test_cli_requires_explicit_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 0
    assert "generate" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        cli.main(["/tmp/repo"])


def test_readonly_bash_allows_simple_reads_and_rejects_unsafe_commands(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("hello\n")
    (tmp_path / ".env").write_text("SECRET=1\n")

    assert "hello" in yread.run_bash(tmp_path, "cat note.txt")
    assert "not allowed" in yread.run_bash(tmp_path, "rm note.txt")
    assert "metacharacters" in yread.run_bash(tmp_path, "cat note.txt | wc -l")
    assert "sensitive path" in yread.run_bash(tmp_path, "cat .env")
    assert "outside repository" in yread.run_bash(tmp_path, "cat ../secret.txt")
    assert "disabled" in yread.run_bash(tmp_path, "ls .", enabled=False)


def test_view_file_blocks_sensitive_paths(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=1\n")
    (tmp_path / "main.py").write_text("print('ok')\n")

    assert "sensitive file" in yread.view_file_in_detail(tmp_path, ".env")
    assert "print('ok')" in yread.view_file_in_detail(tmp_path, "main.py")


def test_parse_catalog_and_slugify() -> None:
    raw = """
<section>
Get Started
<topic kind="overview" level="Beginner" files="README.md, src/yread/core.py">
项目概览
</topic>
<group>
Internals
<topic kind="architecture" level="Advanced" files="src/yread/core.py">
Agent 循环
</topic>
</group>
</section>
"""
    pages = yread.assign_page_fields(yread.parse_catalog(raw))

    assert [p["title"] for p in pages] == ["项目概览", "Agent 循环"]
    assert pages[0]["slug"] == "1-项目概览"
    assert pages[1]["slug"] == "2-Agent-循环"
    assert pages[0]["kind"] == "overview"
    assert pages[0]["evidenceFiles"] == ["README.md", "src/yread/core.py"]
    assert pages[1]["group"] == "Internals"


def test_parse_catalog_requires_kind() -> None:
    raw = """
<section>
Get Started
<topic level="Beginner" files="README.md">
Overview
</topic>
</section>
"""

    with pytest.raises(ValueError, match="invalid or missing kind"):
        yread.parse_catalog(raw)


def test_clean_catalog_pages_filters_invalid_evidence(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n")
    raw = """
<section>
Start
<topic kind="overview" level="Beginner" files="README.md, /tmp/secret.txt, missing.py">
Overview
</topic>
<topic kind="overview" level="Beginner" files="README.md">
Overview
</topic>
</section>
"""

    pages = yread.clean_catalog_pages(tmp_path, yread.parse_catalog(raw), topic_budget=10)

    assert len(pages) == 1
    assert pages[0]["evidenceFiles"] == ["README.md"]


def test_plan_pages_skips_existing_but_page_selector_forces_regeneration(tmp_path: Path) -> None:
    pages = yread.assign_page_fields([
        {"section": "Get Started", "group": "", "title": "Overview", "level": "Beginner"},
        {"section": "Deep Dive", "group": "", "title": "Runtime", "level": "Advanced"},
    ])
    write_page(tmp_path, pages[0], "# Overview\n")

    todo, skipped = yread.plan_pages(tmp_path, pages, selector=None, force=False)
    assert [p["slug"] for p in todo] == [pages[1]["slug"]]
    assert [p["slug"] for p in skipped] == [pages[0]["slug"]]

    todo, skipped = yread.plan_pages(tmp_path, pages, selector=pages[0]["slug"], force=False)
    assert [p["slug"] for p in todo] == [pages[0]["slug"]]
    assert skipped == []


def test_plan_pages_regenerates_pages_with_changed_evidence_files(tmp_path: Path) -> None:
    pages = yread.assign_page_fields([
        {
            "section": "Get Started",
            "group": "",
            "title": "Overview",
            "level": "Beginner",
            "evidenceFiles": ["README.md"],
        },
        {
            "section": "Deep Dive",
            "group": "",
            "title": "Runtime",
            "level": "Advanced",
            "evidenceFiles": ["src/"],
        },
    ])
    for page in pages:
        write_page(tmp_path, page, f"# {page['title']}\n")

    diff = {"added": [], "modified": ["src/main.py"], "removed": []}
    todo, skipped = yread.plan_pages(tmp_path, pages, selector=None, force=False, manifest_diff=diff)

    assert [p["slug"] for p in todo] == [pages[1]["slug"]]
    assert [p["slug"] for p in skipped] == [pages[0]["slug"]]


def test_file_manifest_detects_added_modified_and_removed_files(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("old\n")
    (tmp_path / "old.py").write_text("print('old')\n")
    (tmp_path / ".env").write_text("SECRET=1\n")
    before = yread.build_file_manifest(tmp_path)

    (tmp_path / "README.md").write_text("new\n")
    (tmp_path / "old.py").unlink()
    (tmp_path / "main.py").write_text("print('ok')\n")
    after = yread.build_file_manifest(tmp_path)
    diff = yread.diff_manifests(before, after)

    assert diff["added"] == ["main.py"]
    assert diff["modified"] == ["README.md"]
    assert diff["removed"] == ["old.py"]
    assert ".env" not in {f["path"] for f in after["files"]}


def test_project_profile_detects_repo_shape(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n")
    (tmp_path / "pyproject.toml").write_text('[project.scripts]\ndemo = "demo.cli:main"\n')
    (tmp_path / "src" / "demo").mkdir(parents=True)
    (tmp_path / "src" / "demo" / "cli.py").write_text("def main(): pass\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_cli.py").write_text("def test_ok(): pass\n")

    profile = yread.build_project_profile(tmp_path)

    assert profile.has_readme is True
    assert profile.has_tests is True
    assert profile.primary_languages == ["Python"]
    assert "pyproject.toml" in profile.package_files
    assert "src/demo/cli.py" in profile.entry_points


def test_cli_profile_prints_profile_and_resolved_depth(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    clear_config_env(monkeypatch)
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / ".yread")
    monkeypatch.setattr(cli, "CONFIG_FILE", tmp_path / ".yread" / "config.env")
    (tmp_path / "README.md").write_text("# Demo\n")
    (tmp_path / "pyproject.toml").write_text('[project.scripts]\ndemo = "demo.cli:main"\n')
    (tmp_path / "src" / "demo").mkdir(parents=True)
    (tmp_path / "src" / "demo" / "cli.py").write_text("def main(): pass\n")

    assert cli.main(["profile", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert f"Project    {tmp_path}" in out
    assert "Languages" in out
    assert "Python" in out and "1 file" in out  # per-language table row
    assert "pyproject.toml" in out
    assert "entry src/demo/cli.py" in out
    assert "Code       1 loc" in out
    assert "avg" in out
    assert "primary_languages" not in out  # redundant with the Languages line
    assert "signals" not in out            # low-signal fields dropped
    assert "max_depth" not in out          # humanized as "depth N", raw field not dumped
    assert "GitHub" not in out  # tmp_path is not a git repo -> no network


def test_language_stats_counts_files_and_lines(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('a')\nprint('b')\n")      # 2 lines
    (tmp_path / "b.py").write_text("x = 1\n")                        # 1 line
    (tmp_path / "c.ts").write_text("const a = 1;\n\nconst b = 2;\n")  # 3 lines
    (tmp_path / "README.md").write_text("# hi\n")                   # not source

    stats = yread.language_stats(tmp_path)

    langs = {s["language"]: s for s in stats}
    assert langs["Python"]["files"] == 2
    assert langs["Python"]["loc"] == 3
    assert langs["TypeScript"]["files"] == 1
    assert langs["TypeScript"]["loc"] == 2  # blank line excluded
    # sorted by code loc descending (Python 3 before TypeScript 2)
    assert [s["language"] for s in stats] == ["Python", "TypeScript"]


def test_code_stats_splits_code_blank_and_tests(tmp_path: Path) -> None:
    (tmp_path / "core.py").write_text("x = 1\n\n\ny = 2\n")            # 4 loc, 2 code, 2 blank
    (tmp_path / "small.py").write_text("z = 3\n")                     # 1 loc, 1 code
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_core.py").write_text("a = 1\nb = 2\n")  # 2 loc, test

    stats = yread.code_stats(tmp_path)

    assert stats["core_files"] == 2         # test file excluded
    assert stats["core_loc"] == 3           # 2 + 1 code lines
    assert stats["test_files"] == 1
    assert stats["test_loc"] == 2           # code lines in the test file
    assert stats["test_ratio"] == round(2 / 3, 2)  # test code over core code
    assert stats["avg_file_loc"] == round(3 / 2)   # core code lines / core files


def test_code_stats_excludes_comments_across_languages(tmp_path: Path) -> None:
    (tmp_path / "a.java").write_text(
        "/*\n"
        " * license header\n"
        " */\n"
        "package x;\n"
        "// a note\n"
        "int y = 1; // trailing comment still code\n"
    )  # 6 lines: 4 comment, 2 code
    (tmp_path / "b.py").write_text(
        '"""module\ndocstring"""\n'
        "import os  # inline\n"
    )  # 3 lines: 2 comment, 1 code

    stats = yread.code_stats(tmp_path)

    assert stats["core_loc"] == 3      # comments excluded: java 2 + py 1

    # line-level classification the aggregate relies on
    assert yread._line_stats("/*\n x\n */\ny=1; // t\n", ".java") == (4, 0, 3, 1)
    assert yread._line_stats('"""d\noc"""\nimport os  # c\n', ".py") == (3, 0, 2, 1)


def test_iter_source_files_skips_vendored_dirs(tmp_path: Path) -> None:
    (tmp_path / "app.swift").write_text("let x = 1\n")
    (tmp_path / "Pods" / "Lib").mkdir(parents=True)
    (tmp_path / "Pods" / "Lib" / "dep.swift").write_text("let y = 2\n")
    (tmp_path / "src" / "3rdparty").mkdir(parents=True)
    (tmp_path / "src" / "3rdparty" / "vendored.c").write_text("int z;\n")

    rels = {p.relative_to(tmp_path).as_posix() for p in yread.iter_source_files(tmp_path)}

    assert rels == {"app.swift"}


def test_gitignore_nested_path_does_not_ignore_parent(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(
        "apps/hanzi-picturebook/studio/archive/\n"
        "logs/\n"
        "!.env.example\n"
    )
    (tmp_path / "apps" / "portal" / "public").mkdir(parents=True)
    (tmp_path / "apps" / "portal" / "public" / "app.js").write_text("console.log('hi')\n")
    (tmp_path / "apps" / "hanzi-picturebook" / "studio" / "archive").mkdir(parents=True)
    (tmp_path / "apps" / "hanzi-picturebook" / "studio" / "archive" / "old.js").write_text("old\n")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "debug.log").write_text("debug\n")

    rels = {p.relative_to(tmp_path).as_posix() for p in yread.iter_source_files(tmp_path)}

    # Nested gitignore paths must not cause the parent directory to disappear.
    assert "apps/portal/public/app.js" in rels
    # Simple name patterns should still be ignored at any level.
    assert "logs/debug.log" not in rels


def test_gitignore_names_parses_simple_patterns_only(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(
        "node_modules/\n"
        "/dist/\n"
        "apps/foo/bar/\n"
        "!.env.example\n"
        "*.log\n"
        "# comment\n"
    )
    names = yread._gitignore_names(tmp_path)

    # Only simple name patterns are promoted to global ignore names.
    assert names == {"node_modules", "dist"}



def test_parse_github_remote_handles_ssh_https_and_non_github() -> None:
    assert yread.parse_github_remote("git@github.com:owner/repo.git") == ("owner", "repo")
    assert yread.parse_github_remote("https://github.com/owner/repo.git") == ("owner", "repo")
    assert yread.parse_github_remote("https://github.com/owner/repo") == ("owner", "repo")
    assert yread.parse_github_remote("https://gitlab.com/owner/repo.git") is None
    assert yread.parse_github_remote("not a url") is None


def test_git_stats_returns_none_outside_git_repo(tmp_path: Path) -> None:
    assert yread.git_stats(tmp_path) is None


def test_git_stats_reports_commits_tag_and_dirty(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    _init_git_repo(tmp_path)
    _git_commit_all(tmp_path, "init")
    subprocess.run(["git", "-C", str(tmp_path), "tag", "v0.1.0"], check=True, capture_output=True)

    stats = yread.git_stats(tmp_path)
    assert stats is not None
    assert stats["commits"] == 1
    assert stats["contributors"] == 1
    assert stats["current_version"] == "v0.1.0"
    assert stats["first_commit"] == stats["last_commit"]
    assert stats["recent_commits_30d"] == 1
    assert stats["dirty"] is False

    (tmp_path / "b.py").write_text("y = 2\n")  # uncommitted change
    assert yread.git_stats(tmp_path)["dirty"] is True


class _FakeResp:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._payload


def test_github_repo_info_parses_full_response(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yread, "_git_remote_origin", lambda _repo: "git@github.com:owner/repo.git")
    payload = json.dumps({
        "full_name": "owner/repo",
        "description": "A demo",
        "homepage": "https://example.com",
        "stargazers_count": 1234,
        "forks_count": 56,
        "subscribers_count": 12,
        "open_issues_count": 3,
        "topics": ["cli", "wiki"],
        "license": {"spdx_id": "MIT"},
        "default_branch": "main",
        "pushed_at": "2024-06-01T00:00:00Z",
        "archived": False,
        "fork": True,
    }).encode()
    monkeypatch.setattr(yread.urllib.request, "urlopen", lambda _req, timeout=0: _FakeResp(payload))

    info = yread.github_repo_info(tmp_path)
    assert info["stars"] == 1234
    assert info["forks"] == 56
    assert info["watchers"] == 12
    assert info["open_issues"] == 3
    assert info["topics"] == ["cli", "wiki"]
    assert info["license"] == "MIT"
    assert info["homepage"] == "https://example.com"
    assert info["default_branch"] == "main"
    assert info["is_fork"] is True
    assert info["error"] is None


def test_github_repo_info_uses_github_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yread, "_git_remote_origin", lambda _repo: "https://github.com/o/r")
    monkeypatch.setenv("GITHUB_TOKEN", "sekret")
    captured: dict[str, str | None] = {}

    def fake_urlopen(req: object, timeout: float = 0) -> _FakeResp:
        captured["auth"] = req.get_header("Authorization")  # type: ignore[attr-defined]
        return _FakeResp(b"{}")

    monkeypatch.setattr(yread.urllib.request, "urlopen", fake_urlopen)
    yread.github_repo_info(tmp_path)
    assert captured["auth"] == "Bearer sekret"


def test_github_repo_info_reports_http_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yread, "_git_remote_origin", lambda _repo: "https://github.com/o/r")

    def raise_http(_req: object, timeout: float = 0) -> None:
        raise yread.urllib.error.HTTPError("url", 403, "rate limited", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(yread.urllib.request, "urlopen", raise_http)
    info = yread.github_repo_info(tmp_path)
    assert info["stars"] is None
    assert info["error"] == "HTTP 403"


def test_cli_profile_shows_git_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    clear_config_env(monkeypatch)
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / ".yread")
    monkeypatch.setattr(cli, "CONFIG_FILE", tmp_path / ".yread" / "config.env")
    (tmp_path / "main.py").write_text("print('hi')\n")
    _init_git_repo(tmp_path)
    _git_commit_all(tmp_path, "init")

    assert cli.main(["profile", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Git        1 commits" in out
    assert "1 contributors" in out
    assert "GitHub" not in out  # no origin remote -> no network


def test_topic_budget_for_depth_respects_max_topics() -> None:
    assert yread.topic_budget_for_depth("brief", 30) == 5
    assert yread.topic_budget_for_depth("standard", 30) == 15
    assert yread.topic_budget_for_depth("deep", 12) == 12


def test_generate_writes_flat_output_root_and_overwrites_previous_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Demo\n")
    settings = yread.LLMSettings("test", "https://llm.example/v1", "key", "model")
    config = yread.RuntimeConfig(
        provider="openai-compatible",
        base_url=settings.base_url,
        api_key=settings.api_key,
        model=settings.model,
        doc_lang="en",
        depth="brief",
        max_steps=1,
        max_topics=3,
        concurrency=1,
        enable_shell=False,
    )
    run = {"count": 0}

    def fake_build_catalog(*_args):
        run["count"] += 1
        raw_pages = [
            {
                "section": "Guide",
                "group": "",
                "title": "Overview",
                "kind": "overview",
                "level": "Beginner",
                "evidenceFiles": ["README.md"],
            },
        ]
        if run["count"] == 1:
            raw_pages.append({
                "section": "Guide",
                "group": "",
                "title": "Runtime",
                "kind": "runtime-flow",
                "level": "Intermediate",
                "evidenceFiles": ["README.md"],
            })
        return yread.assign_page_fields(raw_pages, repo)

    monkeypatch.setattr(yread, "resolve_provider", lambda _config: settings)
    monkeypatch.setattr(yread, "make_client", lambda _settings: object())
    monkeypatch.setattr(yread, "build_catalog", fake_build_catalog)
    monkeypatch.setattr(
        yread,
        "generate_page",
        lambda _client, _repo, _messages, slug, _settings, _config: f"# {slug}\n\nrun {run['count']}",
    )

    args = yread.build_arg_parser().parse_args([str(repo)])
    output_root = yread.run_generate(args, config)

    assert output_root == repo / ".yread"
    assert (output_root / "wiki.json").is_file()
    assert (output_root / "manifest.json").is_file()
    assert (output_root / "SUMMARY.md").is_file()
    assert (output_root / "wiki" / "1-Overview.md").read_text() == "# 1-Overview\n\nrun 1\n"
    assert (output_root / "wiki" / "2-Runtime.md").is_file()
    assert not (output_root / "current").exists()
    assert not (output_root / "versions").exists()

    yread.run_generate(args, config)

    assert (output_root / "wiki" / "1-Overview.md").read_text() == "# 1-Overview\n\nrun 2\n"
    assert not (output_root / "wiki" / "2-Runtime.md").exists()


def test_write_one_page_preserves_existing_page_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = yread.assign_page_fields([
        {"section": "Get Started", "group": "", "title": "Overview", "level": "Beginner"}
    ])[0]
    target = tmp_path / page["file"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Previous good page\n")

    def fail_generate_page(*_args, **_kwargs):
        raise RuntimeError("model returned invalid output")

    monkeypatch.setattr(yread, "generate_page", fail_generate_page)
    settings = yread.LLMSettings("test", "https://llm.example/v1", "key", "model")
    config = yread.RuntimeConfig(
        provider="openai-compatible",
        base_url=settings.base_url,
        api_key=settings.api_key,
        model=settings.model,
        doc_lang="English",
        depth="brief",
        max_steps=1,
        max_topics=2,
        concurrency=1,
        enable_shell=False,
    )

    _slug, ok, error = yread.write_one_page(settings, config, tmp_path, tmp_path, ".", [page], page)

    assert ok is False
    assert "model returned invalid output" in error
    assert target.read_text() == "# Previous good page\n"


# --------------------------------------------------------------------------- #
# ML/model project lens                                                        #
# --------------------------------------------------------------------------- #

def test_asset_inventory_buckets(tmp_path: Path) -> None:
    (tmp_path / "m.onnx").write_bytes(b"abc")
    (tmp_path / "train_cfg.yaml").write_text("lr: 0.1\n")
    (tmp_path / "sample.mp3").write_bytes(b"xy")
    (tmp_path / "core.py").write_text("x = 1\n")
    inv = yread.asset_inventory(tmp_path)
    assert inv["weights"]["files"] == 1 and inv["weights"]["exts"] == {".onnx": 1}
    assert inv["configs"]["files"] == 1
    assert inv["data"]["files"] == 1
    assert "core.py" not in str(inv)


# --------------------------------------------------------------------------- #
# Viewer — select-to-explain                                                   #
# --------------------------------------------------------------------------- #

def test_explain_assets_reflects_enabled_flag() -> None:
    on = viewer.explain_assets(True)
    off = viewer.explain_assets(False)
    assert "yr-ebtn" in on and "yr-ebub" in on
    assert "if(!true)" in on
    assert "if(!false)" in off
    # The page template accepts the scripts slot alongside its other placeholders.
    page = viewer.PAGE.format(title="T", nav="N", body="B", scripts=on)
    assert "yr-ebtn" in page and "<title>T · yread</title>" in page


def test_generate_explanation_renders_markdown() -> None:
    from types import SimpleNamespace

    captured: dict = {}

    def create(model, messages):
        captured["model"] = model
        captured["messages"] = messages
        msg = SimpleNamespace(content="**ViT** is a *Vision Transformer*.")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    html = viewer.generate_explanation(client, "m1", "zh", "ViT")
    assert "<strong>ViT</strong>" in html and "<em>Vision Transformer</em>" in html
    assert captured["model"] == "m1"
    assert "Chinese" in captured["messages"][0]["content"]  # lang code -> readable name
    assert captured["messages"][1]["content"] == "ViT"


def test_build_profile_populates_ml_fields(tmp_path: Path) -> None:
    (tmp_path / "model.pth").write_bytes(b"\x00")
    (tmp_path / "cfg.yaml").write_text("a: 1\n")
    (tmp_path / "data.csv").write_text("a,b\n1,2\n")
    (tmp_path / "main.py").write_text("print(1)\n")
    profile = yread.build_project_profile(tmp_path)
    assert profile.model_files == 1
    assert profile.config_files == 1
    assert profile.data_files == 1


def test_preset_for_ml_unlocks_kinds() -> None:
    allowed, catalog_guidance, page_guidance = yread.preset_for("ml")
    assert "model-architecture" in allowed and "training" in allowed
    assert "overview" in allowed  # base kinds still valid
    assert catalog_guidance and page_guidance
    # The ml lens must steer model-first, one page per model — not append optional kinds.
    assert "PER model family" in catalog_guidance
    assert "OVERRIDES" in catalog_guidance
    base_allowed, base_cat, base_page = yread.preset_for("software")
    assert "model-architecture" not in base_allowed
    assert base_cat == "" and base_page == ""


def test_classify_asset_recognizes_json_model_config(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text('{"architectures": ["ASTForAudioClassification"]}\n')
    assert yread.classify_asset(cfg) == "configs"
    assert yread.classify_asset(tmp_path / "fusion_result.json") is None  # not a model config
    assert yread.classify_asset(tmp_path / "m.onnx") == "weights"


def test_detect_model_families_groups_by_model_dir(tmp_path: Path) -> None:
    audio = tmp_path / "models" / "audio_model"
    (audio / "audio_classification").mkdir(parents=True)
    (audio / "ast_model.om").write_bytes(b"\x00")
    (audio / "ast_model.onnx").write_bytes(b"\x00")
    (audio / "audio_classification" / "config.json").write_text(
        '{"architectures": ["ASTForAudioClassification"], "hidden_size": 768}\n')
    text = tmp_path / "models" / "text_model"
    text.mkdir(parents=True)
    (text / "bert_model.om").write_bytes(b"\x00")
    # a lone non-model config.json must NOT register as a model family
    (tmp_path / "app_config").mkdir()
    (tmp_path / "app_config" / "config.json").write_text('{"port": 8000}\n')

    fams = {f["name"]: f for f in yread.detect_model_families(tmp_path)}
    assert set(fams) == {"audio_model", "text_model"}
    assert fams["audio_model"]["arch"] == "ASTForAudioClassification"
    # weights in the family dir group with the config in a sibling subfolder
    assert fams["audio_model"]["formats"] == [".om", ".onnx"]
    assert fams["audio_model"]["config"] == "models/audio_model/audio_classification/config.json"
    assert fams["text_model"]["formats"] == [".om"]


def test_detect_model_families_drops_training_scratch(tmp_path: Path) -> None:
    # Real base models, each with its own config.json + weights.
    for name, arch in [("Qwen3-VL-2B-Instruct", "Qwen3VLForConditionalGeneration"),
                       ("single_model_sonar", "XLMRobertaForSequenceClassification")]:
        d = tmp_path / "multimodal_model" / name if "Qwen" in name else tmp_path / name
        d.mkdir(parents=True)
        (d / "model.safetensors").write_bytes(b"\x00")
        (d / "config.json").write_text(f'{{"architectures": ["{arch}"]}}\n')
    # Training scratch that must NOT become its own model page:
    ckpt = tmp_path / "single_model_sonar" / "checkpoints" / "checkpoint-1054"
    ckpt.mkdir(parents=True)
    (ckpt / "pytorch_model.bin").write_bytes(b"\x00")
    lora = tmp_path / "multimodal_model" / "outputs_12_52_lora_ddp2"
    lora.mkdir(parents=True)
    (lora / "adapter_model.safetensors").write_bytes(b"\x00")

    names = [f["name"] for f in yread.detect_model_families(tmp_path)]
    assert names == ["Qwen3-VL-2B-Instruct", "single_model_sonar"]
    assert not any("checkpoint" in n or "lora" in n or "outputs" in n for n in names)


def test_build_profile_surfaces_model_families(tmp_path: Path) -> None:
    m = tmp_path / "models" / "vit"
    m.mkdir(parents=True)
    (m / "vit_model.onnx").write_bytes(b"\x00")
    (m / "config.json").write_text('{"model_type": "vit"}\n')
    profile = yread.build_project_profile(tmp_path)
    assert [f["name"] for f in profile.models] == ["vit"]
    assert profile.models[0]["arch"] == "vit"


def test_clean_catalog_pages_keeps_ml_kinds_when_allowed(tmp_path: Path) -> None:
    # Regression: clean_catalog_pages must honor the same allowed_kinds as the
    # parser, or every ml page (model-architecture, ...) is silently dropped and
    # only `overview` survives.
    (tmp_path / "model.py").write_text("x = 1\n")
    pages = [
        {"kind": "overview", "title": "Overview", "evidenceFiles": ["model.py"]},
        {"kind": "model-architecture", "title": "AST Model", "evidenceFiles": ["model.py"]},
        {"kind": "model-serving", "title": "Serving", "evidenceFiles": ["model.py"]},
    ]
    base = yread.clean_catalog_pages(tmp_path, pages, topic_budget=10)
    assert [p["kind"] for p in base] == ["overview"]  # ml kinds rejected by default
    ml = yread.clean_catalog_pages(tmp_path, pages, topic_budget=10,
                                   allowed_kinds=yread.TOPIC_KINDS | yread.ML_TOPIC_KINDS)
    assert [p["kind"] for p in ml] == ["overview", "model-architecture", "model-serving"]


def test_parse_catalog_ml_kind_requires_allowed_kinds() -> None:
    raw = """
<section>
Models
<topic kind="model-architecture" level="Intermediate" files="modeling.py">
Model Architecture
</topic>
</section>
"""
    with pytest.raises(ValueError, match="invalid or missing kind"):
        yread.parse_catalog(raw)  # base kinds reject the ml kind
    pages = yread.parse_catalog(raw, yread.TOPIC_KINDS | yread.ML_TOPIC_KINDS)
    assert pages[0]["kind"] == "model-architecture"


def test_view_file_binary_weight_is_not_dumped(tmp_path: Path) -> None:
    (tmp_path / "model.onnx").write_bytes(b"\x00\x01\x02\xff\xfe")
    out = yread.view_file_in_detail(tmp_path, "model.onnx")
    assert "binary model artifact" in out
    assert "not readable as text" in out


def test_config_from_args_reads_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_config_env(monkeypatch)
    env = tmp_path / ".env.yread"
    env.write_text("MODE=ml\n")
    parser = yread.build_arg_parser()
    args = parser.parse_args([str(tmp_path), "--env-file", str(env)])
    config = yread.config_from_args(args)
    assert config.mode == "ml"


def test_mode_cli_flag_overrides_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_config_env(monkeypatch)
    env = tmp_path / ".env.yread"
    env.write_text("MODE=ml\n")
    parser = yread.build_arg_parser()
    args = parser.parse_args([str(tmp_path), "--env-file", str(env), "--mode", "software"])
    config = yread.config_from_args(args)
    assert config.mode == "software"

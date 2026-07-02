from pathlib import Path

import pytest

from yread import core as yread
from yread import cli


CONFIG_ENV_KEYS = {
    "PROVIDER", "BASE_URL", "API_KEY", "MODEL", "DOC_LANG",
    "DOC_DEPTH", "MAX_STEPS", "MAX_TOPICS", "CONCURRENCY", "ENABLE_SHELL", "OUTPUT_DIR",
}


def clear_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in CONFIG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


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
            "DOC_DEPTH=deep",
            "CONCURRENCY=3",
            "ENABLE_SHELL=0",
        ])
    )

    args = yread.build_arg_parser().parse_args([".", "--env-file", str(env_file)])
    config = yread.config_from_args(args)
    settings = yread.resolve_provider(config)

    assert config.doc_lang == "English"
    assert config.doc_depth == "deep"
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
    assert config.doc_depth == "auto"
    assert yread.lang_name(config.doc_lang) == "English"


def test_invalid_doc_depth_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_config_env(monkeypatch)
    monkeypatch.setenv("DOC_DEPTH", "full")
    args = yread.build_arg_parser().parse_args(["."])

    with pytest.raises(SystemExit, match="DOC_DEPTH must be one of"):
        yread.config_from_args(args)


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
    answers = iter(["deepseek", "https://api.deepseek.com/v1", "sk-test", "deepseek-v4-pro", "zh", "standard", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert cli.main(["config", "init"]) == 0
    capsys.readouterr()
    assert cli.main(["config", "show"]) == 0
    out = capsys.readouterr().out
    assert "PROVIDER=deepseek" in out
    assert "DOC_LANG=zh" in out
    assert "DOC_DEPTH=standard" in out
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
    assert meta["doc_depth"] == "brief"
    assert meta["project_profile"]["has_readme"] is True
    assert meta["source_root"] == str(tmp_path / "repo")
    assert meta["pages"][0]["file"] == "wiki/a.md"
    assert meta["pages"][0]["kind"] == "overview"
    assert meta["pages"][0]["evidenceFiles"] == ["README.md"]
    assert (output_root / "SUMMARY.md").read_text().count("(wiki/a.md)") == 1


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
    assert yread.resolve_doc_depth(profile, "auto") == "brief"


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
        doc_depth="brief",
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
        doc_depth="brief",
        max_steps=1,
        max_topics=2,
        concurrency=1,
        enable_shell=False,
    )

    _slug, ok, error = yread.write_one_page(settings, config, tmp_path, tmp_path, ".", [page], page)

    assert ok is False
    assert "model returned invalid output" in error
    assert target.read_text() == "# Previous good page\n"

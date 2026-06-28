from pathlib import Path

import pytest

from yread import core as yread
from yread import cli


CONFIG_ENV_KEYS = {
    "PROVIDER", "BASE_URL", "API_KEY", "MODEL", "DOC_LANG",
    "MAX_STEPS", "MAX_TOPICS", "CONCURRENCY", "ENABLE_SHELL", "OUTPUT_DIR",
}


def clear_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in CONFIG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


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
            "CONCURRENCY=3",
            "ENABLE_SHELL=0",
        ])
    )

    args = yread.build_arg_parser().parse_args([".", "--env-file", str(env_file)])
    config = yread.config_from_args(args)
    settings = yread.resolve_provider(config)

    assert config.doc_lang == "English"
    assert config.concurrency == 3
    assert config.enable_shell is False
    assert settings.base_url == "https://llm.example/v1"
    assert settings.api_key == "test-key"
    assert settings.model == "test-model"


def test_default_doc_language_is_english(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_config_env(monkeypatch)

    args = yread.build_arg_parser().parse_args(["."])
    config = yread.config_from_args(args)

    assert config.doc_lang == "English"


def test_default_config_file_and_cli_output_dir_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_config_env(monkeypatch)
    config_file = tmp_path / "config.env"
    configured_output = tmp_path / "obsidian" / "Yread"
    cli_output = tmp_path / "override"
    config_file.write_text(
        "\n".join([
            "PROVIDER=openai-compatible",
            "BASE_URL=https://llm.example/v1",
            "API_KEY=test-key",
            "MODEL=test-model",
            "DOC_LANG=English",
            f"OUTPUT_DIR={configured_output}",
        ])
    )

    args = yread.build_arg_parser().parse_args([".", "--output-dir", str(cli_output)])
    config = yread.config_from_args(args, config_files=[config_file])

    assert config.doc_lang == "English"
    assert config.output_dir == cli_output

    args = yread.build_arg_parser().parse_args(["."])
    config = yread.config_from_args(args, config_files=[config_file])

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


def test_cli_accepts_repo_path_without_generate_subcommand() -> None:
    assert cli._normalize_argv(["/tmp/repo"]) == ["generate", "/tmp/repo"]
    assert cli._normalize_argv(["generate", "/tmp/repo"]) == ["generate", "/tmp/repo"]


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
<topic level="Beginner" files="README.md, src/yread/core.py">
项目概览
</topic>
<group>
Internals
<topic level="Advanced" files="src/yread/core.py">
Agent 循环
</topic>
</group>
</section>
"""
    pages = yread.assign_page_fields(yread.parse_catalog(raw))

    assert [p["title"] for p in pages] == ["项目概览", "Agent 循环"]
    assert pages[0]["slug"] == "1-xiang-mu-gai-lan"
    assert pages[0]["associatedFiles"] == ["README.md", "src/yread/core.py"]
    assert pages[1]["group"] == "Internals"


def test_plan_pages_skips_existing_but_page_selector_forces_regeneration(tmp_path: Path) -> None:
    pages = yread.assign_page_fields([
        {"section": "Get Started", "group": "", "title": "Overview", "level": "Beginner"},
        {"section": "Deep Dive", "group": "", "title": "Runtime", "level": "Advanced"},
    ])
    (tmp_path / pages[0]["file"]).write_text("# Overview\n")

    todo, skipped = yread.plan_pages(tmp_path, pages, selector=None, force=False)
    assert [p["slug"] for p in todo] == [pages[1]["slug"]]
    assert [p["slug"] for p in skipped] == [pages[0]["slug"]]

    todo, skipped = yread.plan_pages(tmp_path, pages, selector=pages[0]["slug"], force=False)
    assert [p["slug"] for p in todo] == [pages[0]["slug"]]
    assert skipped == []


def test_plan_pages_regenerates_pages_with_changed_associated_files(tmp_path: Path) -> None:
    pages = yread.assign_page_fields([
        {
            "section": "Get Started",
            "group": "",
            "title": "Overview",
            "level": "Beginner",
            "associatedFiles": ["README.md"],
        },
        {
            "section": "Deep Dive",
            "group": "",
            "title": "Runtime",
            "level": "Advanced",
            "associatedFiles": ["src/"],
        },
    ])
    for page in pages:
        (tmp_path / page["file"]).write_text(f"# {page['title']}\n")

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


def test_write_one_page_preserves_existing_page_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = yread.assign_page_fields([
        {"section": "Get Started", "group": "", "title": "Overview", "level": "Beginner"}
    ])[0]
    target = tmp_path / page["file"]
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
        max_steps=1,
        max_topics=2,
        concurrency=1,
        enable_shell=False,
    )

    _slug, ok, error = yread.write_one_page(settings, config, tmp_path, tmp_path, ".", [page], page)

    assert ok is False
    assert "model returned invalid output" in error
    assert target.read_text() == "# Previous good page\n"

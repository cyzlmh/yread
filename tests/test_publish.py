import json
import shutil
from pathlib import Path

import pytest

from yread import builder, cli, publisher


def _make_project(root: Path, project_id: str = "owner/repo") -> Path:
    wiki = root / ".yread"
    (wiki / "wiki").mkdir(parents=True)
    (wiki / "wiki" / "1-overview.md").write_text("# Overview\n", encoding="utf-8")
    (wiki / "wiki.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "project_id": project_id,
                "status": "complete",
                "generated_at": "2026-08-13T00:00:00Z",
                "yread_version": "0.8.0",
                "language": "en",
                "depth": "brief",
                "mode": "software",
                "pages": [
                    {
                        "slug": "1-overview",
                        "title": "Overview",
                        "file": "wiki/1-overview.md",
                        "section": "Guide",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    builder.build_site(wiki)
    return root / ".yread-dist"


def test_parse_target_accepts_ssh_alias_and_rejects_shell_syntax() -> None:
    assert publisher.parse_target("deploy@docs:/srv/yread/") == (
        "deploy@docs",
        "/srv/yread",
    )
    with pytest.raises(SystemExit, match="HUB_TARGET must look like"):
        publisher.parse_target("deploy@docs:/srv/yread;rm")
    with pytest.raises(SystemExit, match="HUB_TARGET must look like"):
        publisher.parse_target("https://docs.example.com")


def test_publish_uploads_flat_site_and_project_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = _make_project(tmp_path)
    commands: list[list[str]] = []
    uploaded_meta: dict = {}

    monkeypatch.setattr(publisher.shutil, "which", lambda _name: "/usr/bin/tool")
    shutil.rmtree(tmp_path / ".yread")  # dist is the complete publish input

    def fake_run(argv: list[str], *, check: bool) -> None:
        assert check is True
        commands.append(argv)
        if argv[0] == "rsync":
            staging = Path(argv[-2])
            uploaded_meta.update(
                json.loads((staging / "project.json").read_text(encoding="utf-8"))
            )
            assert (staging / "index.html").is_file()
            assert (staging / "1-overview.html").is_file()

    remote = publisher.publish_site(
        dist, "deploy@docs:/srv/yread", run=fake_run
    )

    assert remote == "projects/owner/repo/"
    assert commands[0] == [
        "ssh",
        "deploy@docs",
        "mkdir -p -- /srv/yread/projects/owner/repo",
    ]
    assert commands[1][0:4] == [
        "rsync",
        "--archive",
        "--delete",
        "--delay-updates",
    ]
    assert "--chmod=D755,F644" in commands[1]
    assert commands[1][-1] == "deploy@docs:/srv/yread/projects/owner/repo/"
    assert uploaded_meta == {
        "schema_version": 1,
        "project_id": "owner/repo",
        "generated_at": "2026-08-13T00:00:00Z",
        "yread_version": "0.8.0",
        "language": "en",
        "depth": "brief",
        "mode": "software",
        "pages": 1,
    }
    assert not (dist / "project.json").exists()


def test_publish_requires_matching_complete_build(tmp_path: Path) -> None:
    dist = _make_project(tmp_path)
    (dist / "1-overview.html").unlink()

    with pytest.raises(SystemExit, match="expected 1 pages, found 0"):
        publisher.publish_site(dist, "deploy@docs:/srv/yread")


def test_publish_rejects_html_without_build_metadata(tmp_path: Path) -> None:
    dist = tmp_path / ".yread-dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>site</title>", encoding="utf-8")

    with pytest.raises(SystemExit, match="has no valid yread metadata"):
        publisher.publish_site(dist, "deploy@docs:/srv/yread")


def test_publish_cli_uses_configured_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    config = tmp_path / "config.env"
    config.write_text("HUB_TARGET=deploy@docs:/srv/yread\n", encoding="utf-8")
    monkeypatch.setattr(cli, "CONFIG_FILE", config)
    called = {}

    def fake_publish(dist: Path, target: str) -> str:
        called.update(dist=dist, target=target)
        return "projects/owner/repo/"

    monkeypatch.setattr(cli.publisher, "publish_site", fake_publish)

    assert cli.main(["publish", "site"]) == 0
    assert called == {"dist": Path("site"), "target": "deploy@docs:/srv/yread"}
    assert "published projects/owner/repo/" in capsys.readouterr().out


def test_publish_cli_uses_existing_default_dist_without_preparing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.env"
    config.write_text("HUB_TARGET=deploy@docs:/srv/yread\n", encoding="utf-8")
    monkeypatch.setattr(cli, "CONFIG_FILE", config)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".yread-dist").mkdir()
    called = {}

    monkeypatch.setattr(
        cli.builder, "build_site", lambda *_args: pytest.fail("build must not run")
    )
    monkeypatch.setattr(
        cli.core, "run_generate", lambda *_args: pytest.fail("generate must not run")
    )
    monkeypatch.setattr(
        cli.publisher,
        "publish_site",
        lambda dist, target: called.update(dist=dist, target=target) or "projects/owner/repo/",
    )

    assert cli.main(["publish"]) == 0
    assert called == {
        "dist": Path(".yread-dist"),
        "target": "deploy@docs:/srv/yread",
    }


def test_publish_cli_builds_when_only_wiki_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.env"
    config.write_text("HUB_TARGET=deploy@docs:/srv/yread\n", encoding="utf-8")
    monkeypatch.setattr(cli, "CONFIG_FILE", config)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".yread").mkdir()
    called = {}

    monkeypatch.setattr(
        cli.core, "run_generate", lambda *_args: pytest.fail("generate must not run")
    )

    def fake_build(wiki: Path) -> Path:
        called["wiki"] = wiki
        return tmp_path / ".yread-dist"

    monkeypatch.setattr(cli.builder, "build_site", fake_build)
    monkeypatch.setattr(
        cli.publisher,
        "publish_site",
        lambda dist, target: called.update(dist=dist, target=target) or "projects/owner/repo/",
    )

    assert cli.main(["publish"]) == 0
    assert called == {
        "wiki": Path(".yread"),
        "dist": tmp_path / ".yread-dist",
        "target": "deploy@docs:/srv/yread",
    }


def test_publish_cli_generates_and_builds_when_no_artifacts_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "config.env"
    config_file.write_text("HUB_TARGET=deploy@docs:/srv/yread\n", encoding="utf-8")
    monkeypatch.setattr(cli, "CONFIG_FILE", config_file)
    monkeypatch.chdir(tmp_path)
    called = {}
    runtime_config = object()

    def fake_config(args, *, config_files):
        called.update(generate_args=args, config_files=config_files)
        return runtime_config

    def fake_generate(args, config) -> Path:
        assert args is called["generate_args"]
        assert config is runtime_config
        called["generated"] = True
        return tmp_path / "generated-wiki"

    def fake_build(wiki: Path) -> Path:
        called["wiki"] = wiki
        return tmp_path / "generated-dist"

    monkeypatch.setattr(cli.core, "config_from_args", fake_config)
    monkeypatch.setattr(cli.core, "run_generate", fake_generate)
    monkeypatch.setattr(cli.builder, "build_site", fake_build)
    monkeypatch.setattr(
        cli.publisher,
        "publish_site",
        lambda dist, target: called.update(dist=dist, target=target) or "projects/owner/repo/",
    )

    assert cli.main(["publish"]) == 0
    assert called["generate_args"].repo_path == "."
    assert called["config_files"] == [config_file]
    assert called["generated"] is True
    assert called["wiki"] == tmp_path / "generated-wiki"
    assert called["dist"] == tmp_path / "generated-dist"
    assert called["target"] == "deploy@docs:/srv/yread"


def test_publish_cli_passes_gen_flags_to_generate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "config.env"
    config_file.write_text("HUB_TARGET=deploy@docs:/srv/yread\n", encoding="utf-8")
    monkeypatch.setattr(cli, "CONFIG_FILE", config_file)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".yread-dist").mkdir()  # cached: a gen flag should still re-run generate
    called = {}

    def fake_generate(args, _config) -> Path:
        called["generate_args"] = args
        return tmp_path / "generated-wiki"

    monkeypatch.setattr(cli.core, "run_generate", fake_generate)

    def fake_build(wiki: Path) -> Path:
        called["wiki"] = wiki
        return tmp_path / "generated-dist"

    monkeypatch.setattr(cli.builder, "build_site", fake_build)
    monkeypatch.setattr(
        cli.publisher,
        "publish_site",
        lambda dist, target: called.update(dist=dist, target=target) or "projects/owner/repo/",
    )

    assert cli.main(["publish", "--depth", "deep", "--mode", "ml"]) == 0
    assert called["generate_args"].depth == "deep"
    assert called["generate_args"].mode == "ml"
    assert called["dist"] == tmp_path / "generated-dist"


def test_caddy_scaffold_is_static_and_uses_browse_json() -> None:
    root = Path(__file__).parents[1] / "deploy" / "caddy"
    caddyfile = (root / "Caddyfile").read_text(encoding="utf-8")
    index = (root / "index.html").read_text(encoding="utf-8")

    assert "file_server browse" in caddyfile
    assert "path /projects /projects/" in caddyfile
    assert "not header Accept application/json" in caddyfile
    assert "redir @projects_page /" in caddyfile
    assert 'walk("/projects/")' in index
    assert 'Accept:"application/json"' in index
    assert "project.json" in index
    assert "projects.json" not in index
    assert "<style>" in index and "<script>" in index
    assert "<title>yread</title>" in index
    assert "<h1>yread</h1>" in index
    assert "yread Hub" not in index

import json
import stat
from pathlib import Path

import pytest

from yread import builder, cli


def _make_wiki(root: Path, *, status: str = "complete") -> Path:
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "1-概览.md").write_text(
        "# Overview\n\nSee [Details](2-details.md#usage).\n\n"
        "Sources: [README](README.md#L1-L4), [docs](docs/)\n",
        encoding="utf-8",
    )
    (root / "wiki" / "2-details.md").write_text(
        "# Details\n\n## Usage\n\n```python\nprint(1)\n```\n", encoding="utf-8"
    )
    meta = {
        "schema_version": 2,
        "project_id": "owner/repo",
        "status": status,
        "generated_at": "2026-08-13T00:00:00Z",
        "language": "en",
        "depth": "brief",
        "mode": "software",
        "project_profile": {
            "total_files": 2,
            "source_files": 2,
            "primary_languages": ["Python"],
            "max_depth": 2,
            "has_readme": True,
            "has_tests": False,
            "has_ci": False,
            "package_files": [],
            "entry_points": [],
        },
        "pages": [
            {
                "slug": "1-概览",
                "title": "Overview",
                "file": "wiki/1-概览.md",
                "section": "Guide",
            },
            {
                "slug": "2-details",
                "title": "Details",
                "file": "wiki/2-details.md",
                "section": "Guide",
            },
        ],
    }
    (root / "wiki.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    return root


def test_build_site_writes_flat_self_contained_html(tmp_path: Path) -> None:
    wiki = _make_wiki(tmp_path / ".yread")

    output = builder.build_site(wiki)

    assert output == tmp_path / ".yread-dist"
    assert {path.name for path in output.iterdir()} == {
        "index.html",
        "1-概览.html",
        "2-details.html",
    }
    index = (output / "index.html").read_text(encoding="utf-8")
    overview = (output / "1-概览.html").read_text(encoding="utf-8")
    details = (output / "2-details.html").read_text(encoding="utf-8")
    assert "owner/repo · Profile" in index
    assert 'name="yread:project"' in index
    assert '&quot;project_id&quot;:&quot;owner/repo&quot;' in index
    assert 'class="hub" href="/">\u2190 All projects</a>' in index
    assert 'class="hub" href="/">\u2190 All projects</a>' in overview
    assert 'class="hub" href="/">\u2190 All projects</a>' in details
    assert '<html lang="en">' in overview
    assert '<main><article>' in overview
    assert 'class="sources"' in overview
    assert (
        'href="https://github.com/owner/repo/blob/HEAD/README.md#L1-L4"'
        in overview
    )
    assert 'href="https://github.com/owner/repo/tree/HEAD/docs/"' in overview
    assert 'href="1-%E6%A6%82%E8%A7%88.html"' in index
    assert 'href="2-details.html#usage"' in overview
    assert 'href="index.html"' in overview
    assert "<style>" in details and "<script>" in details
    assert 'class="codehilite"' in details
    assert overview.index("On this page") < overview.index("Guide")
    assert 'class="pager"' in overview
    assert 'class="pager-next" href="2-details.html"' in overview
    assert 'class="pager-prev" href="1-%E6%A6%82%E8%A7%88.html"' in details
    assert not (output / "assets").exists()
    assert not (output / "p").exists()
    assert stat.S_IMODE(output.stat().st_mode) == 0o755


def test_build_non_github_sources_are_text_not_broken_links(tmp_path: Path) -> None:
    wiki = _make_wiki(tmp_path / ".yread")
    meta_path = wiki / "wiki.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["project_id"] = "local-project"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    output = builder.build_site(wiki)
    overview = (output / "1-概览.html").read_text(encoding="utf-8")

    assert '<p class="sources">Sources: README, docs</p>' in overview
    assert 'href="README.md#L1-L4"' not in overview


def test_build_escapes_metadata_and_untrusted_markdown_html(tmp_path: Path) -> None:
    wiki = _make_wiki(tmp_path / ".yread")
    meta_path = wiki / "wiki.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["project_id"] = 'local-<script>alert("project")</script>'
    meta["pages"][0]["title"] = "</a><script>alert('title')</script>"
    meta["pages"][0]["section"] = '<img src=x onerror="alert(1)">'
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (wiki / "wiki" / "1-概览.md").write_text(
        "# Safe heading\n\n"
        "<script>alert('body')</script>\n\n"
        "[unsafe](javascript:alert('link'))\n\n"
        "```mermaid\n"
        "graph TD\n"
        "A[\"</div><script>alert('mermaid')</script>\"]\n"
        "```\n",
        encoding="utf-8",
    )

    output = builder.build_site(wiki)
    page = (output / "1-概览.html").read_text(encoding="utf-8")

    assert "<script>alert('title')</script>" not in page
    assert '<script>alert("project")</script>' not in page
    assert "<script>alert('body')</script>" not in page
    assert "<script>alert('mermaid')</script>" not in page
    assert '<img src=x onerror="alert(1)">' not in page
    assert 'href="javascript:' not in page
    assert ">unsafe</a>" in page
    assert "alert('body')" not in page
    assert '<div class="mermaid">' in page
    assert '&lt;/div&gt;&lt;script&gt;alert(\'mermaid\')&lt;/script&gt;' in page


def test_build_site_replaces_old_output_after_success(tmp_path: Path) -> None:
    wiki = _make_wiki(tmp_path / ".yread")
    output = tmp_path / ".yread-dist"
    output.mkdir()
    (output / "stale.html").write_text("old", encoding="utf-8")

    builder.build_site(wiki)

    assert not (output / "stale.html").exists()
    assert (output / "index.html").is_file()


def test_build_site_rejects_incomplete_or_unsafe_artifact(tmp_path: Path) -> None:
    wiki = _make_wiki(tmp_path / ".yread", status="incomplete")
    with pytest.raises(SystemExit, match="not complete"):
        builder.build_site(wiki)

    meta = json.loads((wiki / "wiki.json").read_text(encoding="utf-8"))
    meta["status"] = "complete"
    meta["pages"][0]["file"] = "../outside.md"
    (wiki / "wiki.json").write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(SystemExit, match="points outside"):
        builder.build_site(wiki)


def test_build_site_rejects_slug_that_would_replace_homepage(tmp_path: Path) -> None:
    wiki = _make_wiki(tmp_path / ".yread")
    meta = json.loads((wiki / "wiki.json").read_text(encoding="utf-8"))
    meta["pages"][0]["slug"] = "index"
    (wiki / "wiki.json").write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(SystemExit, match="duplicate page slug: index"):
        builder.build_site(wiki)


def test_build_cli_defaults_to_dot_yread(tmp_path: Path, monkeypatch, capsys) -> None:
    _make_wiki(tmp_path / ".yread")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["build"]) == 0

    assert (tmp_path / ".yread-dist" / "index.html").is_file()
    assert "built " in capsys.readouterr().out

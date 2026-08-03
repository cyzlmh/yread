from pathlib import Path

from yread import viewer as view


def _make_wiki(root: Path, slug: str = "1-overview") -> None:
    """Minimal v2 wiki at ``root`` (no project_profile -> home redirects to the page)."""
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / f"{slug}.md").write_text("# Hi\n\nsee [other](2-other.md)\n")
    (root / "wiki" / "2-other.md").write_text("# Other\n")
    pages = [
        {"slug": slug, "title": "Overview", "file": f"wiki/{slug}.md"},
        {"slug": "2-other", "title": "Other", "file": "wiki/2-other.md"},
    ]
    import json
    (root / "wiki.json").write_text(
        json.dumps({"schema_version": 2, "pages": pages, "language": "zh",
                    "depth": "standard", "mode": "ml",
                    "generated_at": "2026-07-19T12:39:38Z",
                    "yread_version": "0.6.0"}),
        encoding="utf-8")


def test_find_wikis_discovers_direct_and_default_output_dirs(tmp_path: Path) -> None:
    _make_wiki(tmp_path / "projA")           # wiki dir is the child itself
    _make_wiki(tmp_path / "projB" / ".yread")  # wiki in the default output dir
    (tmp_path / "not-a-wiki").mkdir()

    found = view.find_wikis(tmp_path)

    assert found == {"projA": tmp_path / "projA", "projB": tmp_path / "projB" / ".yread"}


def test_site_source_flag_controls_source_root_resolution(tmp_path: Path) -> None:
    """With source=False a recorded source_root is ignored, so /src/ stays closed."""
    import json
    repo = tmp_path / "therealrepo"
    repo.mkdir()
    wiki = tmp_path / "out"
    wiki.mkdir()
    (wiki / "wiki.json").write_text(json.dumps(
        {"schema_version": 2, "pages": [], "source_root": str(repo)}), encoding="utf-8")

    assert view.Site(wiki).repo == repo                 # default: resolve source_root
    assert view.Site(wiki, source=False).repo is None   # deployment: keep /src/ closed


def test_multi_wiki_index_rescans_without_restart(tmp_path: Path) -> None:
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    _make_wiki(tmp_path / "projA")

    old = (view.Handler.__dict__.get("sites"), view.Handler.__dict__.get("site_mtimes"),
           view.Handler.wiki_root, view.Handler.multi, view.Handler.enable_source)
    view.Handler.multi = True
    view.Handler.enable_source = False
    view.Handler.wiki_root = tmp_path
    view.Handler.sites, view.Handler.site_mtimes = {}, {}
    view.Handler.refresh()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), view.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        index = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
        assert 'href="/w/projA/"' in index and "projB" not in index

        _make_wiki(tmp_path / "projB")  # "upload" a new wiki: no restart needed
        index = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
        assert 'href="/w/projB/"' in index
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/w/projB/p/2-other", timeout=5).read().decode()
        assert "Other" in page

        import shutil
        shutil.rmtree(tmp_path / "projA")  # removed wikis disappear from the index
        index = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
        assert "projA" not in index and 'href="/w/projB/"' in index
    finally:
        srv.shutdown()
        (view.Handler.sites, view.Handler.site_mtimes,
         view.Handler.wiki_root, view.Handler.multi, view.Handler.enable_source) = old


def test_multi_wiki_server_serves_index_and_prefixed_pages(tmp_path: Path) -> None:
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    _make_wiki(tmp_path / "projA")
    _make_wiki(tmp_path / "projB" / ".yread")

    old_sites, old_multi = view.Handler.__dict__.get("sites"), view.Handler.multi
    view.Handler.multi = True
    view.Handler.sites = {name: view.Site(wiki) for name, wiki in view.find_wikis(tmp_path).items()}

    srv = ThreadingHTTPServer(("127.0.0.1", 0), view.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        index = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
        assert 'href="/w/projA/"' in index and 'href="/w/projB/"' in index
        assert "2026-07-19" in index  # generation date column
        assert '<td data-label="Lang">zh</td>' in index  # lang/depth/mode columns
        assert '<td data-label="Mode">ml</td>' in index
        assert "0.6.0" in index  # generator version column

        # project home redirects to the first page; links carry the /w/<name>/ prefix
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/w/projA/", timeout=5).read().decode()
        assert "# Hi" not in page  # rendered to HTML, not raw markdown
        assert 'href="/w/projA/p/2-other"' in page  # inter-page link is prefixed
        assert 'href="/w/projA/p/1-overview"' in page  # sidebar links are prefixed

        other = urllib.request.urlopen(f"http://127.0.0.1:{port}/w/projB/p/2-other", timeout=5).read().decode()
        assert "Other" in other

        # single-wiki routes must not leak into multi-wiki mode
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/p/1-overview", timeout=5)
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()
        view.Handler.multi = old_multi
        if old_sites is not None:
            view.Handler.sites = old_sites


def test_render_body_highlights_code_and_builds_toc() -> None:
    md = "# Title\n\n## Section A\n\n```python\nprint(1)\n```\n\n### Sub\n\ntext\n"
    html, toc = view.render_body(md, set(), None)

    assert 'class="codehilite"' in html          # pygments-wrapped block
    assert '<span class="nb">' in html           # `print` got a token span
    assert 'id="section-a"' in html              # heading anchors
    assert 'href="#section-a"' in toc and 'href="#sub"' in toc
    assert "Title" not in toc                    # h1 is excluded (page title)


def test_render_page_injects_pygments_css() -> None:
    html = view.render_page("t", "n", "b")
    assert ".codehilite" in html and "github-dark" not in html  # css, not the marker
    assert ".dark .codehilite" in html  # dark variant scoped to the theme class
    assert 'id="yr-theme"' in html      # light/dark toggle button


def test_resolve_wiki_defaults_to_yread_output_root(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / ".yread"
    (root / "wiki").mkdir(parents=True)
    (root / "wiki.json").write_text('{"schema_version": 2, "pages": []}\n')
    monkeypatch.chdir(tmp_path)

    assert view.resolve_wiki(None) == root


def test_safe_source_path_stays_inside_repo_and_blocks_sensitive_files(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('ok')\n")
    (tmp_path / ".env").write_text("SECRET=1\n")

    assert view.safe_source_path(tmp_path, "src/main.py") == (tmp_path / "src" / "main.py")
    assert view.safe_source_path(tmp_path, ".env") is None
    assert view.safe_source_path(tmp_path, "../outside.txt") is None

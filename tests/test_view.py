from yread import viewer as view


def test_render_body_highlights_code_and_builds_toc() -> None:
    md = "# Title\n\n## Section A\n\n```python\nprint(1)\n```\n\n### Sub\n\ntext\n"
    html, toc = view.render_body(md, set())

    assert 'class="codehilite"' in html
    assert '<span class="nb">' in html
    assert 'id="section-a"' in html
    assert 'href="#section-a"' in toc and 'href="#sub"' in toc
    assert "Title" not in toc


def test_build_nav_places_page_toc_before_project_sections() -> None:
    pages = [{"slug": "one", "title": "One", "section": "Guide"}]

    nav = view.build_nav(pages, "one", toc='<div class="toc">TOC</div>')

    assert nav.index("TOC") < nav.index("Guide")


def test_page_pager_links_adjacent_pages() -> None:
    pages = [
        {"slug": "one", "title": "One"},
        {"slug": "two", "title": "Two"},
        {"slug": "three", "title": "Three"},
    ]

    pager = view.build_pager(pages, "two", page_href=lambda slug: f"{slug}.html")

    assert 'class="pager-prev" href="one.html"' in pager
    assert 'class="pager-next" href="three.html"' in pager
    assert "Previous" in pager and "Next" in pager


def test_render_page_injects_pygments_css() -> None:
    html = view.render_page("t", "n", "b", language="en")

    assert ".codehilite" in html and "github-dark" not in html
    assert ".dark .codehilite" in html
    assert 'id="yr-theme"' in html
    assert '<html lang="en">' in html
    assert '<main><article>b</article></main>' in html

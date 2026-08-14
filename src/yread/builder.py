"""Render a completed yread artifact as flat, self-contained HTML files."""

from __future__ import annotations

import html
import json
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

from . import viewer


def _load_artifact(wiki_dir: Path) -> tuple[dict, list[dict]]:
    meta_path = wiki_dir / "wiki.json"
    if not meta_path.is_file():
        raise SystemExit(f"no wiki.json under {wiki_dir}")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid wiki.json under {wiki_dir}: {exc}") from exc
    if meta.get("schema_version") != 2:
        raise SystemExit(
            f"unsupported wiki schema under {wiki_dir}: expected schema_version 2"
        )
    if meta.get("status") != "complete":
        raise SystemExit(
            f"wiki under {wiki_dir} is not complete; run yread generate again"
        )
    if not isinstance(meta.get("project_id"), str) or not meta["project_id"].strip():
        raise SystemExit(f"wiki under {wiki_dir} has no project_id")
    pages = meta.get("pages")
    if not isinstance(pages, list):
        raise SystemExit(f"wiki under {wiki_dir} has no valid pages list")

    seen: set[str] = {"index"}
    for page in pages:
        if not isinstance(page, dict):
            raise SystemExit(f"wiki under {wiki_dir} contains an invalid page entry")
        slug = page.get("slug")
        if not isinstance(slug, str) or not slug or slug in {".", ".."} or any(
            c in slug for c in '/\\\0<>:"|?*'
        ):
            raise SystemExit(f"wiki under {wiki_dir} contains an unsafe page slug: {slug!r}")
        slug_key = slug.casefold()
        if slug_key in seen:
            raise SystemExit(f"wiki under {wiki_dir} contains duplicate page slug: {slug}")
        seen.add(slug_key)
        if not isinstance(page.get("title"), str) or not isinstance(page.get("file"), str):
            raise SystemExit(f"wiki page {slug} has no valid title or file")
        source = (wiki_dir / page["file"]).resolve()
        try:
            source.relative_to(wiki_dir)
        except ValueError as exc:
            raise SystemExit(f"wiki page {slug} points outside {wiki_dir}") from exc
        if not source.is_file():
            raise SystemExit(f"wiki page {slug} is missing: {page['file']}")
    return meta, pages


def _page_href(slug: str) -> str:
    return f"{quote(slug)}.html"


def _publish_metadata(meta: dict, pages: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "project_id": meta["project_id"],
        "generated_at": meta.get("generated_at", ""),
        "yread_version": meta.get("yread_version", ""),
        "language": meta.get("language", ""),
        "depth": meta.get("depth", ""),
        "mode": meta.get("mode", ""),
        "pages": len(pages),
    }


def _embed_publish_metadata(document: str, meta: dict, pages: list[dict]) -> str:
    value = html.escape(
        json.dumps(_publish_metadata(meta, pages), ensure_ascii=False, separators=(",", ":")),
        quote=True,
    )
    tag = f'<meta name="yread:project" content="{value}">'
    return document.replace("</head>", f"{tag}\n</head>", 1)


def build_site(wiki_dir: Path, output_dir: Path | None = None) -> Path:
    """Build ``wiki_dir`` into a flat static site and return its output path."""
    wiki_dir = wiki_dir.resolve()
    output_dir = (output_dir or wiki_dir.with_name(".yread-dist")).resolve()
    try:
        wiki_dir.relative_to(output_dir)
    except ValueError:
        pass
    else:
        raise SystemExit(f"output directory cannot contain the input wiki: {output_dir}")

    meta, pages = _load_artifact(wiki_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    try:
        slugs = {page["slug"] for page in pages}
        project_name = meta["project_id"]
        language = meta.get("language", "en")
        home = viewer.build_profile_html(meta, project_name)
        if home is None:
            home = f"<h1>{html.escape(project_name)}</h1>"
        nav = viewer.build_nav(
            pages,
            None,
            on_home=True,
            page_href=_page_href,
            home_href="index.html",
            hub_href="/",
            language=language,
        )
        index = viewer.render_page(
            f"{project_name} · Profile", nav, home, language=language
        )
        (staging / "index.html").write_text(
            _embed_publish_metadata(index, meta, pages), encoding="utf-8"
        )

        for page in pages:
            markdown = (wiki_dir / page["file"]).read_text(
                encoding="utf-8", errors="replace"
            )
            body, toc = viewer.render_body(
                markdown,
                slugs,
                page_href=_page_href,
                source_project=project_name,
            )
            nav = viewer.build_nav(
                pages,
                page["slug"],
                page_href=_page_href,
                home_href="index.html",
                hub_href="/",
                toc=toc,
                language=language,
            )
            body += viewer.build_pager(
                pages, page["slug"], page_href=_page_href, language=language
            )
            (staging / f"{page['slug']}.html").write_text(
                viewer.render_page(page["title"], nav, body, language=language),
                encoding="utf-8",
            )

        if output_dir.exists():
            if not output_dir.is_dir():
                raise SystemExit(f"output path is not a directory: {output_dir}")
            shutil.rmtree(output_dir)
        staging.chmod(0o755)
        staging.replace(output_dir)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return output_dir

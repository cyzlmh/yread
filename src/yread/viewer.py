"""view — browse a yread wiki in the browser, the way zread's server does.

    yread view [wiki_dir] [--port 8000] [--repo <repo_path>]

wiki_dir defaults to ./.yread/wiki. If it holds a `current` pointer the latest
version is served; a version dir (with wiki.json) also works directly.

Renders each page's markdown with mermaid diagrams, a section/level sidebar from
wiki.json, and resolves the inter-page `[title](slug)` cross-links. With --repo,
the `Sources: [file](path#Lx-Ly)` citations link to the real source files.
"""
import re
import sys
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import markdown


SENSITIVE_SOURCE_NAMES = {".env", ".env.local", ".npmrc", ".pypirc", ".netrc", "auth.json", "credentials.json"}


def resolve_wiki(arg: str | None) -> Path:
    root = Path(arg).resolve() if arg else Path(".yread/wiki").resolve()
    if (root / "wiki.json").exists():
        return root
    cur = root / "current"
    if cur.exists():
        return (root / cur.read_text().strip()).resolve()
    # maybe given the wiki/ root with versions/ but no current
    versions = sorted((root / "versions").glob("*")) if (root / "versions").exists() else []
    if versions:
        return versions[-1]
    raise SystemExit(f"no wiki.json (or current pointer) under {root}")


PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · yread</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>mermaid.initialize({{startOnLoad:true, theme:"neutral"}});</script>
<style>
 :root{{--fg:#1f2328;--muted:#656d76;--line:#d0d7de;--accent:#0969da;--bg:#fff;--side:#f6f8fa}}
 *{{box-sizing:border-box}} body{{margin:0;font:16px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,"PingFang SC","Microsoft YaHei",sans-serif;color:var(--fg);background:var(--bg)}}
 .wrap{{display:flex;min-height:100vh}}
 nav{{width:320px;flex:none;background:var(--side);border-right:1px solid var(--line);padding:18px 14px;overflow-y:auto;height:100vh;position:sticky;top:0}}
 nav h1{{font-size:14px;margin:4px 6px 14px;color:var(--muted);letter-spacing:.04em;text-transform:uppercase}}
 nav .sec{{font-weight:700;margin:16px 6px 6px;font-size:13px;color:var(--fg)}}
 nav .grp{{font-weight:600;margin:10px 6px 4px;font-size:12px;color:var(--muted)}}
 nav a{{display:block;padding:5px 8px;border-radius:6px;color:var(--fg);text-decoration:none;font-size:13.5px}}
 nav a:hover{{background:#eaeef2}} nav a.active{{background:#ddf4ff;color:var(--accent);font-weight:600}}
 nav .lv{{color:var(--muted);font-size:11px;margin-left:4px}}
 main{{flex:1;max-width:900px;padding:36px 48px;overflow-x:auto}}
 main a{{color:var(--accent);text-decoration:none}} main a:hover{{text-decoration:underline}}
 pre{{background:var(--side);padding:14px;border-radius:8px;overflow-x:auto;font-size:13.5px}}
 code{{background:rgba(175,184,193,.2);padding:.15em .3em;border-radius:4px;font-size:85%}}
 pre code{{background:none;padding:0}}
 table{{border-collapse:collapse;margin:14px 0;display:block;overflow-x:auto}}
 th,td{{border:1px solid var(--line);padding:7px 12px}} th{{background:var(--side)}}
 .mermaid{{background:var(--side);border-radius:8px;padding:14px;margin:16px 0;text-align:center}}
 h1,h2,h3{{line-height:1.3}} h2{{border-bottom:1px solid var(--line);padding-bottom:.3em;margin-top:1.6em}}
 blockquote{{border-left:3px solid var(--line);margin:14px 0;padding:2px 14px;color:var(--muted)}}
</style></head><body><div class="wrap"><nav>{nav}</nav><main>{body}</main></div></body></html>"""


def build_nav(pages, active):
    out, last_sec, last_grp = ['<h1>Wiki</h1>'], None, None
    for p in pages:
        if p.get("section") != last_sec:
            last_sec = p.get("section"); last_grp = None
            out.append(f'<div class="sec">{last_sec or ""}</div>')
        grp = p.get("group")
        if grp and grp != last_grp:
            last_grp = grp; out.append(f'<div class="grp">{grp}</div>')
        cls = " active" if p["slug"] == active else ""
        lv = f'<span class="lv">{p.get("level","")}</span>' if p.get("level") else ""
        out.append(f'<a class="page{cls}" href="/p/{p["slug"]}">{p["title"]}{lv}</a>')
    return "\n".join(out)


def render_body(md_text: str, slugs: set, repo: Path | None) -> str:
    # mermaid fences -> <div class="mermaid"> before markdown so they survive as HTML
    def mm(m): return f'<div class="mermaid">\n{m.group(1)}\n</div>'
    md_text = re.sub(r"```mermaid\s*\n(.*?)```", mm, md_text, flags=re.DOTALL)
    html = markdown.markdown(md_text, extensions=["fenced_code", "tables", "sane_lists"])
    # rewrite inter-page links: href="slug" or "slug.md" -> "/p/slug"
    def link(m):
        href = m.group(1); base = href[:-3] if href.endswith(".md") else href
        anchor = ""
        if "#" in base and base.split("#")[0] in slugs:
            base, anchor = base.split("#", 1); anchor = "#" + anchor
        if base in slugs:
            return f'href="/p/{base}{anchor}"'
        if repo and not href.startswith(("http", "/p/")):  # source citation -> repo file
            return f'href="/src/{href}"'
        return m.group(0)
    return re.sub(r'href="([^"]+)"', link, html)


def safe_source_path(repo: Path, rel: str) -> Path | None:
    rel = unquote(rel).split("#")[0]
    parts = Path(rel).parts
    if any(part in SENSITIVE_SOURCE_NAMES or part.startswith(".env.") for part in parts):
        return None
    f = (repo / rel).resolve()
    try:
        f.relative_to(repo.resolve())
    except ValueError:
        return None
    return f if f.is_file() else None


class Handler(BaseHTTPRequestHandler):
    wiki: Path; pages: list; byslug: dict; repo: Path | None

    def log_message(self, *a): pass

    def _send(self, body: bytes, ctype="text/html; charset=utf-8", code=200):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self.send_response(302); self.send_header("Location", f"/p/{self.pages[0]['slug']}")
            self.end_headers(); return
        if path.startswith("/src/") and self.repo:
            f = safe_source_path(self.repo, path[len("/src/"):])
            if f:
                return self._send(f.read_text(errors="replace").encode(), "text/plain; charset=utf-8")
            return self._send(b"not found", code=404)
        if path.startswith("/p/"):
            slug = path[len("/p/"):]
            p = self.byslug.get(slug)
            if not p:
                return self._send(b"page not found", code=404)
            md_text = (self.wiki / p["file"]).read_text(errors="replace")
            slugs = set(self.byslug)
            html = PAGE.format(title=p["title"],
                               nav=build_nav(self.pages, slug),
                               body=render_body(md_text, slugs, self.repo))
            return self._send(html.encode())
        self._send(b"not found", code=404)


def main(argv: list[str] | None = None):
    import json
    args = [a for a in (sys.argv[1:] if argv is None else argv)]
    port = 8000; repo = None; wiki_arg = None
    i = 0
    while i < len(args):
        if args[i] == "--port": port = int(args[i + 1]); i += 2
        elif args[i] == "--repo": repo = Path(args[i + 1]).resolve(); i += 2
        else: wiki_arg = args[i]; i += 1
    wiki = resolve_wiki(wiki_arg)
    meta = json.loads((wiki / "wiki.json").read_text())
    pages = meta["pages"]
    Handler.wiki, Handler.pages = wiki, pages
    Handler.byslug = {p["slug"]: p for p in pages}
    Handler.repo = repo
    url = f"http://127.0.0.1:{port}/"
    print(f"yread viewer: {wiki}\n  {len(pages)} pages -> {url}  (Ctrl-C to stop)", flush=True)
    try: webbrowser.open(url)
    except Exception: pass
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()

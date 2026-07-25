"""view — browse a yread wiki in the browser, the way zread's server does.

    yread browse [wiki_dir] [--host localhost] [--port 8000] [--repo <repo_path>]

wiki_dir defaults to ./.yread and must contain a v2 wiki.json plus Markdown
pages under wiki/.

Renders each page's markdown with mermaid diagrams, a section/group sidebar from
wiki.json, and resolves the inter-page `[title](slug)` cross-links. Pass --repo
to make `Sources: [file](path#Lx-Ly)` citations link to the real files (older
wikis may still carry a `source_root` and resolve it automatically).
"""
import json
import re
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

import markdown


SENSITIVE_SOURCE_NAMES = {".env", ".env.local", ".npmrc", ".pypirc", ".netrc", "auth.json", "credentials.json"}


def resolve_wiki(arg: str | None) -> Path:
    root = Path(arg).resolve() if arg else Path(".yread").resolve()
    if (root / "wiki.json").exists():
        return root
    raise SystemExit(f"no wiki.json under {root}")


PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · yread</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>mermaid.initialize({{startOnLoad:true, theme:"neutral"}});</script>
<style>
 :root{{--fg:#1f2328;--muted:#656d76;--line:#d0d7de;--accent:#0969da;--bg:#fff;--side:#f6f8fa}}
 *{{box-sizing:border-box}} body{{margin:0;font:16px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,"PingFang SC","Microsoft YaHei",sans-serif;color:var(--fg);background:var(--bg)}}
 .wrap{{display:flex;min-height:100vh}}
 nav{{width:320px;flex:none;background:var(--side);border-right:1px solid var(--line);padding:18px 14px;overflow-y:auto;height:100vh;position:sticky;top:0;z-index:65}}
 nav h1{{font-size:14px;margin:4px 6px 14px;color:var(--muted);letter-spacing:.04em;text-transform:uppercase}}
 nav .sec{{font-weight:700;margin:16px 6px 6px;font-size:13px;color:var(--fg)}}
 nav .grp{{font-weight:600;margin:10px 6px 4px;font-size:12px;color:var(--muted)}}
 nav a{{display:block;padding:5px 8px;border-radius:6px;color:var(--fg);text-decoration:none;font-size:13.5px}}
 nav a:hover{{background:#eaeef2}} nav a.active{{background:#ddf4ff;color:var(--accent);font-weight:600}}
 nav .lv{{color:var(--muted);font-size:11px;margin-left:4px}}
 main{{flex:1;min-width:0;max-width:900px;padding:36px 48px;overflow-x:auto}}
 main a{{color:var(--accent);text-decoration:none}} main a:hover{{text-decoration:underline}}
 pre{{background:var(--side);padding:14px;border-radius:8px;overflow-x:auto;font-size:13.5px}}
 code{{background:rgba(175,184,193,.2);padding:.15em .3em;border-radius:4px;font-size:85%}}
 pre code{{background:none;padding:0}}
 table{{border-collapse:collapse;margin:14px 0;display:block;overflow-x:auto}}
 th,td{{border:1px solid var(--line);padding:7px 12px}} th{{background:var(--side)}}
 .mermaid{{background:var(--side);border-radius:8px;padding:14px;margin:16px 0;text-align:center;cursor:zoom-in}}
 main img{{max-width:100%;cursor:zoom-in}}
 h1,h2,h3{{line-height:1.3}} h2{{border-bottom:1px solid var(--line);padding-bottom:.3em;margin-top:1.6em}}
 blockquote{{border-left:3px solid var(--line);margin:14px 0;padding:2px 14px;color:var(--muted)}}
 #yr-menu{{display:none;position:fixed;top:12px;left:12px;z-index:80;width:40px;height:40px;align-items:center;justify-content:center;font-size:20px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.12)}}
 #yr-backdrop{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:64}}
 #yr-zoom{{display:none;position:fixed;inset:0;z-index:90;background:rgba(0,0,0,.82);align-items:center;justify-content:center;padding:24px;cursor:zoom-out}}
 #yr-zoom .yr-zoom-inner{{background:var(--bg);border-radius:10px;padding:18px;max-width:96vw;max-height:94vh;overflow:auto}}
 #yr-zoom .yr-zoom-inner svg{{width:min(1400px,90vw);height:auto;max-width:none}}
 #yr-zoom .yr-zoom-inner img{{max-width:90vw;max-height:88vh}}
 @media (max-width:800px){{
   nav{{position:fixed;left:0;top:0;height:100vh;transform:translateX(-100%);transition:transform .22s ease;box-shadow:2px 0 16px rgba(0,0,0,.15)}}
   body.sb-open nav{{transform:translateX(0)}}
   body.sb-open #yr-backdrop{{display:block}}
   #yr-menu{{display:flex}}
   main{{padding:64px 20px 40px;max-width:100%}}
 }}
</style></head><body><button id="yr-menu" aria-label="目录">☰</button><div id="yr-backdrop"></div><div id="yr-zoom"><div class="yr-zoom-inner"></div></div><div class="wrap"><nav>{nav}</nav><main>{body}</main></div>{scripts}</body></html>"""


# Sidebar drawer toggle plus click-to-zoom for diagrams and images.
LAYOUT_JS = """<script>
(function(){
  var m=document.getElementById('yr-menu'), b=document.getElementById('yr-backdrop'), nav=document.querySelector('nav');
  if(m){
    function toggle(){document.body.classList.toggle('sb-open');}
    m.addEventListener('click',toggle);
    if(b) b.addEventListener('click',toggle);
    if(nav) nav.addEventListener('click',function(e){if(e.target.tagName==='A') document.body.classList.remove('sb-open');});
  }
  // Click-to-zoom for mermaid diagrams and images.
  var main=document.querySelector('main'), zoom=document.getElementById('yr-zoom');
  if(main&&zoom){
    var inner=zoom.querySelector('.yr-zoom-inner');
    function close(){zoom.style.display='none';inner.innerHTML='';}
    main.addEventListener('click',function(e){
      var el=e.target.closest('.mermaid, img'); if(!el) return;
      inner.innerHTML = el.tagName==='IMG' ? '<img src="'+el.getAttribute('src')+'">' : el.innerHTML;
      zoom.style.display='flex';
    });
    zoom.addEventListener('click',close);
    document.addEventListener('keydown',function(e){if(e.key==='Escape') close();});
  }
})();
</script>"""


def build_nav(pages, active, on_home=False):
    home_cls = " active" if on_home else ""
    out = ['<h1>Wiki</h1>', f'<a class="page{home_cls}" href="/">◈ Profile</a>']
    last_sec, last_grp = None, None
    for p in pages:
        if p.get("section") != last_sec:
            last_sec = p.get("section"); last_grp = None
            out.append(f'<div class="sec">{last_sec or ""}</div>')
        grp = p.get("group")
        if grp and grp != last_grp:
            last_grp = grp; out.append(f'<div class="grp">{grp}</div>')
        cls = " active" if p["slug"] == active else ""
        meta = p.get("kind") or p.get("level", "")
        lv = f'<span class="lv">{meta}</span>' if meta else ""
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


def build_profile_html(meta: dict, repo: Path | None, name: str) -> str | None:
    """Render the wiki's project profile + run metadata as HTML for the home view,
    reusing the exact SUMMARY.md formatter (loc/git included when the source repo
    is available). Returns None if the stored profile can't be reconstructed."""
    import dataclasses

    from . import core
    pf = meta.get("project_profile") or {}
    try:
        fields = {f.name for f in dataclasses.fields(core.ProjectProfile)}
        profile = core.ProjectProfile(**{k: v for k, v in pf.items() if k in fields})
    except (TypeError, ValueError):
        return None
    code = core.code_stats(repo) if repo and repo.is_dir() else None
    git = core.git_stats(repo) if repo and repo.is_dir() else None
    md = "\n".join(core._summary_profile_lines(meta, profile, code=code, git=git))
    html = markdown.markdown(md, extensions=["fenced_code", "tables", "sane_lists"])
    return f"<h1>{name} · Profile</h1>\n{html}"


class Handler(BaseHTTPRequestHandler):
    wiki: Path; pages: list; byslug: dict; repo: Path | None
    home_body = None; home_title = "Wiki"

    def log_message(self, *a): pass

    def _send(self, body: bytes, ctype="text/html; charset=utf-8", code=200):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            if self.home_body is not None:
                html = PAGE.format(title=self.home_title,
                                   nav=build_nav(self.pages, None, on_home=True),
                                   body=self.home_body, scripts=LAYOUT_JS)
                return self._send(html.encode())
            # No reconstructable profile: fall back to the first page. Slugs keep
            # CJK/Unicode; HTTP headers are latin-1, so the target must be encoded.
            self.send_response(302)
            self.send_header("Location", "/p/" + quote(self.pages[0]["slug"]))
            self.end_headers(); return
        if path.startswith("/src/") and self.repo:
            f = safe_source_path(self.repo, path[len("/src/"):])
            if f:
                return self._send(f.read_text(errors="replace").encode(), "text/plain; charset=utf-8")
            return self._send(b"not found", code=404)
        if path.startswith("/p/"):
            slug = unquote(path[len("/p/"):])  # browsers percent-encode CJK slugs
            p = self.byslug.get(slug)
            if not p:
                return self._send(b"page not found", code=404)
            md_text = (self.wiki / p["file"]).read_text(errors="replace")
            slugs = set(self.byslug)
            html = PAGE.format(title=p["title"],
                               nav=build_nav(self.pages, slug),
                               body=render_body(md_text, slugs, self.repo),
                               scripts=LAYOUT_JS)
            return self._send(html.encode())
        self._send(b"not found", code=404)


def main(argv: list[str] | None = None):
    args = [a for a in (sys.argv[1:] if argv is None else argv)]
    host = "localhost"; port = 8000; repo = None; wiki_arg = None
    i = 0
    while i < len(args):
        if args[i] == "--port": port = int(args[i + 1]); i += 2
        elif args[i] == "--host": host = args[i + 1]; i += 2
        elif args[i] == "--repo": repo = Path(args[i + 1]).resolve(); i += 2
        else: wiki_arg = args[i]; i += 1
    wiki = resolve_wiki(wiki_arg)
    meta = json.loads((wiki / "wiki.json").read_text())
    if meta.get("schema_version") != 2:
        raise SystemExit(f"unsupported wiki schema under {wiki}: expected schema_version 2")
    if repo is None and meta.get("source_root"):
        recorded = Path(meta["source_root"])
        if recorded.is_dir():
            repo = recorded.resolve()
    pages = meta["pages"]
    name = Path(meta.get("source_root") or "").name or wiki.name or "Wiki"
    Handler.wiki, Handler.pages = wiki, pages
    Handler.byslug = {p["slug"]: p for p in pages}
    Handler.repo = repo
    Handler.home_title = f"{name} · Profile"
    Handler.home_body = build_profile_html(meta, repo, name)
    url = f"http://{host}:{port}/"
    print(f"yread browser: {wiki}\n  {len(pages)} pages -> {url}  (Ctrl-C to stop)", flush=True)
    try: webbrowser.open(url)
    except Exception: pass
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()

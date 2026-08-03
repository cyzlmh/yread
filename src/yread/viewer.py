"""view — browse a yread wiki in the browser, the way zread's server does.

    yread browse [wiki_dir] [--host localhost] [--port 8000] [--repo <repo_path>]

wiki_dir defaults to ./.yread and must contain a v2 wiki.json plus Markdown
pages under wiki/.

Renders each page's markdown with mermaid diagrams, a section/group sidebar from
wiki.json, and resolves the inter-page `[title](slug)` cross-links. Pass --repo
to make `Sources: [file](path#Lx-Ly)` citations link to the real files (older
wikis may still carry a `source_root` and resolve it automatically).

If wiki_dir has no wiki.json of its own, its subdirectories are scanned instead
— each child that is a yread output (or has a default `.yread/` one) is mounted
at /w/<name>/, and `/` becomes a project index that rescans on every visit, so
uploaded wikis appear without a restart. Multi-wiki mode is the deployment
shape: --repo does not apply, and source citations only resolve with
--enable-source (off by default so a public site can't leak a source tree).
"""
import json
import re
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

import markdown
from pygments.formatters import HtmlFormatter


SENSITIVE_SOURCE_NAMES = {".env", ".env.local", ".npmrc", ".pypirc", ".netrc", "auth.json", "credentials.json"}


# Pygments styles for the light/dark color schemes; injected into PAGE after
# .format() (its braces would clash with the format placeholders). The dark
# variant is scoped under :root.dark so the theme toggle controls it.
def _pygments_css() -> str:
    light = HtmlFormatter(style="default").get_style_defs(".codehilite")
    dark = HtmlFormatter(style="github-dark").get_style_defs(".dark .codehilite")
    return f"{light}\n{dark}"


PYGMENTS_CSS = _pygments_css()


def resolve_wiki(arg: str | None) -> Path:
    root = Path(arg).resolve() if arg else Path(".yread").resolve()
    if (root / "wiki.json").exists():
        return root
    raise SystemExit(f"no wiki.json under {root}")


PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · yread</title>
<script>(function(){{var t;try{{t=localStorage.getItem("yr-theme")}}catch(e){{}}
if(t!=="dark"&&t!=="light")t=window.matchMedia&&matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";
document.documentElement.classList.add(t);}})();</script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>mermaid.initialize({{startOnLoad:true, theme:document.documentElement.classList.contains("dark")?"dark":"neutral"}});</script>
<style>
 :root{{--fg:#1f2328;--muted:#656d76;--line:#d0d7de;--accent:#0969da;--bg:#fff;--side:#f6f8fa;--hover:#eaeef2;--active:#ddf4ff}}
 :root.dark{{color-scheme:dark;--fg:#e6edf3;--muted:#8b949e;--line:#30363d;--accent:#58a6ff;--bg:#0d1117;--side:#161b22;--hover:#21262d;--active:#1f6feb26}}
 *{{box-sizing:border-box}} body{{margin:0;font:16px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,"PingFang SC","Microsoft YaHei",sans-serif;color:var(--fg);background:var(--bg)}}
 .wrap{{display:flex;min-height:100vh}}
 nav{{width:320px;flex:none;background:var(--side);border-right:1px solid var(--line);padding:18px 14px;overflow-y:auto;height:100vh;position:sticky;top:0;z-index:65}}
 nav h1{{font-size:14px;margin:4px 6px 14px;color:var(--muted);letter-spacing:.04em;text-transform:uppercase}}
 nav .sec{{font-weight:700;margin:16px 6px 6px;font-size:13px;color:var(--fg)}}
 nav .grp{{font-weight:600;margin:10px 6px 4px;font-size:12px;color:var(--muted)}}
 nav a{{display:block;padding:5px 8px;border-radius:6px;color:var(--fg);text-decoration:none;font-size:13.5px}}
 nav a:hover{{background:var(--hover)}} nav a.active{{background:var(--active);color:var(--accent);font-weight:600}}
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
 #yr-filter{{width:100%;max-width:320px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;font-size:14px;margin:4px 0 12px;background:var(--bg);color:var(--fg)}}
 nav .toc{{padding:0 6px 12px}}
 nav .toc ul{{list-style:none;margin:0;padding-left:12px}}
 nav .toc>ul{{padding-left:6px}}
 nav .toc a{{font-size:12.5px;color:var(--muted);padding:2px 8px}}
 nav .toc a:hover{{color:var(--accent)}}
 #yr-menu{{display:none;position:fixed;top:12px;left:12px;z-index:80;width:40px;height:40px;align-items:center;justify-content:center;font-size:20px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.12)}}
 #yr-theme{{position:fixed;top:12px;right:12px;z-index:80;width:40px;height:40px;display:flex;align-items:center;justify-content:center;font-size:17px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.12)}}
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
   table.idx,table.idx tbody,table.idx tr,table.idx td{{display:block}}
   table.idx thead{{display:none}}
   table.idx tr{{border:1px solid var(--line);border-radius:8px;margin:0 0 10px;padding:8px 12px}}
   table.idx td{{border:none;padding:2px 0}}
   table.idx td[data-label]::before{{content:attr(data-label) ": ";color:var(--muted);font-size:12px;margin-right:4px}}
   table.idx td:first-child{{font-weight:600}}
 }}
/*PYGMENTS*/
</style></head><body><button id="yr-menu" aria-label="目录">☰</button><button id="yr-theme" aria-label="切换主题">🌙</button><div id="yr-backdrop"></div><div id="yr-zoom"><div class="yr-zoom-inner"></div></div><div class="wrap"><nav>{nav}</nav><main>{body}</main></div>{scripts}</body></html>"""


# Sidebar drawer toggle plus click-to-zoom for diagrams and images.
LAYOUT_JS = """<script>
(function(){
  // Theme toggle: persists to localStorage, overrides the system preference.
  // Mermaid diagrams bake their theme at render time, so toggling with a
  // diagram on the page reloads to re-render them.
  var tb=document.getElementById('yr-theme'), root=document.documentElement;
  if(tb){
    tb.textContent = root.classList.contains('dark') ? '☀️' : '🌙';
    tb.addEventListener('click',function(){
      var t = root.classList.contains('dark') ? 'light' : 'dark';
      root.classList.toggle('dark', t==='dark');
      root.classList.toggle('light', t==='light');
      try{localStorage.setItem('yr-theme',t);}catch(e){}
      if(document.querySelector('.mermaid')) location.reload();
      else tb.textContent = t==='dark' ? '☀️' : '🌙';
    });
  }
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
  // Index filter: hide table rows and sidebar links that don't match.
  var f=document.getElementById('yr-filter');
  if(f){
    f.addEventListener('input',function(){
      var q=f.value.toLowerCase();
      document.querySelectorAll('table.idx tr, nav a.page').forEach(function(el){
        if(el.querySelector('th')) return;
        el.style.display = el.textContent.toLowerCase().indexOf(q)>=0 ? '' : 'none';
      });
    });
  }
})();
</script>"""


def build_nav(pages, active, prefix="", on_home=False):
    home_cls = " active" if on_home else ""
    out = ['<h1>Wiki</h1>', f'<a class="page{home_cls}" href="{prefix}/">◈ Profile</a>']
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
        out.append(f'<a class="page{cls}" href="{prefix}/p/{quote(p["slug"])}">{p["title"]}{lv}</a>')
    return "\n".join(out)


def render_body(md_text: str, slugs: set, repo: Path | None, prefix: str = ""):
    """Markdown -> (page html, toc html). The toc extension also ids every heading
    so sections are linkable; the returned toc feeds the sidebar's on-this-page block."""
    # mermaid fences -> <div class="mermaid"> before markdown so they survive as HTML
    def mm(m): return f'<div class="mermaid">\n{m.group(1)}\n</div>'
    md_text = re.sub(r"```mermaid\s*\n(.*?)```", mm, md_text, flags=re.DOTALL)
    md = markdown.Markdown(
        extensions=["fenced_code", "tables", "sane_lists", "codehilite", "toc"],
        extension_configs={"codehilite": {"guess_lang": False},
                           "toc": {"toc_depth": "2-4"}})  # h1 stays out: it's the page title
    html = md.convert(md_text)
    # rewrite inter-page links: href="slug" or "slug.md" -> "{prefix}/p/slug"
    def link(m):
        href = m.group(1); base = href[:-3] if href.endswith(".md") else href
        anchor = ""
        if "#" in base and base.split("#")[0] in slugs:
            base, anchor = base.split("#", 1); anchor = "#" + anchor
        if base in slugs:
            return f'href="{prefix}/p/{quote(base)}{anchor}"'
        if repo and not href.startswith(("http", "/p/", f"{prefix}/p/")):  # source citation -> repo file
            return f'href="{prefix}/src/{href}"'
        return m.group(0)
    return re.sub(r'href="([^"]+)"', link, html), md.toc


def render_page(title: str, nav: str, body: str) -> str:
    html = PAGE.format(title=title, nav=nav, body=body, scripts=LAYOUT_JS)
    return html.replace("/*PYGMENTS*/", PYGMENTS_CSS)


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


class Site:
    """One mounted wiki: parsed wiki.json, its source repo, and the rendered home page."""

    def __init__(self, wiki: Path, repo: Path | None = None, source: bool = True):
        meta = json.loads((wiki / "wiki.json").read_text(encoding="utf-8"))
        if meta.get("schema_version") != 2:
            raise SystemExit(f"unsupported wiki schema under {wiki}: expected schema_version 2")
        if not source:
            repo = None  # /src/ stays closed even if wiki.json records a source_root
        elif repo is None and meta.get("source_root"):
            recorded = Path(meta["source_root"])
            if recorded.is_dir():
                repo = recorded.resolve()
        self.wiki, self.repo = wiki, repo
        self.pages = meta["pages"]
        self.byslug = {p["slug"]: p for p in self.pages}
        self.name = Path(meta.get("source_root") or "").name or wiki.name or "Wiki"
        self.generated_at = str(meta.get("generated_at", ""))[:10]
        self.yread_version = meta.get("yread_version", "")
        self.language = meta.get("language", "")
        self.depth = meta.get("depth") or meta.get("doc_depth") or ""
        self.mode = meta.get("mode", "")
        self.home_body = build_profile_html(meta, repo, self.name)


def find_wikis(root: Path) -> dict[str, Path]:
    """Immediate children that are yread outputs — the child itself or its default
    `.yread/` output dir — keyed by project name."""
    found = {}
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        wiki = child if (child / "wiki.json").exists() else child / ".yread"
        if (wiki / "wiki.json").exists():
            found[child.name] = wiki
    return found


class Handler(BaseHTTPRequestHandler):
    sites: dict[str, Site]  # mount name -> wiki; single-wiki mode mounts as {"": site}
    site_mtimes: dict[str, float]  # mount name -> wiki.json mtime at load (multi mode)
    wiki_root: Path | None = None  # scanned directory (multi mode only)
    enable_source = False          # multi mode: resolve source_root and serve /src/
    multi = False

    def log_message(self, *a): pass

    def _send(self, body: bytes, ctype="text/html; charset=utf-8", code=200):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def _index(self):
        def row(name, site):
            return (f'<tr><td><a href="/w/{quote(name)}/">{name}</a></td>'
                    f'<td data-label="Lang">{site.language}</td>'
                    f'<td data-label="Depth">{site.depth}</td>'
                    f'<td data-label="Mode">{site.mode}</td>'
                    f'<td data-label="Version">{site.yread_version}</td>'
                    f'<td data-label="Pages">{len(site.pages)}</td>'
                    f'<td data-label="Generated">{site.generated_at}</td></tr>')
        ordered = sorted(self.sites.items(), key=lambda kv: kv[1].generated_at, reverse=True)
        rows = "".join(row(name, site) for name, site in ordered)
        body = ('<h1>Wikis</h1>\n'
                '<input id="yr-filter" type="search" placeholder="Filter projects\u2026" autocomplete="off">\n'
                '<table class="idx"><thead><tr><th>Project</th><th>Lang</th><th>Depth</th>'
                '<th>Mode</th><th>Version</th><th>Pages</th><th>Generated</th></tr></thead>'
                f'{rows}</table>')
        nav = '<h1>Wikis</h1>' + "".join(
            f'<a class="page" href="/w/{quote(name)}/">{name}</a>' for name in self.sites)
        return render_page("Wikis · yread", nav, body)

    def _serve(self, site: Site, prefix: str, path: str):
        if path == "/":
            if site.home_body is not None:
                nav = build_nav(site.pages, None, prefix, on_home=True)
                return self._send(render_page(f"{site.name} · Profile", nav, site.home_body).encode())
            if not site.pages:
                return self._send(b"no pages", code=404)
            # No reconstructable profile: fall back to the first page. Slugs keep
            # CJK/Unicode; HTTP headers are latin-1, so the target must be encoded.
            self.send_response(302)
            self.send_header("Location", f"{prefix}/p/" + quote(site.pages[0]["slug"]))
            self.end_headers(); return
        if path.startswith("/src/") and site.repo:
            f = safe_source_path(site.repo, path[len("/src/"):])
            if f:
                return self._send(f.read_text(encoding="utf-8", errors="replace").encode(), "text/plain; charset=utf-8")
            return self._send(b"not found", code=404)
        if path.startswith("/p/"):
            slug = unquote(path[len("/p/"):])  # browsers percent-encode CJK slugs
            p = site.byslug.get(slug)
            if not p:
                return self._send(b"page not found", code=404)
            md_text = (site.wiki / p["file"]).read_text(encoding="utf-8", errors="replace")
            body, toc = render_body(md_text, set(site.byslug), site.repo, prefix)
            nav = build_nav(site.pages, slug, prefix)
            if toc:
                nav += f'\n<div class="sec">On this page</div>\n{toc}'
            return self._send(render_page(p["title"], nav, body).encode())
        self._send(b"not found", code=404)

    def do_GET(self):
        path = self.path.split("?")[0]
        if not self.multi:
            return self._serve(self.sites[""], "", path)
        if path == "/":
            type(self).refresh()  # pick up uploaded/removed wikis without a restart
            return self._send(self._index().encode())
        m = re.match(r"^/w/([^/]+)(/.*)?$", path)
        if m and (site := self.sites.get(unquote(m.group(1)))):
            return self._serve(site, "/w/" + m.group(1), m.group(2) or "/")
        self._send(b"not found", code=404)

    @classmethod
    def refresh(cls):
        """Rescan wiki_root; reload a site only when its wiki.json changed. Builds a
        fresh dict before swapping so concurrent requests never see a half-update."""
        if cls.wiki_root is None:
            return
        sites, mtimes = dict(cls.sites), dict(cls.site_mtimes)
        found = find_wikis(cls.wiki_root)
        for name in list(sites):
            if name not in found:
                del sites[name]; mtimes.pop(name, None)
        for name, wiki in found.items():
            try:
                mtime = (wiki / "wiki.json").stat().st_mtime
            except OSError:
                continue
            if mtimes.get(name) == mtime:
                continue
            try:
                sites[name] = Site(wiki, source=cls.enable_source)
                mtimes[name] = mtime
            except SystemExit as e:
                print(f"skipping {wiki}: {e}", flush=True)
        cls.sites = dict(sorted(sites.items()))
        cls.site_mtimes = mtimes


def main(argv: list[str] | None = None):
    args = [a for a in (sys.argv[1:] if argv is None else argv)]
    host = "localhost"; port = 8000; repo = None; wiki_arg = None; enable_source = False
    i = 0
    while i < len(args):
        if args[i] == "--port": port = int(args[i + 1]); i += 2
        elif args[i] == "--host": host = args[i + 1]; i += 2
        elif args[i] == "--repo": repo = Path(args[i + 1]).resolve(); i += 2
        elif args[i] == "--enable-source": enable_source = True; i += 1
        else: wiki_arg = args[i]; i += 1
    root = Path(wiki_arg).resolve() if wiki_arg else Path(".yread").resolve()
    if (root / "wiki.json").exists():
        Handler.multi = False
        Handler.sites = {"": Site(root, repo)}
        desc = f"{len(Handler.sites[''].pages)} pages"
    else:
        if repo is not None:
            print("note: --repo is ignored when browsing multiple wikis", flush=True)
        Handler.multi = True
        Handler.wiki_root = root
        Handler.enable_source = enable_source
        Handler.sites, Handler.site_mtimes = {}, {}
        Handler.refresh()
        if not Handler.sites:
            raise SystemExit(f"no wiki.json under {root} or its subdirectories")
        desc = f"{len(Handler.sites)} projects"
        if not enable_source:
            print("note: /src/ source links are off in multi-wiki mode "
                  "(--enable-source to allow)", flush=True)
    url = f"http://{host}:{port}/"
    print(f"yread browser: {root}\n  {desc} -> {url}  (Ctrl-C to stop)", flush=True)
    try: webbrowser.open(url)
    except Exception: pass
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()

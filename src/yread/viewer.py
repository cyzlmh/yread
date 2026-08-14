"""Render yread Markdown and metadata as self-contained HTML pages."""
import html as html_lib
import re
from urllib.parse import quote

import markdown
import nh3
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound


# Pygments styles for the light/dark color schemes; injected into PAGE after
# .format() (its braces would clash with the format placeholders). The dark
# variant is scoped under :root.dark so the theme toggle controls it. The
# formatter's own .codehilite background rules are dropped: the block chrome
# comes from the page theme so highlighting blends into it.
def _pygments_css() -> str:
    light = HtmlFormatter(style="default").get_style_defs(".codehilite")
    dark = HtmlFormatter(style="github-dark").get_style_defs(".dark .codehilite")
    css = f"{light}\n{dark}"
    return re.sub(r"^(?:\.dark )?\.codehilite\s*\{[^}]*\}\n?", "", css, flags=re.MULTILINE)


PYGMENTS_CSS = _pygments_css()

HTML_CLEANER = nh3.Cleaner(
    tags={
        "a", "blockquote", "br", "button", "code", "del", "div", "em", "h1", "h2",
        "h3", "h4", "h5", "h6", "hr", "img", "li", "ol", "p", "pre",
        "span", "strong", "table", "tbody", "td", "th", "thead", "tr", "ul",
    },
    clean_content_tags={"embed", "iframe", "object", "script", "style", "template"},
    attributes={
        "a": {"href", "target", "title"},
        "blockquote": {"class"},
        "button": {"class", "type"},
        "code": {"class"},
        "div": {"class"},
        "h1": {"id"},
        "h2": {"id"},
        "h3": {"id"},
        "h4": {"id"},
        "h5": {"id"},
        "h6": {"id"},
        "img": {"alt", "src", "title"},
        "p": {"class"},
        "span": {"class"},
    },
    url_schemes={"http", "https", "mailto", "tel"},
)


PAGE = """<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · yread</title>
<script>(function(){{var t;try{{t=localStorage.getItem("yr-theme")}}catch(e){{}}
if(t!=="dark"&&t!=="light")t=window.matchMedia&&matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";
document.documentElement.classList.add(t);}})();</script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>mermaid.initialize({{startOnLoad:true, theme:document.documentElement.classList.contains("dark")?"dark":"neutral"}});</script>
<style>
/* Design tokens follow the VitePress default theme
   (github.com/vuejs/vitepress, src/client/theme-default/styles/vars.css):
   indigo brand, 3-level text grays, bg/bg-alt/bg-soft surfaces, and the
   green/purple/yellow/red palette for alert containers. */
 :root{{--fg:#3c3c43;--fg2:#67676c;--fg3:#929295;--bg:#fff;--bg-alt:#f6f6f7;--bg-soft:#f6f6f7;--bg-elv:#fff;--line:#e2e2e3;--border:#c2c2c4;--brand:#3451b2;--brand2:#3a5ccc;--brand-soft:#646cff24;--soft:#8e96aa24;--green:#18794e;--green-soft:#10b98124;--purple:#6f42c1;--purple-soft:#9f7aea24;--yellow:#915930;--yellow-soft:#eab30824;--red:#b8272c;--red-soft:#f43f5e24;--shadow:0 1px 2px #0000000a,0 1px 2px #0000000f}}
 :root.dark{{color-scheme:dark;--fg:#dfdfd6;--fg2:#98989f;--fg3:#6a6a71;--bg:#1b1b1f;--bg-alt:#161618;--bg-soft:#202127;--bg-elv:#202127;--line:#2e2e32;--border:#3c3f44;--brand:#a8b1ff;--brand2:#5c73e7;--brand-soft:#646cff29;--soft:#65758529;--green:#3dd68c;--green-soft:#10b98129;--purple:#c8abfa;--purple-soft:#9f7aea29;--yellow:#f9b44e;--yellow-soft:#eab30829;--red:#f66f81;--red-soft:#f43f5e29;--shadow:0 1px 2px #00000059}}
 *{{box-sizing:border-box}} html{{scroll-behavior:smooth}}
 @media (prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}}}
 body{{margin:0;font:16px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;color:var(--fg);background:var(--bg);text-rendering:optimizeLegibility;-webkit-font-smoothing:antialiased}}
 code,pre,.code-head{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace}}
 .wrap{{display:flex;min-height:100vh}}
 nav{{width:288px;flex:none;background:var(--bg-alt);border-right:1px solid var(--line);padding:20px 14px;overflow-y:auto;height:100vh;position:sticky;top:0;z-index:65}}
 nav h1{{font-size:13px;font-weight:600;margin:4px 8px 12px;color:var(--fg2);letter-spacing:.05em;text-transform:uppercase}}
 nav .sec{{font-weight:600;margin:18px 8px 6px;font-size:13px;color:var(--fg)}}
 nav .grp{{font-weight:600;margin:10px 8px 4px;font-size:12px;color:var(--fg3)}}
 nav a{{display:block;padding:5px 10px;border-radius:6px;color:var(--fg2);text-decoration:none;font-size:14px;transition:color .2s}}
 nav a:hover{{color:var(--fg)}} nav a.active{{background:var(--brand-soft);color:var(--brand);font-weight:600}}
 nav .lv{{display:inline-block;padding:1px 7px;border-radius:999px;background:var(--brand-soft);color:var(--brand);font-size:10.5px;font-weight:600;letter-spacing:.02em;margin-left:4px;vertical-align:1px}}
 main{{flex:1;min-width:0;padding:48px clamp(24px,5vw,72px) 72px;overflow-x:hidden}}
 article{{width:min(100%,46rem);margin:0 auto;overflow-wrap:break-word}}
 main a{{color:var(--brand);font-weight:500;text-decoration:none;transition:color .2s}} main a:hover{{color:var(--brand2);text-decoration:underline;text-underline-offset:2px}}
 main a:focus-visible,nav a:focus-visible,button:focus-visible{{outline:2px solid var(--brand);outline-offset:2px}}
 article p{{margin:1em 0}} article ul,article ol{{padding-left:1.4em}} article li{{margin:.4em 0}}
 article li::marker{{color:var(--fg3)}}
 pre{{max-width:100%;background:var(--bg-alt);color:var(--fg2);margin:16px 0;padding:16px 20px;border:0;border-radius:8px;overflow-x:auto;font-size:.875em;line-height:1.7;overflow-wrap:normal}}
 code{{background:var(--soft);color:var(--brand);padding:.2em .4em;border-radius:4px;font-size:.85em}}
 pre code{{background:none;color:inherit;padding:0;font-size:inherit;overflow-wrap:normal;white-space:pre}}
 .codehilite{{background:var(--bg-alt);border-radius:8px;margin:16px 0}}
 .codehilite pre{{margin:0;padding:14px 20px;background:none}}
 .code-head{{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:6px 8px 6px 20px;border-bottom:1px solid var(--line);font-size:12px;color:var(--fg2);letter-spacing:.03em;user-select:none}}
 .code-head span{{color:var(--brand);font-weight:500}}
 .code-copy{{border:0;background:none;color:var(--fg2);font:inherit;cursor:pointer;padding:3px 8px;border-radius:4px;transition:color .2s,background .2s}}
 .code-copy:hover{{color:var(--fg);background:var(--soft)}}
 table{{max-width:100%;border-collapse:collapse;margin:20px 0;display:block;overflow-x:auto;font-size:15px}}
 th,td{{border:1px solid var(--line);padding:8px 16px}} th{{background:var(--brand-soft);font-weight:600;text-align:left}}
 tbody tr:nth-child(even){{background:var(--bg-soft)}} tbody tr:hover{{background:var(--soft)}}
 .mermaid{{max-width:100%;overflow-x:auto;background:var(--bg-soft);border-radius:8px;padding:16px;margin:24px 0;text-align:center;cursor:zoom-in}}
 main img{{display:block;max-width:100%;height:auto;margin:24px auto;border-radius:8px;cursor:zoom-in}}
 article h1,article h2,article h3,article h4,article h5,article h6{{line-height:1.35;font-weight:600;scroll-margin-top:24px}}
 article h1{{font-size:2rem;letter-spacing:-.02em;margin:0 0 1em;background:linear-gradient(120deg,var(--brand),var(--purple));-webkit-background-clip:text;background-clip:text;color:transparent;-webkit-text-fill-color:transparent}}
 article h2{{font-size:1.5rem;letter-spacing:-.02em;border-top:1px solid var(--line);padding-top:24px;margin:48px 0 16px}}
 article h3{{font-size:1.25rem;letter-spacing:-.01em;margin:32px 0 12px}}
 article h4{{font-size:1rem;margin:24px 0 8px}}
 hr{{border:0;height:1px;margin:48px 0;background:linear-gradient(90deg,var(--brand-soft),var(--line) 40%,transparent)}}
 ::selection{{background:var(--brand-soft)}}
 blockquote{{border-left:2px solid var(--brand-soft);margin:16px 0;padding-left:16px;color:var(--fg2)}}
 blockquote.alert{{border:1px solid transparent;border-radius:8px;padding:12px 16px;color:var(--fg)}}
 .alert-title{{margin:0 0 4px;font-weight:600;font-size:.92em}}
 .alert p{{margin:.4em 0}}
 .alert p:empty{{display:none}}
 .alert a{{color:inherit;font-weight:600;text-decoration:underline;text-underline-offset:2px}}
 .alert-note{{background:var(--brand-soft)}} .alert-note .alert-title{{color:var(--brand)}}
 .alert-tip{{background:var(--green-soft)}} .alert-tip .alert-title{{color:var(--green)}}
 .alert-important{{background:var(--purple-soft)}} .alert-important .alert-title{{color:var(--purple)}}
 .alert-warning{{background:var(--yellow-soft)}} .alert-warning .alert-title{{color:var(--yellow)}}
 .alert-caution{{background:var(--red-soft)}} .alert-caution .alert-title{{color:var(--red)}}
 blockquote>:first-child{{margin-top:0}} blockquote>:last-child{{margin-bottom:0}}
 .sources{{font-size:.82em;line-height:1.55;color:var(--fg2);margin:1.4em 0 2em}}
 .sources a{{color:var(--fg2);text-decoration:underline;text-decoration-color:var(--line);text-underline-offset:2px}}
 .pager{{display:flex;justify-content:space-between;gap:16px;margin-top:64px;padding-top:24px;border-top:1px solid var(--line)}}
 .pager a{{display:flex;flex:1;flex-direction:column;padding:12px 16px;border:1px solid var(--line);border-radius:8px;line-height:1.4;transition:border-color .2s,background .2s}}
 .pager a:hover{{text-decoration:none;border-color:var(--brand);background:var(--bg-soft)}} .pager a:hover .pager-title{{color:var(--brand)}} .pager-next{{text-align:right;align-items:flex-end}}
 .pager-label{{font-size:12px;color:var(--fg2);margin-bottom:4px}} .pager-title{{color:var(--fg);font-size:14px;font-weight:500}}
 nav .toc{{padding:0 6px 12px}}
 nav .toc ul{{list-style:none;margin:0;padding-left:12px}}
 nav .toc>ul{{padding-left:6px}}
 nav .toc a{{font-size:13px;color:var(--fg2);padding:2px 10px}}
 nav .toc a:hover{{color:var(--brand)}}
 #yr-menu{{display:none;position:fixed;top:12px;left:12px;z-index:80;width:40px;height:40px;align-items:center;justify-content:center;font-size:20px;border:1px solid var(--border);border-radius:8px;background:var(--bg-elv);color:var(--fg);cursor:pointer;box-shadow:var(--shadow)}}
 #yr-theme{{position:fixed;top:12px;right:12px;z-index:80;width:40px;height:40px;display:flex;align-items:center;justify-content:center;font-size:17px;border:1px solid var(--border);border-radius:8px;background:var(--bg-elv);color:var(--fg);cursor:pointer;box-shadow:var(--shadow)}}
 #yr-backdrop{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:64}}
 #yr-zoom{{display:none;position:fixed;inset:0;z-index:90;background:rgba(0,0,0,.82);align-items:center;justify-content:center;padding:24px;cursor:zoom-out}}
 #yr-zoom .yr-zoom-inner{{background:var(--bg-elv);border-radius:10px;padding:18px;max-width:96vw;max-height:94vh;overflow:auto}}
 #yr-zoom .yr-zoom-inner svg{{width:min(1400px,90vw);height:auto;max-width:none}}
 #yr-zoom .yr-zoom-inner img{{max-width:90vw;max-height:88vh}}
 @media (max-width:960px){{
   nav{{width:min(288px,88vw);position:fixed;left:0;top:0;height:100vh;height:100dvh;padding-bottom:calc(20px + env(safe-area-inset-bottom));transform:translateX(-100%);transition:transform .22s ease;box-shadow:var(--shadow)}}
   body.sb-open nav{{transform:translateX(0)}}
   body.sb-open #yr-backdrop{{display:block}}
   #yr-menu{{display:flex}}
   nav a{{min-height:44px;display:flex;align-items:center}}
   nav .toc a{{min-height:36px}}
   main{{padding:72px 18px 48px;max-width:100%}}
   article h1{{font-size:1.75rem}}
   article h2{{font-size:1.35rem;margin-top:40px;padding-top:20px}}
   .pager{{gap:10px;margin-top:48px}} .pager a{{padding:11px 12px}}
 }}
/*PYGMENTS*/
</style></head><body><button id="yr-menu" aria-label="目录">☰</button><button id="yr-theme" aria-label="切换主题">🌙</button><div id="yr-backdrop"></div><div id="yr-zoom"><div class="yr-zoom-inner"></div></div><div class="wrap"><nav>{nav}</nav><main><article>{body}</article></main></div>{scripts}</body></html>"""


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
  // Code blocks: copy button (the header bar itself is rendered server-side).
  document.addEventListener('click',function(e){
    var btn=e.target.closest('.code-copy'); if(!btn) return;
    var box=btn.closest('.codehilite'), pre=box&&box.querySelector('pre');
    if(!pre) return;
    var txt=pre.innerText;
    function done(){btn.textContent='Copied';setTimeout(function(){btn.textContent='Copy';},1600);}
    if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(txt).then(done,fallback);}
    else fallback();
    function fallback(){
      var ta=document.createElement('textarea'); ta.value=txt; ta.style.position='fixed'; ta.style.opacity='0';
      document.body.appendChild(ta); ta.select();
      try{document.execCommand('copy');done();}catch(x){}
      document.body.removeChild(ta);
    }
  });
  // Instant navigation: swap the sidebar and article in place instead of doing
  // a full page load for same-site .html links. Falls back to a normal load on
  // any failure (e.g. file:// previews where fetch is unavailable).
  function navigate(href,push){
    fetch(href,{credentials:'same-origin'}).then(function(r){
      if(!r.ok) throw new Error(r.status); return r.text();
    }).then(function(txt){
      var doc=new DOMParser().parseFromString(txt,'text/html');
      var newNav=doc.querySelector('nav'), newArt=doc.querySelector('main article');
      if(!newNav||!newArt) throw new Error('unexpected page');
      nav.innerHTML=newNav.innerHTML;
      var art=document.querySelector('main article');
      art.innerHTML=newArt.innerHTML;
      document.title=doc.title;
      if(push) history.pushState(null,'',href);
      var mms=art.querySelectorAll('.mermaid');
      if(mms.length&&window.mermaid) mermaid.run({nodes:Array.prototype.slice.call(mms)});
      var u=new URL(href,location.href);
      var el=u.hash&&document.getElementById(decodeURIComponent(u.hash.slice(1)));
      if(el) el.scrollIntoView(); else window.scrollTo(0,0);
    }).catch(function(){location.href=href;});
  }
  document.addEventListener('click',function(e){
    if(e.defaultPrevented||e.button!==0||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey) return;
    var a=e.target.closest('a[href]');
    if(!a||a.target) return;
    var u=new URL(a.href,location.href);
    if(u.origin!==location.origin||!/\\.html?$/.test(u.pathname)) return;
    if(u.pathname===location.pathname) return; // same-page anchor: default smooth scroll
    e.preventDefault();
    document.body.classList.remove('sb-open');
    navigate(u.href,true);
  });
  window.addEventListener('popstate',function(){navigate(location.href,false);});
})();
</script>"""


def build_nav(
    pages,
    active,
    on_home=False,
    page_href=None,
    home_href=None,
    hub_href=None,
    toc=None,
    language="en",
):
    home_cls = " active" if on_home else ""
    home = html_lib.escape(
        home_href if home_href is not None else "index.html", quote=True
    )
    out = []
    if hub_href is not None:
        out.append(
            f'<a class="hub" href="{html_lib.escape(hub_href, quote=True)}">'
            "← All projects</a>"
        )
    out.extend(
        ['<h1>Wiki</h1>', f'<a class="page{home_cls}" href="{home}">◈ Profile</a>']
    )
    if toc:
        toc_label = (
            "本页目录"
            if str(language).lower().startswith("zh")
            else "On this page"
        )
        out.extend([f'<div class="sec">{toc_label}</div>', toc])
    last_sec, last_grp = None, None
    for p in pages:
        if p.get("section") != last_sec:
            last_sec = p.get("section"); last_grp = None
            out.append(
                f'<div class="sec">{html_lib.escape(str(last_sec or ""))}</div>'
            )
        grp = p.get("group")
        if grp and grp != last_grp:
            last_grp = grp
            out.append(f'<div class="grp">{html_lib.escape(str(grp))}</div>')
        cls = " active" if p["slug"] == active else ""
        meta = p.get("kind") or p.get("level", "")
        lv = (
            f'<span class="lv">{html_lib.escape(str(meta))}</span>' if meta else ""
        )
        href = page_href(p["slug"]) if page_href else f'{quote(p["slug"])}.html'
        out.append(
            f'<a class="page{cls}" href="{html_lib.escape(href, quote=True)}">'
            f'{html_lib.escape(str(p["title"]))}{lv}</a>'
        )
    return "\n".join(out)


def build_pager(pages, active, page_href=None, language="en") -> str:
    """Return links to the pages adjacent to ``active`` in catalog order."""
    index = next((i for i, page in enumerate(pages) if page["slug"] == active), None)
    if index is None:
        return ""
    zh = str(language).lower().startswith("zh")

    def link(page, css_class, label):
        href = (
            page_href(page["slug"])
            if page_href
            else f'{quote(page["slug"])}.html'
        )
        return (
            f'<a class="{css_class}" href="{html_lib.escape(href, quote=True)}">'
            f'<span class="pager-label">{label}</span>'
            f'<span class="pager-title">{html_lib.escape(page["title"])}</span></a>'
        )

    items = []
    if index > 0:
        items.append(
            link(pages[index - 1], "pager-prev", "← 上一篇" if zh else "← Previous")
        )
    else:
        items.append("<span></span>")
    if index + 1 < len(pages):
        items.append(
            link(pages[index + 1], "pager-next", "下一篇 →" if zh else "Next →")
        )
    else:
        items.append("<span></span>")
    return (
        '<div class="pager" aria-label="Page navigation">'
        + "".join(items)
        + "</div>"
    )


_SOURCE_PARAGRAPH_RE = re.compile(r"<p>Sources:\s*(.*?)</p>", re.DOTALL)
_SOURCE_LINK_RE = re.compile(r'<a href="([^"]+)">(.*?)</a>', re.DOTALL)
_GITHUB_PROJECT_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _style_source_paragraphs(document: str, source_project: str | None) -> str:
    """Style generated Sources paragraphs and make static source links useful."""
    github = bool(source_project and _GITHUB_PROJECT_RE.fullmatch(source_project))

    def paragraph(match):
        content = match.group(1)
        if github:
            def github_link(link_match):
                href = html_lib.unescape(link_match.group(1))
                if href.startswith(("http://", "https://")):
                    return link_match.group(0)
                path, marker, fragment = href.partition("#")
                kind = "tree" if path.endswith("/") else "blob"
                url = f"https://github.com/{source_project}/{kind}/HEAD/{quote(path, safe='/')}"
                if marker:
                    url += "#" + quote(fragment, safe="-_:.")
                return (
                    f'<a href="{html_lib.escape(url, quote=True)}" target="_blank" '
                    f'rel="noopener">{link_match.group(2)}</a>'
                )
            content = _SOURCE_LINK_RE.sub(github_link, content)
        elif source_project is not None:
            content = _SOURCE_LINK_RE.sub(lambda link: link.group(2), content)
        return f'<p class="sources">Sources: {content}</p>'

    return _SOURCE_PARAGRAPH_RE.sub(paragraph, document)


_ALERT_BLOCK_RE = re.compile(r"<blockquote>(.*?)</blockquote>", re.DOTALL)
_ALERT_MARKER_RE = re.compile(
    r"(<p>)\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(?:<br\s*/?>\s*)?",
    re.IGNORECASE,
)


def _style_alerts(document: str) -> str:
    """Turn GitHub-style ``> [!NOTE]`` blockquotes into colored alert boxes.
    Adjacent quotes merge into one <blockquote> in python-markdown, so split
    every marker paragraph into its own alert box."""

    def block(match):
        inner = match.group(1)
        if "<blockquote>" in inner:  # nested quotes: leave untouched
            return match.group(0)
        parts = _ALERT_MARKER_RE.split(inner)
        if len(parts) == 1:
            return match.group(0)
        out = []
        if parts[0].strip():
            out.append(f"<blockquote>{parts[0]}</blockquote>")
        for i in range(1, len(parts), 3):
            kind = parts[i + 1].lower()
            out.append(
                f'<blockquote class="alert alert-{kind}">'
                f'<p class="alert-title">{kind.capitalize()}</p>'
                f"<p>{parts[i + 2]}</blockquote>"
            )
        return "".join(out)

    return _ALERT_BLOCK_RE.sub(block, document)


def render_body(
    md_text: str,
    slugs: set,
    page_href=None,
    source_project: str | None = None,
):
    """Markdown -> (page html, toc html). The toc extension also ids every heading
    so sections are linkable; the returned toc feeds the sidebar's on-this-page block."""
    # Fences are handled before markdown runs so the output survives as raw HTML:
    # mermaid fences become live diagram divs, everything else is highlighted
    # here and wrapped with a language header + copy button.
    def fence(m):
        lang = (m.group(1) or "").lower()
        src = m.group(2)
        if lang == "mermaid":
            source = html_lib.escape(src, quote=False)
            return f'<div class="mermaid">\n{source}\n</div>'
        try:
            lexer = get_lexer_by_name(lang) if lang else TextLexer()
        except ClassNotFound:
            lexer = TextLexer()
        highlighted = highlight(src, lexer, HtmlFormatter(nowrap=True))
        label = html_lib.escape(lang)
        return (
            '<div class="codehilite"><div class="code-head">'
            f"<span>{label}</span>"
            '<button class="code-copy" type="button">Copy</button></div>'
            f"<pre>{highlighted}</pre></div>"
        )
    md_text = re.sub(r"```([A-Za-z][\w.+-]*)?\s*\n(.*?)```", fence, md_text, flags=re.DOTALL)
    md = markdown.Markdown(
        extensions=["fenced_code", "tables", "sane_lists", "toc"],
        extension_configs={"toc": {"toc_depth": "2-4"}})  # h1 stays out: it's the page title
    html = md.convert(md_text)
    # Rewrite inter-page links to the corresponding flat HTML file.
    def link(m):
        href = m.group(1)
        anchor = ""
        base = href
        if "#" in base:
            base, anchor = base.split("#", 1); anchor = "#" + anchor
        base = base[:-3] if base.endswith(".md") else base
        if base in slugs:
            target = page_href(base) if page_href else f"{quote(base)}.html"
            return f'href="{target}{anchor}"'
        return m.group(0)
    rendered = re.sub(r'href="([^"]+)"', link, html)
    return HTML_CLEANER.clean(
        _style_alerts(_style_source_paragraphs(rendered, source_project))
    ), md.toc


def render_page(title: str, nav: str, body: str, language="zh") -> str:
    lang = "zh" if str(language).lower().startswith("zh") else "en"
    html = PAGE.format(
        title=html_lib.escape(str(title)),
        lang=lang,
        nav=nav,
        body=body,
        scripts=LAYOUT_JS,
    )
    return html.replace("/*PYGMENTS*/", PYGMENTS_CSS)


def build_profile_html(meta: dict, name: str) -> str | None:
    """Render the wiki's project profile + run metadata as HTML for the home view,
    reusing the exact artifact-backed SUMMARY.md formatter. Returns None if the
    stored profile can't be reconstructed."""
    import dataclasses

    from . import core
    pf = meta.get("project_profile") or {}
    try:
        fields = {f.name for f in dataclasses.fields(core.ProjectProfile)}
        profile = core.ProjectProfile(**{k: v for k, v in pf.items() if k in fields})
    except (TypeError, ValueError):
        return None
    md = "\n".join(core._summary_profile_lines(meta, profile))
    rendered = markdown.markdown(md, extensions=["fenced_code", "tables", "sane_lists"])
    return (
        f"<h1>{html_lib.escape(str(name))} · Profile</h1>\n"
        f"{HTML_CLEANER.clean(rendered)}"
    )

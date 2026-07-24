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

# Select-to-explain: a reader selects text (a term or a passage) and gets an
# explanation from the LLM configured in ~/.yread/config.env. The instruction that
# drives the explanation is customizable — editable in the popover and remembered
# in the browser — with EXPLAIN_SYSTEM / DEFAULT_EXPLAIN_PROMPT as the built-in default.
LANG_NAMES = {"zh": "Chinese", "en": "English"}
MAX_SELECTION = 2000  # chars — a term or a paragraph, but not a whole page
EXPLAIN_SYSTEM = (
    "You explain a technical term or passage for a developer reading project "
    "documentation. Give a concise 2-4 sentence explanation of what it is and why it "
    "matters. Answer in {lang}. Plain prose; inline code for names/APIs is fine. Do "
    "not add a preamble or restate the selection as a heading."
)
DEFAULT_EXPLAIN_PROMPT = {
    "zh": "简明解释选中的内容（2-4 句）：它是什么、为什么重要。",
    "en": "Concisely explain the selection (2-4 sentences): what it is and why it matters.",
}


def default_explain_prompt(lang: str) -> str:
    return DEFAULT_EXPLAIN_PROMPT.get(lang, DEFAULT_EXPLAIN_PROMPT["en"])


def generate_explanation(client, model: str, lang: str, term: str, prompt: str = "") -> str:
    """Explain the selected ``term`` text. ``prompt`` is the (user-customizable)
    instruction; when empty the built-in default explanation prompt is used.
    Returns rendered HTML."""
    lang_name = LANG_NAMES.get(lang, lang or "English")
    if prompt:
        system = (f"{prompt}\n\nAnswer in {lang_name}. Be concise; plain prose, inline "
                  "code for names/APIs is fine; no preamble.")
    else:
        system = EXPLAIN_SYSTEM.format(lang=lang_name)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": term}]
    resp = client.chat.completions.create(model=model, messages=messages)
    text = (resp.choices[0].message.content or "").strip()
    return markdown.markdown(text, extensions=["fenced_code"])


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
 #yr-ebtn{{position:absolute;z-index:60;display:none;padding:3px 11px;font:12.5px/1.4 inherit;background:var(--accent);color:#fff;border:none;border-radius:6px;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.22)}}
 #yr-ebub{{position:absolute;z-index:61;display:none;width:360px;max-width:92vw;background:var(--bg);border:1px solid var(--line);border-radius:10px;box-shadow:0 8px 28px rgba(0,0,0,.18);padding:12px 14px;font-size:13.5px;line-height:1.6}}
 #yr-ebub .yr-ctx{{font-size:12px;color:var(--muted);background:var(--side);border-radius:6px;padding:5px 22px 5px 8px;margin:0 0 8px;max-height:52px;overflow:auto}}
 #yr-ebub .yr-body{{max-height:300px;overflow:auto}}
 #yr-ebub .yr-a p{{margin:.35em 0}} #yr-ebub .yr-a code{{font-size:85%}}
 #yr-ebub .yr-ask{{display:flex;gap:6px;margin-top:10px}}
 #yr-ebub .yr-ask input{{flex:1;min-width:0;border:1px solid var(--line);border-radius:6px;padding:5px 8px;font:inherit;font-size:12.5px;background:var(--bg);color:var(--fg)}}
 #yr-ebub .yr-ask button{{border:none;background:var(--accent);color:#fff;border-radius:6px;padding:0 11px;cursor:pointer;font-size:14px}}
 #yr-ebub .yr-x{{position:absolute;top:5px;right:9px;color:var(--muted);cursor:pointer;font-size:15px;line-height:1}}
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


EXPLAIN_ASSETS = """<button id="yr-ebtn">解释</button><div id="yr-ebub"></div>
<script>
(function(){
  if(!__ENABLED__) return;
  var main=document.querySelector('main');
  var btn=document.getElementById('yr-ebtn'), bub=document.getElementById('yr-ebub'), term='';
  function esc(s){return s.replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
  function hideBtn(){btn.style.display='none';}
  function hideBub(){bub.style.display='none';}
  function place(el,r){el.style.left=(window.scrollX+r.left)+'px';el.style.top=(window.scrollY+r.bottom+6)+'px';}
  function trySelect(){
    setTimeout(function(){
      var sel=window.getSelection(), t=(sel?sel.toString():'').trim();
      if(!t||t.length>2000||!sel.rangeCount){hideBtn();return;}
      var r=sel.getRangeAt(0).getBoundingClientRect();
      if(!r.width&&!r.height){hideBtn();return;}
      term=t; place(btn,r); btn.style.display='block';
    },10);
  }
  main.addEventListener('mouseup',trySelect);       // desktop
  main.addEventListener('touchend',trySelect);      // mobile: tap-drag select
  var selTimer=null;                                // mobile: handle bars often fire only selectionchange
  document.addEventListener('selectionchange',function(){clearTimeout(selTimer);selTimer=setTimeout(trySelect,300);});
  function dismiss(e){
    var hasSel=((window.getSelection()||'')+'').trim().length>0;
    if(e.target!==btn&&!bub.contains(e.target)) hideBub();
    // keep the button while a selection is live so the synthesized post-touch
    // mousedown doesn't hide what the user just selected
    if(e.target!==btn&&!hasSel) hideBtn();
  }
  document.addEventListener('mousedown',dismiss);
  document.addEventListener('touchstart',dismiss);
  document.addEventListener('keydown',function(e){if(e.key==='Escape'){hideBtn();hideBub();}});
  var LS='yr_explain_prompt';
  function savedPrompt(){ try{return localStorage.getItem(LS)||'';}catch(e){return '';} }
  function run(promptVal){
    var body=bub.querySelector('.yr-body'); body.innerHTML='<div class="yr-a">…</div>';
    var a=body.querySelector('.yr-a');
    fetch('/explain',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({term:term,prompt:promptVal||''})})
      .then(function(res){return res.json();})
      .then(function(d){a.innerHTML=d.html||esc(d.error||'（无结果）');})
      .catch(function(err){a.textContent='请求失败：'+err;});
  }
  btn.addEventListener('click',function(){
    var sel=window.getSelection();
    var r=(sel&&sel.rangeCount)?sel.getRangeAt(0).getBoundingClientRect():btn.getBoundingClientRect();
    var head=term.length>140?term.slice(0,140)+'…':term;
    var p=savedPrompt()||(window.YR_DEFAULT_PROMPT||'');
    bub.innerHTML='<span class="yr-x">×</span><div class="yr-ctx">'+esc(head)+'</div>'
      +'<div class="yr-body"></div>'
      +'<form class="yr-ask"><input class="yr-prompt" value="'+esc(p)+'" placeholder="解释提示词（可编辑，回车重新解释）" maxlength="1000"><button type="submit">解释</button></form>';
    place(bub,r); bub.style.display='block'; hideBtn();
    bub.querySelector('.yr-x').onclick=hideBub;
    var form=bub.querySelector('.yr-ask'), inp=form.querySelector('.yr-prompt');
    // editing the prompt + submitting re-runs and remembers it as the new default
    form.addEventListener('submit',function(e){e.preventDefault(); var v=inp.value.trim(); try{localStorage.setItem(LS,v);}catch(_){}; run(v);});
    run(p);            // auto-run with the current (saved or default) prompt
  });
})();
</script>"""


def explain_assets(enabled: bool) -> str:
    """The select-to-explain button/bubble markup + JS, with the feature flag
    baked in. When disabled the script returns early and no requests are made."""
    return EXPLAIN_ASSETS.replace("__ENABLED__", "true" if enabled else "false")


# Sidebar drawer toggle. Always runs (independent of select-to-explain) so the
# hamburger works on mobile even when no LLM is configured.
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


def page_scripts(explain_enabled: bool, lang: str = "en") -> str:
    """All page-level scripts: the injected default explain prompt, the always-on
    sidebar/zoom layout script, and the optional select-to-explain layer."""
    prompt_js = f"<script>window.YR_DEFAULT_PROMPT={json.dumps(default_explain_prompt(lang))};</script>"
    return prompt_js + LAYOUT_JS + explain_assets(explain_enabled)


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
    settings = None; client = None; lang = "en"; explain_cache: dict = {}
    home_body = None; home_title = "Wiki"

    def log_message(self, *a): pass

    def _send(self, body: bytes, ctype="text/html; charset=utf-8", code=200):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj: dict, code=200):
        self._send(json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8", code)

    def do_POST(self):
        if self.path.split("?")[0] != "/explain":
            return self._send_json({"error": "not found"}, code=404)
        if not (self.settings and self.client):
            return self._send_json({"error": "未配置模型（~/.yread/config.env）"})
        length = int(self.headers.get("Content-Length", "0") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            term = (payload.get("term") or "").strip()
            prompt = (payload.get("prompt") or "").strip()[:1000]
        except (ValueError, OSError):
            term, prompt = "", ""
        if not term or len(term) > MAX_SELECTION:
            return self._send_json({"error": "无效选区"})
        key = f"{prompt}\n##\n{term}".lower()
        cached = self.explain_cache.get(key)
        if cached is not None:
            return self._send_json({"html": cached})
        try:
            html = generate_explanation(self.client, self.settings.model, self.lang, term, prompt)
        except Exception as e:  # noqa: BLE001 — surface the failure to the bubble, don't crash the server
            return self._send_json({"error": f"生成失败：{e}"})
        self.explain_cache[key] = html
        self._send_json({"html": html})

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            if self.home_body is not None:
                html = PAGE.format(title=self.home_title,
                                   nav=build_nav(self.pages, None, on_home=True),
                                   body=self.home_body,
                                   scripts=page_scripts(bool(self.settings and self.client), self.lang))
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
                               scripts=page_scripts(bool(self.settings and self.client), self.lang))
            return self._send(html.encode())
        self._send(b"not found", code=404)


def main(argv: list[str] | None = None, settings=None):
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
    Handler.lang = meta.get("language", "en")
    Handler.explain_cache = {}
    Handler.home_title = f"{name} · Profile"
    Handler.home_body = build_profile_html(meta, repo, name)
    Handler.settings = settings
    if settings:
        from openai import OpenAI
        Handler.client = OpenAI(base_url=settings.base_url, api_key=settings.api_key,
                                max_retries=1, timeout=40)
    explain = " · select-to-explain on" if settings else ""
    url = f"http://{host}:{port}/"
    print(f"yread browser: {wiki}\n  {len(pages)} pages -> {url}{explain}  (Ctrl-C to stop)", flush=True)
    try: webbrowser.open(url)
    except Exception: pass
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()

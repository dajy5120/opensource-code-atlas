"""Static reading site for a study repository (`osca site build`).

Pages: an index (progress, analysis docs, annotated files), one page per annotated
source file (upstream code highlighted, 【zh】 blocks rendered as notes between the
lines, symbol outline with statuses), and one page per analysis document.
"""

from __future__ import annotations

import os
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from html import escape as h
from pathlib import Path

from markdown_it import MarkdownIt
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_for_filename
from pygments.token import STANDARD_TYPES
from pygments.util import ClassNotFound

from . import gitutil, status as status_mod
from .docs import FRONT
from .markers import comment_tokens, is_annotation
from .project import Project
from .symbols import annotation_body

STATUS_ZH = {"reviewed": "已审核", "translated": "已翻译", "stale": "过时", "pending": "待翻译"}
DOC_ZH = {"current": "最新", "new": "最新", "stale": "过时", "broken": "锚点失效", "unanchored": "无锚点"}

CSS = """
:root{--bg:#fbfaf7;--fg:#22211f;--muted:#6b6862;--line:#e7e3da;--card:#fff;--accent:#b4472b;
--note-bg:#fff6e8;--note-border:#e0a34a;--code-bg:#fff;--ln:#a8a39a;--ok:#2f7d4f;--info:#2d62a8;--warn:#b7791f;--idle:#9a958c}
@media (prefers-color-scheme:dark){:root{--bg:#161514;--fg:#e8e4dc;--muted:#a19c93;--line:#2c2a27;--card:#1d1c1a;
--accent:#e0805f;--note-bg:#2a2216;--note-border:#a77a35;--code-bg:#1b1a18;--ln:#5f5b55;--ok:#6cc08f;--info:#7aa7e6;--warn:#e0b25a;--idle:#77736c}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.7 -apple-system,"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",system-ui,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header.top{border-bottom:1px solid var(--line);padding:14px 20px;display:flex;gap:18px;align-items:baseline;flex-wrap:wrap}
header.top .brand{font-weight:700;color:var(--fg)}header.top .sub{color:var(--muted);font-size:14px}
main{max-width:1180px;margin:0 auto;padding:24px 20px 64px}
h1{font-size:28px;line-height:1.3;margin:8px 0 4px}h2{font-size:20px;margin:36px 0 12px;border-bottom:1px solid var(--line);padding-bottom:6px}
.lede{color:var(--muted);margin:0 0 20px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.stat b{display:block;font-size:24px;font-variant-numeric:tabular-nums}.stat span{color:var(--muted);font-size:13px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600}td.num{text-align:right;font-variant-numeric:tabular-nums}
.badge{display:inline-block;font-size:12px;line-height:1;padding:4px 7px;border-radius:999px;border:1px solid currentColor;white-space:nowrap}
.s-reviewed{color:var(--ok)}.s-translated{color:var(--info)}.s-stale{color:var(--warn)}.s-pending{color:var(--idle)}
.s-current,.s-new{color:var(--ok)}
nav.outline .dot.s-reviewed{background:var(--ok)}nav.outline .dot.s-translated{background:var(--info)}nav.outline .dot.s-stale{background:var(--warn)}nav.outline .dot.s-pending{background:var(--idle)}.s-broken{color:var(--accent)}.s-unanchored{color:var(--idle)}
.bar{height:6px;background:var(--line);border-radius:3px;overflow:hidden;min-width:80px}.bar i{display:block;height:100%;background:var(--info)}
.layout{display:grid;grid-template-columns:260px minmax(0,1fr);gap:28px}
nav.outline{position:sticky;top:12px;align-self:start;max-height:calc(100vh - 24px);overflow:auto;font-size:13px;border-right:1px solid var(--line);padding-right:12px}
nav.outline a{display:flex;gap:8px;align-items:center;color:var(--fg);padding:3px 0;word-break:break-all}
nav.outline .dot{width:8px;height:8px;border-radius:50%;flex:none;background:currentColor}
.code{background:var(--code-bg);border:1px solid var(--line);border-radius:10px;padding:10px 0;overflow-x:auto;font:13px/1.55 ui-monospace,"JetBrains Mono","SF Mono",Menlo,Consolas,monospace}
.line{display:flex;white-space:pre;min-width:max-content}.line:target{background:color-mix(in srgb,var(--note-border) 22%,transparent)}
.ln{flex:none;width:4.2em;text-align:right;padding-right:1em;color:var(--ln);user-select:none}.ln:hover{color:var(--accent);text-decoration:none}
.note{margin:6px 16px 6px 5.2em;padding:8px 12px;background:var(--note-bg);border-left:3px solid var(--note-border);border-radius:0 8px 8px 0;
font:14px/1.75 -apple-system,"PingFang SC","Noto Sans CJK SC","Microsoft YaHei",sans-serif;white-space:normal;max-width:78ch}
.note p{margin:0}.note p+p{margin-top:2px}.note code,.doc code{font:12.5px ui-monospace,Menlo,Consolas,monospace;background:color-mix(in srgb,var(--line) 70%,transparent);padding:1px 4px;border-radius:4px}
.doc{max-width:860px}.doc pre{background:var(--code-bg);border:1px solid var(--line);border-radius:8px;padding:12px;overflow-x:auto}.doc pre code{background:none;padding:0}
.doc pre.mermaid{background:var(--card);text-align:center}
.anchors{font-size:13px;color:var(--muted)}.anchors li{margin:2px 0}
footer{color:var(--muted);font-size:12px;text-align:center;padding:30px 0}
@media (max-width:900px){.layout{grid-template-columns:1fr}nav.outline{position:static;max-height:none;border-right:0;border-bottom:1px solid var(--line);padding-bottom:10px}
.note{margin-left:16px}main{padding:16px}}
"""

MERMAID = """<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
mermaid.initialize({startOnLoad:true,theme:matchMedia("(prefers-color-scheme: dark)").matches?"dark":"default"});
</script>"""


def _pygments_css() -> str:
    light = HtmlFormatter(style="default").get_style_defs(".code")
    dark = HtmlFormatter(style="github-dark").get_style_defs(".code")
    # backgrounds come from our own palette
    strip = lambda css: "\n".join(l for l in css.splitlines() if not re.match(r"^\.code\s*\{", l) and "pre {" not in l)  # noqa: E731
    return strip(light) + "\n@media (prefers-color-scheme:dark){\n" + strip(dark) + "\n}"


def _cls(ttype) -> str:
    while ttype not in STANDARD_TYPES:
        ttype = ttype.parent
    return STANDARD_TYPES[ttype]


def highlight_lines(path: str, code: str) -> list[str]:
    try:
        lexer = get_lexer_for_filename(path, stripnl=False, stripall=False, ensurenl=False)
    except ClassNotFound:
        lexer = TextLexer(stripnl=False, ensurenl=False)
    out: list[list[str]] = [[]]
    for ttype, value in lexer.get_tokens(code):
        cls = _cls(ttype)
        for n, part in enumerate(value.split("\n")):
            if n:
                out.append([])
            if part:
                out[-1].append(f'<span class="{cls}">{h(part)}</span>' if cls else h(part))
    return ["".join(x) for x in out]


def _paragraphs(lines: list[str]) -> list[str]:
    """Join wrapped annotation lines; break at blank lines and list items."""
    out: list[str] = []
    for line in lines:
        starts_item = bool(re.match(r"^(?:[-*•]|\d+[.、)])\s", line))
        if not line:
            out.append("")
        elif not out or not out[-1] or starts_item:
            out.append(line)
        else:
            sep = " " if re.search(r"[A-Za-z0-9`]$", out[-1]) and re.match(r"^[A-Za-z0-9`]", line) else ""
            out[-1] += sep + line
    return [p for p in out if p]


def _inline(text: str) -> str:
    return re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", h(text))


def _page(title: str, body: str, root: str, project: Project, gh: str | None, extra_head: str = "") -> str:
    name = project.raw.get("name", project.id)
    links = [f'<a href="{root}index.html">首页</a>']
    if gh:
        links.append(f'<a href="https://github.com/{gh}">GitHub</a>')
    links.append(f'<a href="{h(project.upstream_url)}">上游</a>')
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{h(title)}</title><link rel="stylesheet" href="{root}assets/site.css">{extra_head}</head>
<body><header class="top"><a class="brand" href="{root}index.html">{h(name)} · 中文源码学习版</a>
<span class="sub">OpenSource Code Atlas</span><span class="sub">{" · ".join(links)}</span></header>
<main>{body}</main>
<footer>由 osca 生成于 {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")} · 上游代码版权归原作者所有，中文注释为学习用途</footer>
</body></html>"""


def _gh_repo(project: Project) -> str | None:
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    url = gitutil.git(project.root, "remote", "get-url", "origin", check=False).strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else None


def _rel_root(page: str) -> str:
    return "../" * page.count("/")


def render_source(project: Project, path: str, rows: list[status_mod.Row], gh: str | None) -> str:
    text = (project.root / path).read_text(encoding="utf-8")
    lines = text.split("\n")
    tokens = comment_tokens(path) or ()
    zh = {i for i, line in enumerate(lines) if tokens and is_annotation(line, tokens, project.marker)}
    code_rows = [i for i in range(len(lines)) if i not in zh]
    hl = highlight_lines(path, "\n".join(lines[i] for i in code_rows))
    hl += [""] * (len(code_rows) - len(hl))
    pos = {r: k for k, r in enumerate(code_rows)}
    page = f"src/{path}.html"
    blob = f"https://github.com/{gh}/blob/{project.study_branch}/{path}" if gh else None

    out, i = [], 0
    while i < len(lines):
        if i in zh:
            j = i
            while j < len(lines) and j in zh:
                j += 1
            paras = _paragraphs([annotation_body(lines[k], project.marker) for k in range(i, j)])
            out.append('<div class="note">' + "".join(f"<p>{_inline(p)}</p>" for p in paras) + "</div>")
            i = j
            continue
        n = i + 1
        out.append(f'<div class="line" id="L{n}"><a class="ln" href="#L{n}">{n}</a><span>{hl[pos[i]] or " "}</span></div>')
        i += 1

    visible = sorted((r for r in rows if r.sym.translatable or r.status != "pending"), key=lambda r: r.sym.item_row)
    outline = "".join(
        f'<a href="#L{r.sym.item_row + 1}" title="{STATUS_ZH[r.status]}"><span class="dot s-{r.status}"></span>'
        f'<span>{h(r.sym.qualname)}</span></a>'
        for r in visible
    )
    done = sum(1 for r in visible if r.status != "pending")
    head = (
        f"<h1>{h(path)}</h1><p class=\"lede\">{done} / {len(visible)} 个符号已注释"
        + (f" · <a href=\"{blob}\">在 GitHub 查看</a>" if blob else "")
        + " · 图例：<span class=\"s-reviewed\">● 已审核</span> <span class=\"s-translated\">● 已翻译</span>"
        " <span class=\"s-stale\">● 过时</span> <span class=\"s-pending\">● 待翻译</span></p>"
    )
    body = f'{head}<div class="layout"><nav class="outline">{outline}</nav><div class="code">{"".join(out)}</div></div>'
    return _page(path, body, _rel_root(page), project, gh)


def render_doc(project: Project, ds, line_of: dict[str, int], annotated: set[str], gh: str | None) -> str:
    doc = ds.doc
    text = (project.root / doc.path).read_text(encoding="utf-8")
    m = FRONT.match(text)
    md = text[m.end():] if m else text
    html = MarkdownIt("commonmark", {"html": False}).enable("table").render(md)
    has_mermaid = 'class="language-mermaid"' in html
    html = re.sub(r'<pre><code class="language-mermaid">(.*?)</code></pre>', r'<pre class="mermaid">\1</pre>', html, flags=re.S)
    page = "docs/" + doc.path.removeprefix("osca/docs/").removesuffix(".md") + ".html"
    root = _rel_root(page)
    items = []
    for a in doc.anchors:
        path = a.split("#", 1)[0]
        target = f"{root}src/{path}.html" + (f"#L{line_of[a]}" if a in line_of else "") if path in annotated else (
            f"https://github.com/{gh}/blob/{project.study_branch}/{path}" if gh else None
        )
        mark = " ⚠" if a in ds.changed or a in ds.missing else ""
        label = h(a.split("#", 1)[1] if "#" in a else a)
        items.append(f'<li><a href="{target}">{label}</a> <span>{h(path)}</span>{mark}</li>' if target else f"<li>{label}{mark}</li>")
    meta = (
        f'<p class="lede"><span class="badge s-{ds.status}">{DOC_ZH[ds.status]}</span> '
        f"· 写作状态：{h(str(doc.meta.get('status', 'draft')))}</p>"
    )
    anchors = f'<details class="anchors"><summary>源码锚点（{len(doc.anchors)}）</summary><ul>{"".join(items)}</ul></details>' if items else ""
    body = f'<div class="doc">{meta}{anchors}{html}</div>'
    return _page(doc.title, body, root, project, gh, MERMAID if has_mermaid else "")


def render_index(project: Project, st: status_mod.Status, data: dict, files: dict[str, list], gh: str | None) -> str:
    s = data["symbols"]
    stats = [
        (data["anchor"]["ref"] or data["anchor"]["commit"][:9], "对应上游版本"),
        (f"{data['coverage']:.1%}", f"注释覆盖率（{s['in_scope'] - s['pending']:,} / {s['in_scope']:,} 符号）"),
        (f"{s['reviewed']:,}", "已人工审核的符号"),
        (f"{s['stale']:,}", "等待复核（上游已改动）"),
        (f"{data['zh_lines']:,}", "中文注释行"),
        (f"{len(st.docs)}", "架构分析文档"),
    ]
    cards = "".join(f'<div class="stat"><b>{h(str(v))}</b><span>{h(t)}</span></div>' for v, t in stats)
    docs_rows = "".join(
        f'<tr><td><a href="docs/{d.doc.path.removeprefix("osca/docs/").removesuffix(".md")}.html">{h(d.doc.title)}</a></td>'
        f'<td><span class="badge s-{d.status}">{DOC_ZH[d.status]}</span></td><td class="num">{len(d.doc.anchors)}</td></tr>'
        for d in st.docs
    )
    groups: dict[str, list[str]] = defaultdict(list)
    for path in files:
        groups[status_mod.group_of(path, 3)].append(path)
    file_html = []
    for g in sorted(groups):
        trs = []
        for path in sorted(groups[g]):
            rows = [r for r in files[path] if r.sym.translatable or r.status != "pending"]
            c = defaultdict(int)
            for r in rows:
                c[r.status] += 1
            done = len(rows) - c["pending"]
            pct = done / len(rows) if rows else 0
            trs.append(
                f'<tr><td><a href="src/{h(path)}.html">{h(path.removeprefix(g + "/"))}</a></td>'
                f'<td><div class="bar"><i style="width:{pct:.0%}"></i></div></td><td class="num">{done} / {len(rows)}</td>'
                f'<td class="num s-reviewed">{c["reviewed"] or ""}</td><td class="num s-stale">{c["stale"] or ""}</td></tr>'
            )
        file_html.append(
            f"<h3>{h(g)}</h3><table><tr><th>文件</th><th>进度</th><th class=num>已注释</th>"
            f"<th class=num>已审核</th><th class=num>过时</th></tr>{''.join(trs)}</table>"
        )
    name = project.raw.get("name", project.id)
    body = (
        f"<h1>{h(name)} 中文源码学习版</h1>"
        f'<p class="lede">上游源码保持原样，中文注释以 【zh】 标记写在源码中，并随上游版本持续更新。'
        f'上游：<a href="{h(project.upstream_url)}">{h(project.upstream_url)}</a></p>'
        f'<div class="stats">{cards}</div>'
        + (f"<h2>架构分析</h2><table><tr><th>文档</th><th>状态</th><th class=num>锚点</th></tr>{docs_rows}</table>" if docs_rows else "")
        + f"<h2>已注释的源码（{len(files)} 个文件）</h2>"
        + "".join(file_html)
    )
    return _page(f"{name} 中文源码学习版", body, "", project, gh)


def build(project: Project, out: Path) -> dict[str, int]:
    st = status_mod.compute(project)
    data = status_mod.to_json(project, st)
    gh = _gh_repo(project)
    by_path: dict[str, list] = defaultdict(list)
    for r in st.rows:
        by_path[r.sym.path].append(r)
    files = {p: rows for p, rows in by_path.items() if any(r.status != "pending" for r in rows)}
    line_of = {r.sym.id: r.sym.item_row + 1 for r in st.rows}

    if out.exists():
        shutil.rmtree(out)
    (out / "assets").mkdir(parents=True)
    (out / "assets" / "site.css").write_text(CSS + _pygments_css(), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    (out / "index.html").write_text(render_index(project, st, data, files, gh), encoding="utf-8")
    for path, rows in files.items():
        p = out / "src" / f"{path}.html"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_source(project, path, rows, gh), encoding="utf-8")
    for ds in st.docs:
        p = out / "docs" / (ds.doc.path.removeprefix("osca/docs/").removesuffix(".md") + ".html")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_doc(project, ds, line_of, set(files), gh), encoding="utf-8")
    return {"files": len(files), "docs": len(st.docs)}

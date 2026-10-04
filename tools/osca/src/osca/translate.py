"""AI translation pipeline: structured annotation patches (ADR 0005).

The model never edits files. It receives the file with line numbers plus a list
of target symbols and returns JSON: for each symbol, Chinese text lines and
where they go ("doc" = the symbol's documentation slot, or "line" = before a
given line inside the symbol). osca renders the comment syntax and the 【zh】
marker itself, replaces the symbol's previous annotation, and keeps the result
only if the strip invariant and placement lint still hold for the file.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import gitutil, state, status
from .project import Project, load_sync
from .symbols import MODULE, FileIndex, Symbol, annotation_body, index_text
from .terms import relevant
from .verify import verify

DEFAULT_BACKEND = "claude-code"  # subscription quota via `claude -p`; "api" = Anthropic API key
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "medium"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 64000

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "annotations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "placement": {"type": "string", "enum": ["doc", "line"]},
                    "line": {"type": "integer"},
                    "text": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["symbol", "placement", "line", "text"],
                "additionalProperties": False,
            },
        },
        "unchanged": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": ["annotations", "unchanged", "notes"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """你是 OpenSource Code Atlas（OSCA）的源码注释作者，为经典开源项目的源码撰写中文学习注释。
读者是有经验、想深入理解这份源码的中文开发者。

# 你的产出

只为“目标符号”列表中的符号撰写注释，以 JSON 返回（结构由 schema 约束）。你不修改代码，也不输出注释符号或标记：
`text` 中每个元素是一行纯中文说明，工具会自动加上 `///`、`//` 或 `#` 以及 【zh】 标记并插入源码。

每条注释（annotations 的一个元素）：
- `symbol`：目标符号 ID，必须原样取自目标列表。
- `placement`：
  - `"doc"`：符号的整体说明，工具会放在该符号的文档位置（英文文档之后、属性之前；没有英文文档时放在符号上方）。`line` 填 0。
  - `"line"`：放在第 `line` 行**之前**，用于解释函数体内某段关键逻辑。`line` 必须是该符号范围内、一行代码（不是空行或已有的中文注释）的行号。
- `text`：中文行，每行不超过约 60 个汉字；一条注释一般 1～4 行，一个符号合计一般不超过 8 行。

一个符号可以有多条注释（通常一条 "doc"，复杂函数再加一两条 "line"）。对某个符号返回注释时，
**它原有的中文注释会被全部替换**——所以修订时要返回完整的新版本，而不仅是新增的部分。

# 写什么

- 翻译 + 讲解，而不是逐句直译：说明这段代码为什么这样写、在系统中处于什么位置、有哪些不变式和边界情况。
- 不复述代码（不要写“i 加 1”这类注释）；英文文档已经说清楚的事实可以简要转述，重点放在英文没写的“为什么”。
- 只陈述能从给出的源码中确认的事实。没有把握的推断写成“推测：……”，并在 `notes` 中说明。
- 术语以给出的术语表为准：首次出现写成“中文（English）”，之后可只写中文；标注 keep_english 的词保留英文；不要使用 avoid 中的译法。
- 代码标识符用反引号包裹；中英文之间加空格；使用中文标点。

# 过时的注释（状态为 stale 的符号）

会给出原有中文注释、哪类代码发生了变化（签名 / 英文文档 / 实现）以及上游 diff。
- 原注释已不准确或不完整：返回修订后的完整注释（尽量保留原有措辞，只改需要改的部分）。
- 原注释仍然完全正确：不要返回注释，把符号 ID 放进 `unchanged`，由人工确认。

# notes

用一两句话说明不确定之处或值得写进架构分析文档的设计要点；没有则为空字符串。"""


@dataclass
class Target:
    sym: Symbol
    status: str  # pending | stale
    changed: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)


@dataclass
class Job:
    path: str
    text: str
    targets: list[Target]
    diff: str = ""
    context: str = ""  # definitions of types used here but defined in other files

    @property
    def sha(self) -> str:
        return hashlib.sha1(self.text.encode()).hexdigest()


@dataclass
class Result:
    path: str
    applied: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)  # "symbol: reason"
    error: str = ""
    notes: str = ""
    usage: dict[str, int] = field(default_factory=dict)


# --- planning ------------------------------------------------------------------------------


def ai_config(project: Project) -> dict[str, Any]:
    cfg = dict(project.raw.get("ai") or {})
    return {
        "backend": cfg.get("backend", DEFAULT_BACKEND),
        "model": cfg.get("model", DEFAULT_MODEL),
        "effort": cfg.get("effort", DEFAULT_EFFORT),
        "max_symbols": int(cfg.get("max_symbols_per_request", 20)),
        "fallbacks": cfg.get("fallbacks", True),
    }


def _last_sync_range(project: Project) -> tuple[str, str] | None:
    hist = load_sync(project.root).get("history") or []
    if not hist:
        return None
    return hist[-1]["from_commit"], hist[-1]["to_commit"]


def plan(
    project: Project,
    paths: list[str] | None = None,
    which: tuple[str, ...] = ("pending", "stale"),
    max_symbols: int | None = None,
    limit_files: int | None = None,
    all_symbols: bool = False,
) -> list[Job]:
    st = status.compute(project, paths)
    per_file: dict[str, list[status.Row]] = {}
    for r in st.rows:
        if r.status not in which:
            continue
        if r.status == "pending" and not r.sym.translatable and not all_symbols:
            continue
        per_file.setdefault(r.sym.path, []).append(r)
    rng = _last_sync_range(project)
    from .context import related_definitions, type_index

    types = type_index(project) if per_file else {}
    size = max_symbols or ai_config(project)["max_symbols"]
    jobs: list[Job] = []
    for path in sorted(per_file)[: limit_files or None]:
        text = (project.root / path).read_text(encoding="utf-8")
        lines = text.split("\n")
        rows = sorted(per_file[path], key=lambda r: (r.status != "stale", r.sym.start))
        diff = ""
        if rng and any(r.status == "stale" for r in rows):
            diff = gitutil.git(project.root, "diff", "-U3", rng[0], rng[1], "--", path)[:20000]
        targets = [
            Target(
                r.sym,
                r.status,
                r.changed,
                [annotation_body(lines[i], project.marker) for i in r.sym.zh_rows],
            )
            for r in rows
        ]
        idx = index_text(path, text, project.marker, project.symbol_policy)
        context = related_definitions(project, idx, text, [t.sym for t in targets], types) if idx else ""
        for i in range(0, len(targets), size):
            jobs.append(Job(path, text, targets[i : i + size], diff, context))
    return jobs


# --- request -------------------------------------------------------------------------------

CHANGE_ZH = {"sig": "签名", "doc": "英文文档", "body": "实现"}


def render_file(job: Job, terms: dict[str, dict]) -> str:
    lines = job.text.split("\n")
    skip: dict[int, int] = {}  # start row -> end row of elided test code
    if _is_rust(job.path):
        from .lang.rust import test_ranges

        skip = {a: b for a, b in test_ranges(job.text.encode("utf-8")) if b - a >= 5}
    numbered, r = [], 0
    while r < len(lines):
        if r in skip:
            numbered.append(f"{'':>5}| … 第 {r + 1}–{skip[r] + 1} 行为测试代码，已省略 …")
            r = skip[r] + 1
            continue
        numbered.append(f"{r + 1:>5}| {lines[r]}")
        r += 1
    numbered = "\n".join(numbered)
    rel = relevant(terms, job.text)
    term_lines = [
        f"- {t['en']} → {t['zh']}"
        + (" (keep_english)" if t.get("keep_english") else "")
        + (f"；避免：{'、'.join(t['avoid'])}" if t.get("avoid") else "")
        + (f"；{t['note']}" if t.get("note") else "")
        for t in rel.values()
    ]
    out = (
        f"# 文件 `{job.path}`\n\n```\n{numbered}\n```\n\n"
        f"# 术语表\n\n" + ("\n".join(term_lines) or "（无）")
    )
    if job.context:
        out += (
            "\n\n# 相关类型定义（来自其他文件，仅供理解；不要为它们写注释）\n\n"
            f"```\n{job.context}\n```"
        )
    return out


def render_targets(job: Job) -> str:
    out = ["# 目标符号", ""]
    for t in job.targets:
        s = t.sym
        head = f"- `{s.id}` — {s.kind}，第 {s.start + 1}–{s.end + 1} 行"
        if t.status == "stale":
            head += f"，状态 stale（变化：{' / '.join(CHANGE_ZH[c] for c in t.changed)}）"
            out.append(head)
            out.append("  原有中文注释：")
            out += [f"    {line}" for line in t.existing] or ["    （无）"]
        else:
            out.append(head + ("，状态 pending" if not t.existing else "，已有注释（请修订）"))
    if job.diff:
        out += ["", "# 上游 diff（上次同步）", "", "```diff", job.diff, "```"]
    return "\n".join(out)


def request_params(project: Project, job: Job, terms: dict[str, dict], batch: bool = False) -> dict[str, Any]:
    cfg = ai_config(project)
    params: dict[str, Any] = {
        "model": cfg["model"],
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "messages": [
            {
                "role": "user",
                "content": [
                    # file + terms are shared by every chunk of the same file: cache them
                    {"type": "text", "text": render_file(job, terms), "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": render_targets(job)},
                ],
            }
        ],
        "output_config": {"effort": cfg["effort"], "format": {"type": "json_schema", "schema": SCHEMA}},
    }
    if cfg["fallbacks"] and not batch:  # server-side fallback is rejected on the Batches API
        params["betas"] = [FALLBACK_BETA]
        params["fallbacks"] = "default"
    return params


# --- applying ------------------------------------------------------------------------------


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _clean(text: str, marker: str) -> str:
    t = text.strip()
    for prefix in ("///", "//!", "//", "#"):
        if t.startswith(prefix):
            t = t[len(prefix) :].strip()
            break
    if t.startswith(marker):
        t = t[len(marker) :].strip()
    return t


def _is_rust(path: str) -> bool:
    return path.endswith(".rs")


def _token(path: str) -> str:
    """Line-comment token for non-Rust files: `#` (Python, Cython, …) or `//` (Go, TS, C++, …)."""
    from .markers import comment_tokens

    return "#" if comment_tokens(path) == ("#",) else "//"


def _doc_slot(path: str, lines: list[str], sym: Symbol, zh: set[int]) -> tuple[int, str, str]:
    """(insert-before row, comment token, indentation) for a symbol's documentation slot."""
    rust = _is_rust(path)
    if sym.qualname == MODULE:
        if rust:
            # inner docs (`//!`) never attach to items, so they are safe anywhere before the first item
            inner = [r for r, l in enumerate(lines) if l.startswith("//!") and r not in zh]
            if inner:
                return inner[-1] + 1, "//!", ""
            r = 0
            while r < len(lines) and lines[r].startswith("//") and not lines[r].startswith(("///", "//!")):
                r += 1  # skip the license header
            return r, "//!", ""
        # other languages: after the leading comment block (license / package doc) and, for
        # Python, the module docstring -- and only before a blank line, so it attaches to nothing
        tok = _token(path)
        r = 0
        while r < len(lines) and lines[r].lstrip().startswith((tok, "/*", "*", "*/") if tok == "//" else tok):
            r += 1
        if tok == "#" and r < len(lines) and lines[r].lstrip().startswith(('"""', "'''", 'r"""')):
            q = '"""' if '"""' in lines[r] else "'''"
            if lines[r].count(q) < 2:
                r += 1
                while r < len(lines) and q not in lines[r]:
                    r += 1
            r += 1
        while r < len(lines) and r in zh:
            r += 1
        if r < len(lines) and lines[r].strip():
            return -1, tok, ""  # would attach to the following statement
        return r, tok, ""
    ind = _indent(lines[sym.item_row])
    if rust:
        docs = [
            r
            for r in range(sym.start, sym.item_row)
            if r not in zh and lines[r].lstrip().startswith("///") and not lines[r].lstrip().startswith("////")
        ]
        if docs:
            return docs[-1] + 1, "///", ind
        return sym.start, "//", ind
    return sym.item_row, _token(path), ind


def _line_slot(path: str, lines: list[str], row: int, zh: set[int]) -> tuple[str, str]:
    """(comment token, indentation) for a block inserted before `row`."""
    ref = row
    while ref < len(lines) and (not lines[ref].strip() or ref in zh):
        ref += 1
    ind = _indent(lines[ref]) if ref < len(lines) else ""
    if not _is_rust(path):
        return _token(path), ind
    prev = row - 1
    while prev >= 0 and prev in zh:
        prev -= 1
    here = lines[row].lstrip() if row < len(lines) else ""
    before = lines[prev].lstrip() if prev >= 0 else ""
    if here.startswith("//!") or (before.startswith("//!") and not here.strip()):
        return "//!", ind
    if here.startswith("///") and before.startswith("///"):
        return "///", ind
    return "//", ind


def apply(project: Project, job: Job, data: dict[str, Any]) -> Result:
    """Apply one structured response to the working tree (all-or-nothing per file)."""
    res = Result(job.path, notes=str(data.get("notes", "")))
    full = project.root / job.path
    current = full.read_text(encoding="utf-8")
    if hashlib.sha1(current.encode()).hexdigest() != job.sha:
        res.error = "file changed since the request was built; re-run translate"
        return res
    idx: FileIndex | None = index_text(job.path, current, project.marker, project.symbol_policy)
    if idx is None:
        res.error = "unsupported file type"
        return res
    lines = current.split("\n")
    zh_all = set(idx.zh_rows)
    targets = {t.sym.id: t for t in job.targets}
    res.unchanged = [s for s in data.get("unchanged", []) if s in targets]

    by_sym: dict[str, list[dict]] = {}
    for a in data.get("annotations", []):
        sid = a.get("symbol", "")
        if sid not in targets:
            res.rejected.append(f"{sid}: not a target symbol")
            continue
        if sid not in idx.symbols:
            res.rejected.append(f"{sid}: symbol no longer exists")
            continue
        by_sym.setdefault(sid, []).append(a)

    delete: set[int] = set()
    inserts: list[tuple[int, int, list[str]]] = []  # (row, seq, rendered lines)
    seq = 0
    for sid, anns in by_sym.items():
        sym = idx.symbols[sid]
        planned: list[tuple[int, list[str]]] = []
        ok = True
        for a in anns:
            texts = [_clean(t, project.marker) for t in a.get("text", [])]
            texts = [t for t in texts if t] or []
            if not texts:
                continue
            if a.get("placement") == "doc":
                row, tok, ind = _doc_slot(job.path, lines, sym, zh_all)
                if row < 0:
                    res.rejected.append(f"{sid}: no safe module-level slot (add a module docstring first)")
                    ok = False
                    break
            else:
                row = int(a.get("line", 0)) - 1
                lo, hi = (0, len(lines) - 1) if sym.qualname == MODULE else (sym.start, sym.end)
                if not (lo <= row <= hi) or row in zh_all or not lines[row].strip():
                    res.rejected.append(f"{sid}: line {row + 1} is not a code line inside the symbol")
                    ok = False
                    break
                tok, ind = _line_slot(job.path, lines, row, zh_all)
            planned.append((row, [f"{ind}{tok} {project.marker} {t}" for t in texts]))
        if not ok or not planned:
            continue
        delete |= set(sym.zh_rows)
        for row, rendered in planned:
            inserts.append((row, seq, rendered))
            seq += 1
        res.applied.append(sid)

    if not res.applied:
        return res
    by_row: dict[int, list[str]] = {}
    for row, _, rendered in sorted(inserts):
        by_row.setdefault(row, []).extend(rendered)
    out: list[str] = []
    for r, line in enumerate(lines):
        out += by_row.get(r, [])
        if r not in delete:
            out.append(line)
    out += by_row.get(len(lines), [])
    new_text = "\n".join(out)

    full.write_text(new_text, encoding="utf-8")
    anchor = load_sync(project.root)["anchor"]["upstream_commit"]
    issues = verify(project.root, anchor, project.marker, [job.path], project.generated)
    if issues:
        full.write_text(current, encoding="utf-8")
        res.error = "; ".join(i.format() for i in issues)
        res.rejected += [f"{s}: file rolled back" for s in res.applied]
        res.applied = []
    return res


def record(project: Project, results: list[Result], by: str) -> None:
    """Record fresh state for files that changed (AI annotations count as translated, never reviewed)."""
    paths = {r.path for r in results if r.applied}
    if not paths:
        return
    from .index import index_worktree

    records = state.load(project.root)
    state.update(records, state.all_symbols(index_worktree(project, sorted(paths))), by, paths)
    state.save(project.root, records)


# --- calling the API ---------------------------------------------------------------------

Caller = Callable[[dict[str, Any]], tuple[dict[str, Any] | None, str, dict[str, int]]]


def _usage(msg: Any) -> dict[str, int]:
    u = getattr(msg, "usage", None)
    return {
        k: int(getattr(u, k, 0) or 0)
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    }


def parse_message(msg: Any) -> tuple[dict[str, Any] | None, str]:
    """(data, error) from a Messages API response."""
    if msg.stop_reason == "refusal":
        cat = getattr(getattr(msg, "stop_details", None), "category", None)
        return None, f"refused (category: {cat})"
    if msg.stop_reason == "max_tokens":
        return None, "hit max_tokens; use a smaller --max-symbols"
    text = next((b.text for b in msg.content if getattr(b, "type", "") == "text"), None)
    if text is None:
        return None, f"no text block (stop_reason={msg.stop_reason})"
    try:
        return json.loads(text), ""
    except json.JSONDecodeError as e:
        return None, f"invalid JSON: {e}"


def anthropic_caller() -> Caller:
    import anthropic

    client = anthropic.Anthropic()

    def call(params: dict[str, Any]) -> tuple[dict[str, Any] | None, str, dict[str, int]]:
        try:
            if "betas" in params:
                with client.beta.messages.stream(**params) as stream:
                    msg = stream.get_final_message()
            else:
                with client.messages.stream(**params) as stream:
                    msg = stream.get_final_message()
        except anthropic.RateLimitError as e:
            return None, f"rate limited: {e.message}", {}
        except anthropic.APIStatusError as e:
            return None, f"API error {e.status_code}: {e.message}", {}
        except anthropic.APIConnectionError as e:
            return None, f"connection error: {e}", {}
        data, err = parse_message(msg)
        return data, err, _usage(msg)

    return call


def refresh(project: Project, job: Job) -> Job:
    """Re-read the file so chunks after the first see earlier chunks' annotations."""
    text = (project.root / job.path).read_text(encoding="utf-8")
    if text == job.text:
        return job
    idx = index_text(job.path, text, project.marker, project.symbol_policy)
    lines = text.split("\n")
    targets = []
    for t in job.targets:
        sym = idx.symbols.get(t.sym.id) if idx else None
        if sym is not None:
            targets.append(Target(sym, t.status, t.changed, [annotation_body(lines[i], project.marker) for i in sym.zh_rows]))
    return Job(job.path, text, targets, job.diff, job.context)


def claude_code_caller(model: str, effort: str, timeout: int = 1200) -> Caller:
    """Run requests through the local Claude Code CLI (`claude -p`), i.e. the user's subscription.

    No tools, our own system prompt, structured output via --json-schema, and a
    neutral working directory so no project CLAUDE.md is loaded.
    """
    import subprocess
    import tempfile

    def call(params: dict[str, Any]) -> tuple[dict[str, Any] | None, str, dict[str, int]]:
        system = "\n\n".join(b["text"] for b in params["system"])
        prompt = "\n\n".join(b["text"] for b in params["messages"][0]["content"])
        cmd = [
            "claude", "-p",
            "--model", model,
            "--output-format", "json",
            "--tools", "",
            "--no-session-persistence",
            *([] if "haiku" in model else ["--effort", effort]),
            "--system-prompt", system,
            "--json-schema", json.dumps(SCHEMA),
        ]
        with tempfile.TemporaryDirectory(prefix="osca-") as cwd:
            try:
                proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=cwd, timeout=timeout)
            except FileNotFoundError:
                return None, "`claude` CLI not found (install Claude Code, or set ai.backend: api)", {}
            except subprocess.TimeoutExpired:
                return None, f"claude -p timed out after {timeout}s", {}
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return None, f"claude -p failed (exit {proc.returncode}): {(proc.stderr or proc.stdout)[-400:]}", {}
        u = out.get("usage") or {}
        usage = {k: int(u.get(k, 0) or 0) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
        usage["cost_micro_usd"] = int(float(out.get("total_cost_usd") or 0) * 1_000_000)  # list-price equivalent
        if out.get("is_error") or out.get("subtype") != "success":
            return None, f"claude -p: {out.get('subtype')} {str(out.get('result', ''))[:300]}", usage
        data = out.get("structured_output")
        if data is None:
            try:
                data = json.loads(out.get("result") or "")
            except json.JSONDecodeError:
                return None, "claude -p returned no structured output", usage
        return data, "", usage

    return call


def caller_for(project: Project) -> Caller:
    cfg = ai_config(project)
    if cfg["backend"] == "api":
        return anthropic_caller()
    if cfg["backend"] == "claude-code":
        return claude_code_caller(cfg["model"], cfg["effort"])
    raise ValueError(f"unknown ai.backend {cfg['backend']!r}")


def run(project: Project, jobs: list[Job], terms: dict[str, dict], call: Caller, on_result=None) -> list[Result]:
    results = []
    for job in jobs:
        job = refresh(project, job)
        if not job.targets:
            continue
        data, err, usage = call(request_params(project, job, terms))
        if data is None:
            res = Result(job.path, error=err, usage=usage)
        else:
            res = apply(project, job, data)
            res.usage = usage
        results.append(res)
        if on_result:
            on_result(job, res)
    record(project, results, f"ai:{ai_config(project)['model']}")
    return results


# --- batches -----------------------------------------------------------------------------


def _batch_dir(root: Path) -> Path:
    return root / gitutil.git(root, "rev-parse", "--git-path", "osca-batches").strip()


def submit_batch(project: Project, jobs: list[Job], terms: dict[str, dict]) -> str:
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = anthropic.Anthropic()
    # one request per file: every response refers to the file as submitted
    merged: dict[str, Job] = {}
    for j in jobs:
        if j.path in merged:
            merged[j.path].targets += j.targets
        else:
            merged[j.path] = Job(j.path, j.text, list(j.targets), j.diff, j.context)
    jobs = list(merged.values())
    requests = [
        Request(custom_id=f"job-{i}", params=MessageCreateParamsNonStreaming(**request_params(project, j, terms, batch=True)))
        for i, j in enumerate(jobs)
    ]
    batch = client.messages.batches.create(requests=requests)
    d = _batch_dir(project.root)
    d.mkdir(parents=True, exist_ok=True)
    meta = [
        {"custom_id": f"job-{i}", "path": j.path, "sha": j.sha, "targets": [t.sym.id for t in j.targets]}
        for i, j in enumerate(jobs)
    ]
    (d / f"{batch.id}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return batch.id


def collect_batch(project: Project, batch_id: str) -> tuple[str, list[Result]]:
    """Apply a finished batch. Returns (processing_status, results)."""
    import anthropic

    client = anthropic.Anthropic()
    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        return batch.processing_status, []
    meta = {m["custom_id"]: m for m in json.loads((_batch_dir(project.root) / f"{batch_id}.json").read_text(encoding="utf-8"))}
    # rebuild jobs from the current tree; apply() refuses files that changed since submission
    jobs = {j.path: j for j in plan(project, sorted({m["path"] for m in meta.values()}), max_symbols=10**6)}
    for m in meta.values():  # files whose targets are no longer pending/stale still need a job object
        if m["path"] not in jobs:
            text = (project.root / m["path"]).read_text(encoding="utf-8")
            jobs[m["path"]] = Job(m["path"], text, [])
    results = []
    for item in client.messages.batches.results(batch_id):
        m = meta.get(item.custom_id)
        if m is None:
            continue
        if item.result.type != "succeeded":
            results.append(Result(m["path"], error=f"batch result {item.result.type}"))
            continue
        data, err = parse_message(item.result.message)
        job = jobs.get(m["path"])
        if data is None or job is None:
            results.append(Result(m["path"], error=err or "no pending work left for this file"))
            continue
        if job.sha != m["sha"]:
            results.append(Result(m["path"], error="file changed since the batch was submitted"))
            continue
        job.targets = [t for t in job.targets if t.sym.id in set(m["targets"])]
        if not job.targets:
            results.append(Result(m["path"], error="targets already handled"))
            continue
        res = apply(project, job, data)
        res.usage = _usage(item.result.message)
        results.append(res)
    record(project, results, f"ai:{ai_config(project)['model']}")
    return "ended", results

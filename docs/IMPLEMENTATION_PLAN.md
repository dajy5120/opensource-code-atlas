# OSCA 实施方案

> **OpenSource Code Atlas（开源源码地图）**
> A living atlas for studying, annotating, translating, and tracking the evolution of classic open-source codebases.
>
> 版本：v1.0 · 2026-10-04 · 首个试点项目：NautilusTrader

---

## 0. 结论先行（关键设计决策）

参考资料的大方向（总索引仓库 + 每项目独立仓库 + 双源结构 + 增量翻译）是正确的。本方案在此基础上补齐了**让它在 50～100 个项目规模下仍然可维护**所需的工程约束。最重要的 10 条决策：

| # | 决策 | 理由 |
|---|------|------|
| D1 | 所有中文注释必须是**独占一行、带 `【zh】` 标记**的注释行 | 让“学习版 = 上游源码 + 若干 `【zh】` 行”成为一个**可机器验证的不变式** |
| D2 | **剥离不变式**：`strip_zh(study) == upstream@anchor`（逐字节） | 比“能编译”强得多：直接证明代码一个字节都没改 |
| D3 | upstream 更新用 **merge**（不用 rebase），sync PR **禁止 squash** | 保留上游提交祖先关系，`merge-base` 即锚点；squash 会导致下次同步全量冲突 |
| D4 | 镜像分支命名为 `mirror/<branch>`，不使用 `upstream/main` | `upstream/main` 与远程跟踪引用 `refs/remotes/upstream/main` 同名，Git 会报 ambiguous refname |
| D5 | 快速演进项目**跟踪 release tag**，不跟 `develop` HEAD | NautilusTrader 每天多次提交，跟 tag 可以把翻译返工降低一个数量级 |
| D6 | 变化检测粒度是**符号（symbol）**，用 Tree-sitter + 内容哈希，而不是行号 | 行号在每次上游提交后都会漂移；哈希能精确判断“这个函数变了没有” |
| D7 | 状态**派生而非手填**：人只维护配置，状态由工具从哈希计算 | 1,000+ 文件的手填状态必然腐烂 |
| D8 | AI 输出**结构化注释补丁（JSON）**，由工具插入，AI 不直接编辑源文件 | 从构造上保证 AI 无法修改代码 |
| D9 | OSCA 自有内容全部放在 `.osca/` 与 `osca/` 下 | 上游已有 `docs/`、`CLAUDE.md`、`.github/` 等，必须避免路径冲突 |
| D10 | 项目模板用 **copier** 管理 | 模板升级后可 `copier update` 批量回灌到已有的 50 个项目，而不是一次性复制 |

---

## 1. 总体架构

```
                         opensource-code-atlas（总索引仓库：只放“地图”，不放源码）
        ┌──────────────────────────┬───────────────────────────┬─────────────────────┐
        │ projects/*.yaml 项目登记  │ terminology/ 统一术语库    │ tools/osca  CLI      │
        │ templates/ copier 模板    │ docs/ 规范、ADR、风格指南   │ CI：聚合各项目状态    │
        └────────────┬─────────────┴─────────────┬─────────────┴──────────┬──────────┘
                     │ 登记/聚合 status.json       │ 术语注入                 │ uv tool install
                     ▼                            ▼                         ▼
   osca-nautilus-trader   osca-tokio   osca-polars   osca-duckdb   ...（每项目一个独立仓库）
   ┌───────────────────────────────────────────────────────────────────────────────┐
   │ mirror/develop   ── 官方原始源码（只 fast-forward，永不手改）                      │
   │ study/zh-CN      ── 官方源码 + 【zh】 中文注释 + .osca/ 元数据 + osca/ 分析文档       │
   │ sync/*           ── 每次上游同步的临时分支（PR → study/zh-CN）                     │
   │ tr/*             ── 翻译工作分支（PR → study/zh-CN）                               │
   │ exp/*            ── 实验分支（从 study 分出，永不合回）                             │
   └───────────────────────────────────────────────────────────────────────────────┘
```

**数据流（核心闭环）：**

```
upstream 新 tag
   │ osca sync          git fetch → ff mirror → merge 进 sync 分支 → 自动解冲突
   ▼
符号索引对比              Tree-sitter 解析 old/new 两个版本，按哈希比对
   │ osca impact
   ▼
CHANGE_ANALYSIS.md       新增/删除/修改/重命名/签名变化 的符号列表 + 受影响的 【zh】 注释
   │ osca translate      仅针对 stale + new 的符号，AI 输出 JSON 注释补丁
   ▼
osca verify              剥离不变式 + 注释位置 lint + 术语 lint（秒级）
   │
   ▼
cargo check / doc / 测试   分层 CI（见 §8）
   │
   ▼
人工 Review → osca review approve → merge → status.json 更新 → 总仓库聚合
```

---

## 2. 仓库设计

### 2.1 总索引仓库 `opensource-code-atlas`

```
opensource-code-atlas/
├── README.md                    # 项目介绍 + 自动生成的进度总表（标记区间内由 CI 改写）
├── CLAUDE.md                    # 总仓库的 Claude Code 规则
├── catalog/
│   └── categories.yaml          # 分类定义：trading / rust / database / infra / ai ...
├── projects/                    # 一项目一文件（避免多人/多 PR 改同一个分类文件冲突）
│   ├── nautilus-trader.yaml
│   └── ...
├── terminology/                 # 统一术语库（跨项目共享）
│   ├── _schema.json
│   ├── general.yml              # 通用编程
│   ├── rust.yml
│   ├── python.yml
│   ├── distributed-systems.yml
│   ├── trading.yml
│   ├── database.yml
│   └── ai.yml
├── templates/
│   └── study-repo/              # copier 模板：.osca/、osca/、CLAUDE 规则、CI、hooks
├── tools/
│   └── osca/                    # Python CLI（uv 管理），见 §6
├── docs/
│   ├── IMPLEMENTATION_PLAN.md   # 本文
│   ├── conventions/
│   │   ├── comment-style.md     # 中文注释风格指南
│   │   ├── branching.md
│   │   └── analysis-docs.md
│   └── adr/                     # 架构决策记录：0001-zh-marker.md ...
├── status/                      # CI 聚合产物（各项目 status.json 快照，可选）
└── .github/workflows/
    ├── ci.yml                   # osca 工具测试、术语库 schema 校验
    └── aggregate.yml            # 定时拉取各项目 status.json → 重写 README 进度表
```

> 与参考资料的差异：`projects/` 改为**一项目一文件**，分类作为字段（一个项目可以同时属于 trading 和 rust）；进度百分比**不手写**，由 CI 聚合。

`projects/nautilus-trader.yaml`（登记信息，手工维护，极少变动）：

```yaml
id: nautilus-trader
name: NautilusTrader
description: 高性能、生产级的算法交易平台（Rust 核心 + Python API）
repository:
  upstream: https://github.com/nautechsystems/nautilus_trader
  study: https://github.com/dajy5120/osca-nautilus-trader
license: LGPL-3.0            # 上游许可证，决定学习仓库能否公开及如何声明
languages:
  primary: rust
  secondary: [python, cython]
categories: [trading, rust]
domains: [market-data, order-management, backtesting, execution]
terminology: [general, rust, python, trading, distributed-systems]
priority: 1
onboarded: 2026-10-04
```

### 2.2 学习仓库 `osca-<project>`（以 `osca-nautilus-trader` 为例）

**不要在 GitHub 上点 Fork**，而是新建一个普通仓库后推送上游历史：

- Fork 会把 PR 默认目标指向上游，容易误向官方仓库发 PR；
- Fork 不能单独设为私有（在授权未厘清前你可能需要私有）；
- 普通仓库 + `upstream` remote 完全满足需求。

```
osca-nautilus-trader/  （study/zh-CN 分支视图）
├── <上游全部源码，保持原样，仅插入 【zh】 注释行>
├── CLAUDE.md                    # 若上游没有则新增；若上游已有，只在末尾追加一行 @.osca/CLAUDE.md
├── .osca/                       # OSCA 元数据（机器 + 人）
│   ├── project.yaml             # 配置（人工维护）
│   ├── sync.yaml                # 锚点与同步历史（工具写入）
│   ├── terminology.yaml         # 项目级术语覆盖（可选）
│   ├── CLAUDE.md                # 本项目的 Claude Code 硬规则
│   ├── state/
│   │   └── symbols.jsonl        # 符号级翻译状态（工具写入，按 id 排序，diff 友好）
│   ├── status.json              # 汇总统计（工具生成，供总仓库聚合）
│   └── reports/
│       └── sync-2026-10-04-v1.220.0..v1.221.0.md   # 每次同步的 CHANGE_ANALYSIS
├── osca/                        # OSCA 自有的人类可读内容
│   ├── README.zh-CN.md          # 学习入口：阅读路线图
│   ├── docs/
│   │   ├── architecture/        # 架构总览、分层、依赖图
│   │   ├── modules/             # 按 crate / package 的模块分析
│   │   ├── flows/               # 关键流程：订单生命周期、回测事件循环…
│   │   └── notes/               # 学习笔记
│   └── experiments/             # 不修改上游代码的独立实验（独立 crate，不加入上游 workspace）
├── .claude/                     # Claude Code 命令与 hooks（见 §9；若上游已有则合并）
└── .github/workflows/osca-*.yml # 仅 OSCA 自己的工作流
```

> 为什么不用参考资料里的 `docs/source-analysis/`：NautilusTrader 上游本身就有 `docs/` 目录，放进去会与上游同步冲突，并破坏剥离不变式。

**OSCA 允许新增/修改的路径白名单（overlay）**——`osca verify` 只放行这些：

```
.osca/**   osca/**   .claude/**（仅 OSCA 新增文件）   .github/workflows/osca-*.yml
CLAUDE.md（新增或仅追加一行 import）   以及任意文件中的 【zh】 注释行
```

---

## 3. 分支模型

```
upstream (remote)   ──A──B──C──D──────────E──F──G   (tag v1.221.0 = G)
                                │                 │
mirror/develop      ──A──B──C──D ═══ff═══════════▶G      只 fast-forward
                                │                 │
study/zh-CN         ──A──B──C──D──z1──z2──z3──────M──z4   M = merge(G)，z = 中文注释提交
                                                 ╱
sync/v1.221.0                       (从 z3 分出，merge G，解冲突，生成报告 → PR)

exp/performance/orderbook-bench     从 study/zh-CN 任意点分出，永不合回
```

| 分支 | 来源 | 写入方式 | 合并规则 |
|------|------|---------|---------|
| `mirror/<upstream-branch>` | 上游 | 仅 `osca sync` fast-forward | 不接受 PR；检测到上游 force-push 时报警停止 |
| `study/zh-CN`（默认分支） | mirror | 只通过 PR | 受保护；必须通过 `osca verify` |
| `sync/<to-ref>` | study | `osca sync` 生成 | **必须用 “Create a merge commit”**，禁止 squash/rebase |
| `tr/<scope>` | study | 翻译工作 | 可 squash |
| `exp/<category>/<name>` | study | 任意修改 | 永不合回 study；有价值的结论写入 `osca/docs/notes/` |

> `experiments` 的分类（architecture / performance / api / strategy / prototype）体现在分支名上：`exp/performance/...`、`exp/api/...`。修改上游代码的实验**只能**放在 `exp/*` 分支；不修改上游代码的独立 benchmark/原型可放 `osca/experiments/`。

GitHub 仓库设置：默认分支设为 `study/zh-CN`；开启 merge commit；分支保护要求 `osca-verify` 检查通过。

---

## 4. 中文注释规范（整个系统的基石）

### 4.1 标记格式

每一行中文注释都**独占一行**，注释体以 `【zh】` 开头：

**Rust**

```rust
/// Provides an order book which can handle L1/L2/L3 granularity data.
/// 【zh】 订单簿（Order Book）：维护单个交易品种的买卖盘口，
/// 【zh】 支持 L1（最优价）/L2（按价位聚合）/L3（逐笔订单）三种粒度。
#[derive(Clone, Debug)]
pub struct OrderBook {
    // 【zh】 卖盘按价格升序排列，因此 asks.top() 即最优卖价。
    pub asks: BookLadder,
```

**Python / Cython**

```python
# 【zh】 风险引擎：所有订单在送达执行引擎前必须经过此处的事前风控检查。
# 【zh】 若交易状态为 HALTED，会直接拒绝所有新订单。
@cython.final
cdef class RiskEngine(Component):
```

### 4.2 放置规则（由 `osca lint` 用 Tree-sitter 强制检查）

1. **禁止行尾注释**：`let x = 1; // 【zh】 ...` 不允许——否则剥离时无法做到逐字节还原。
2. **禁止插入字符串/原始字符串/文档字符串内部**：插入多行字符串的行会改变字符串值，剥离检查却能通过，这是剥离不变式唯一的盲区，必须由 AST 检查兜底。
3. **禁止插入 rustdoc 代码块（```）内部**：会改变 doctest。
4. Rust 中 `/// 【zh】` 只能追加在**已有 English `///` 文档块之后、属性 `#[...]` 之前**；其他位置一律用 `// 【zh】`（避免 `unused_doc_comments` 等 lint 及“doc comment 后无条目”的编译错误）。
5. Python 中**不修改 docstring**（会改变运行时 `__doc__`），中文说明一律以 `# 【zh】` 写在 `def/class` 及其装饰器**之上**。
6. 学习分支上**禁止运行格式化器**（rustfmt / ruff / pre-commit），不要 `pre-commit install`——格式化器可能重排注释或代码。

### 4.3 内容风格（`docs/conventions/comment-style.md` 详述）

- 不是逐句直译，而是**“翻译 + 讲解”**：说明这段代码**为什么**这样做、在系统中处于什么位置、有什么不变式/边界条件。
- 术语首次出现写成 `中文（English）`，之后可只用中文；`terminology` 标记 `keep_english: true` 的词（如 trait、future、lifetime）保留英文。
- 不复述代码（避免 `// 【zh】 i 加 1`）；单个符号注释一般不超过 8 行，更长的分析放进 `osca/docs/` 并在注释中引用：`// 【zh】 详见 osca/docs/flows/order-lifecycle.md`。

### 4.4 剥离不变式（`osca verify` 的核心）

```
设 A = 最近一次同步的上游提交（锚点）
对 git diff --name-only A HEAD 中的每个文件 f：
    若 f ∈ overlay 白名单       → 放行
    否则要求 strip_zh(HEAD:f) == A:f   （逐字节相等）
    并且 A 中存在的文件在 HEAD 中不得被删除
strip_zh = 删除所有满足 ^\s*(//[/!]?|#)\s*\[zh\] 的整行
```

这条检查在毫秒到秒级完成，不需要编译，**每个 PR、每次 AI 编辑后都跑**。它把“源码保持可编译”从一个愿望变成了一个可证明的性质（配合 §4.2 的 AST 检查）。

---

## 5. 元数据与状态模型

原则：**人工维护的配置**与**工具写入的状态**分文件存放，避免合并冲突，也避免状态被手改。

### 5.1 `.osca/project.yaml`（人工维护）

```yaml
schema: osca/v1
project: nautilus-trader          # 对应总仓库 projects/nautilus-trader.yaml
osca_version: ">=0.3,<0.4"        # 要求的 osca CLI 版本

upstream:
  repository: https://github.com/nautechsystems/nautilus_trader
  branch: develop
  track: tags                     # tags | branch
  tag_pattern: '^v\d+\.\d+\.\d+$'
  mirror_branch: mirror/develop

study:
  branch: study/zh-CN
  language: zh-CN
  strategy: inline-comments
  marker: "【zh】"

scope:                            # 翻译范围：先聚焦核心，不追求全覆盖
  include:
    - crates/core/**
    - crates/model/**
    - crates/common/**
    - crates/execution/**
    - crates/risk/**
    - crates/backtest/**
    - nautilus_trader/**/*.pyx
    - nautilus_trader/**/*.py
  exclude:
    - "**/tests/**"
    - "**/benches/**"
    - crates/adapters/**          # 交易所适配器数量多、价值低，后期再说
  symbols:                        # 哪些符号算“应翻译”
    kinds: [module, struct, enum, trait, impl, fn, class, method]
    min_body_lines: 5             # 小于该行数且无英文文档的私有函数不计入
    public_only: false

terminology: [general, rust, python, trading, distributed-systems]

build:                            # 分层 CI 命令（见 §8）
  check: cargo check --workspace --all-features
  doc: cargo doc --workspace --no-deps
  test: make test                 # 仅在 sync PR / 手动触发时运行

ai:
  translate_model: claude-sonnet-5-5
  analysis_model: claude-opus-5-5
```

### 5.2 `.osca/sync.yaml`（工具写入）——“锚点”

```yaml
anchor:
  upstream_commit: 3f9a1c2e8b...     # 当前学习版对应的官方提交（完整 SHA）
  upstream_ref: v1.221.0
  synced_at: 2026-10-04T10:30:00+08:00
history:
  - from: v1.220.0
    to: v1.221.0
    from_commit: 9b1e...
    to_commit: 3f9a...
    commits: 87
    files_changed: 214
    symbols: { added: 41, removed: 12, modified: 133, renamed: 6 }
    zh_stale: 58
    report: .osca/reports/sync-2026-10-04-v1.220.0..v1.221.0.md
```

`upstream_commit` 是人类可读的锚点；工具同时用 `git merge-base study/zh-CN mirror/develop` 交叉验证，两者不一致即报错。

### 5.3 符号身份与哈希（增量翻译的关键）

**符号 ID**：`<path>#<qualified-name>`，例如

```
crates/model/src/orderbook/book.rs#impl OrderBook::apply_delta
nautilus_trader/risk/engine.pyx#RiskEngine._check_order
```

同名冲突（如 `#[cfg]` 变体）追加序号 `~2`。

每个符号计算三个哈希（计算前剔除所有 `【zh】` 行、归一化空白）：

| 哈希 | 内容 | 变化意味着 |
|------|------|-----------|
| `sig` | 签名（函数头/结构体字段/trait 方法列表） | API 变化，**高优先级**复核 |
| `doc` | 英文文档注释与普通注释 | 原文变了，中文需要**重新翻译** |
| `body` | 实现体 | 实现变了，中文**讲解**可能过时 |

### 5.4 `.osca/state/symbols.jsonl`（工具写入，每行一个符号）

```json
{"id":"crates/model/src/orderbook/book.rs#impl OrderBook::apply_delta","kind":"fn","sig":"a1f0","doc":"77c2","body":"e93b","zh":{"hash":"5d10","at":{"sig":"a1f0","doc":"77c2","body":"e93b"},"by":"ai:claude-sonnet-5-5","commit":"3f9a1c2"},"review":{"by":"dajy5120","at":"2026-10-05","hashes":{"sig":"a1f0","doc":"77c2","body":"e93b","zh":"5d10"}}}
```

**状态是派生出来的**，不存储，不手填：

```
无 【zh】 注释                                   → pending
有 【zh】，且注释时的 sig/doc/body ≠ 当前          → stale（needs_review），并标出是哪类变化
有 【zh】，哈希一致，无有效人工审核               → translated（AI 或人工初稿）
有 【zh】，且审核时记录的 4 个哈希全部与当前一致    → reviewed
符号已被上游删除，但 【zh】 注释残留               → orphaned（同步时自动报告）
```

这样“人工审核过的注释”在上游改动后会**自动失效**为 stale，无需任何人记得去改状态。

### 5.5 `.osca/status.json`（生成）与 `osca status` 输出

```
NautilusTrader  @ v1.221.0 (3f9a1c2)   synced 2026-10-04
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                 Files     Symbols
In scope         1,482      18,930
Reviewed           803       9,412
Translated (AI)    300       4,688
Stale              217       1,205   (sig 88 · doc 410 · body 707)
Pending            162       3,625
Orphaned             –          14

Coverage   74.46%  (translated + reviewed, by symbol)
Reviewed   49.72%
Analysis   12 / 20 docs current (3 stale)
```

文件级状态 = 其内符号状态的聚合（任一 stale → 文件 stale）。

---

## 6. `osca` CLI 设计

**技术选型**：Python 3.12 + uv + typer + `tree-sitter`（`tree-sitter-language-pack` 提供 Rust/Python/Go/C/C++/TS/Java…）+ GitPython 或直接调用 `git`。放在总仓库 `tools/osca/`，安装方式：

```bash
uv tool install "git+https://github.com/dajy5120/opensource-code-atlas#subdirectory=tools/osca"
```

选 Python 而非 Rust：语言适配器（Tree-sitter query）迭代频繁、需要调用 LLM API、性能瓶颈在 Git 与 AST 解析（已是 C 实现），Python 的开发效率更合适。

| 命令 | 所在仓库 | 作用 |
|------|---------|------|
| `osca new <id> --upstream <url>` | 总仓库 | 登记项目 → 创建 GitHub 仓库 → 推送上游历史 → copier 生成 `.osca/` 等 → 建立初始索引 |
| `osca sync [--to <ref>]` | 学习仓库 | fetch → ff mirror → 建 `sync/*` 分支 → merge → `resolve` → `impact` → `verify` → 开 PR |
| `osca resolve` | 学习仓库 | 自动解冲突：冲突块取上游版本，再按符号把 `【zh】` 行重新挂回仍然存在的符号；挂不回去的标 orphaned |
| `osca impact <a>..<b>` | 学习仓库 | 生成 CHANGE_ANALYSIS 报告（符号级 diff + 受影响注释 + 受影响分析文档） |
| `osca index` | 学习仓库 | 重建符号索引与哈希 |
| `osca verify [--files ...]` | 学习仓库 | 剥离不变式 + 注释位置 lint + 术语 lint + overlay 白名单 |
| `osca strip [--out dir]` | 学习仓库 | 输出去掉 `【zh】` 的源码（调试用） |
| `osca status [--json]` | 学习仓库 | 统计（§5.5） |
| `osca queue [--stale] [--path ..]` | 学习仓库 | 列出待翻译/待复核的符号，按优先级排序 |
| `osca translate <path\|symbol\|--queue N>` | 学习仓库 | 调用 AI 生成 JSON 注释补丁并应用（§7） |
| `osca review [approve\|reject] <path\|symbol>` | 学习仓库 | 交互式逐符号审核，写入 review 记录 |
| `osca list` | 总仓库 | 读取各项目 status.json，按分类打印进度树 |
| `osca terms lint/find <term>` | 两者 | 检查 `avoid` 用词；术语变更时找出所有需要改的注释 |

### 6.1 `osca sync` 详细流程

```bash
git fetch upstream --tags --prune
TARGET=$(osca _resolve-target)                         # 最新匹配 tag_pattern 的 tag
git merge-base --is-ancestor mirror/develop "$TARGET" \
  || abort "上游历史被改写（force-push），需人工处理"
git branch -f mirror/develop "$TARGET" && git push origin mirror/develop
git switch -c "sync/$TARGET_REF" study/zh-CN
git merge --no-ff "$TARGET" -m "sync: upstream $OLD_REF → $TARGET_REF"
osca resolve                                            # 仅在有冲突时
osca index && osca impact "$OLD..$TARGET" > .osca/reports/sync-....md
osca verify
git add .osca && git commit -m "osca: update index & report for $TARGET_REF"
gh pr create --base study/zh-CN --body-file .osca/reports/sync-....md
```

冲突的本质：我们只加了 `【zh】` 行，所以冲突必然是“上游改动的位置附近有中文注释”。`osca resolve` 的策略因此可以是确定性的——**上游代码永远赢，中文注释重新挂载或标记过时**，不需要人工逐个解冲突。

### 6.2 CHANGE_ANALYSIS 报告样例

```markdown
# Upstream Changes: v1.220.0 → v1.221.0
87 commits · 214 files · 2026-09-20 → 2026-10-03

## 需要复核的中文注释（58）
| 优先级 | 符号 | 变化 | 提交 |
|---|---|---|---|
| P0 | crates/execution/src/order_manager.rs#OrderManager::submit_order | sig 变化：新增参数 `params: Option<Params>` | e4a1b2c |
| P1 | crates/model/src/orderbook/book.rs#impl OrderBook::apply_delta | doc 变化 | f02c9d1 |
| P2 | crates/risk/src/engine.rs#RiskEngine::check_order | body 变化（+23/−8） | 7aa3e01 |

## 新增符号（41）  · 删除符号（12）  · 重命名（6，按 body 相似度识别）
## 孤儿注释（3）：符号已删除，注释已移至报告附录，待人工决定
## 受影响的分析文档（2）：osca/docs/flows/order-lifecycle.md …
## 依赖变化：Cargo.toml / pyproject.toml diff 摘要
## AI 摘要（可选）：本版本的主要架构变化
```

---

## 7. AI 分工（对应参考资料的 5 个 Level）

| Level | 内容 | 执行者 | 自动化程度 |
|------|------|-------|-----------|
| L1 源码同步 | fetch / ff / merge | `osca sync`（纯 Git） | 全自动（定时） |
| L2 变化检测 | 符号级 diff、重命名识别、报告 | Tree-sitter + 哈希（**确定性，不用 AI**）；AI 只写报告末尾摘要 | 全自动 |
| L3 中文注释 | stale/new 符号的翻译与讲解 | AI（结构化补丁） | 自动生成，人工审核 |
| L4 源码分析 | 架构/模块/流程文档 | AI + 人在 Claude Code 中协作 | 半自动 |
| L5 人工审核 | PR review + `osca review` | 人 | 人工 |

> 关键改进：L2 用确定性工具完成。AI 判断“哪些函数变了”既贵又不可靠，哈希比较则完全可靠且免费。

### 7.1 L3 翻译：结构化注释补丁

AI **不直接编辑文件**，只返回 JSON：

```json
{
  "file": "crates/model/src/orderbook/book.rs",
  "annotations": [
    {
      "symbol": "impl OrderBook::apply_delta",
      "placement": "doc_append",
      "lines": ["【zh】 应用单条盘口增量（delta）：根据 action 执行新增/更新/删除。",
                "【zh】 注意：sequence 必须单调递增，否则视为乱序数据。"]
    },
    {
      "symbol": "impl OrderBook::apply_delta",
      "placement": "before_line",
      "anchor_text": "match delta.action {",
      "lines": ["【zh】 四种动作中 Clear 会清空整个盘口，常见于快照重建。"]
    }
  ],
  "terms_used": ["order_book", "delta"]
}
```

由 `osca translate` 负责：定位锚点（`anchor_text` 文本匹配，而非行号）→ 按 §4.2 规则校验位置 → 插入 → 跑 `osca verify` → 更新 `symbols.jsonl`。代码从构造上不可能被 AI 修改。

**Prompt 组成**（固定部分走 prompt caching）：风格指南 + 相关术语表（只注入该文件命中的术语）+ 整个文件源码 + 目标符号列表 +（stale 时）旧中文注释与上游 diff，要求“在旧注释基础上最小修改”。

**批量执行**：大规模初次翻译使用 Message Batches API（异步、成本更低）；日常增量量小，直接同步调用即可。

### 7.2 L4 分析文档：同样可被追踪

每篇 `osca/docs/**.md` 带 front matter：

```yaml
---
title: 订单生命周期
upstream_commit: 3f9a1c2
anchors:
  - crates/execution/src/order_manager.rs#OrderManager::submit_order
  - crates/model/src/orders/mod.rs#OrderAny
  - crates/risk/src/engine.rs#RiskEngine::check_order
status: reviewed
---
```

任一锚点符号的哈希变化 → 文档在报告中标为 stale。这让架构文档也纳入同一套增量机制，而不是写完即过时。图示统一用 Mermaid（GitHub 可直接渲染）。

---

## 8. CI 分层

| 层级 | 内容 | 触发 | 耗时 |
|------|------|------|------|
| L0 | `osca verify`（剥离不变式 + AST 位置 lint + 术语 lint + overlay 白名单） | 每个 PR、Claude Code 每次编辑后 | 秒级 |
| L1 | `cargo check` + `cargo doc`（仅受影响的 crate） | 修改 `.rs` 的 PR | 分钟级 |
| L2 | 完整构建 + 测试 | `sync/*` PR、手动触发 | NautilusTrader 较重，建议本地或自托管 runner |

由于 L0 已证明“除注释外代码逐字节一致”，L1 主要捕捉 rustdoc 相关问题，L2 主要用于确认上游版本本身可构建——这让日常翻译 PR 的 CI 成本几乎为零。

**必须处理的坑：上游自带的 GitHub Actions。** 推送上游历史后，`.github/workflows/` 里的上游工作流会在你的仓库里运行（消耗额度且必然失败）。处理方式：

1. 分支命名（`study/zh-CN`、`mirror/develop`）避开上游工作流的分支触发条件；
2. `osca sync` 末尾对所有非 `osca-*.yml` 的工作流执行 `gh workflow disable`（上游新增的工作流也会被自动禁用）；
3. `osca verify` 不允许修改/删除上游的 workflow 文件（保持剥离不变式）。

---

## 9. Claude Code 自动化规则

### 9.1 `.osca/CLAUDE.md`（被根 `CLAUDE.md` 通过 `@.osca/CLAUDE.md` 引入）

```markdown
# OSCA 学习仓库规则（硬性）

本仓库 = 上游源码 + `【zh】` 中文注释。你的工作只允许：
1. 插入独占一行、以 `// 【zh】`、`/// 【zh】` 或 `# 【zh】` 开头的注释行；
2. 修改/新增 `.osca/`、`osca/` 下的文件。

绝对禁止：
- 修改、删除、移动任何非 `【zh】` 行（包括空白、格式、英文注释、docstring）；
- 行尾注释；在字符串、docstring、rustdoc 代码块内插入；
- 运行 rustfmt / ruff / pre-commit 等格式化工具；
- 在 `mirror/*` 分支上提交；向上游仓库发 PR。

每次编辑后必须运行 `osca verify --files <改动文件>` 并保证通过。
术语以 `osca terms show <file>` 输出为准；风格见 osca/README.zh-CN.md#注释风格。
修改代码的实验只能在 `exp/*` 分支进行。
```

如果上游已有 `CLAUDE.md` / `AGENTS.md`：仅在末尾追加一行 `@.osca/CLAUDE.md`（`verify` 对此单行做特殊放行），把冲突面压缩到一行。

### 9.2 Hooks（`.claude/settings.json`）——把规则从“提示”变成“强制”

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [{ "type": "command", "command": "osca verify --hook" }]
      }
    ]
  }
}
```

`osca verify --hook` 从 stdin 读取被编辑的文件路径，校验失败时以退出码 2 返回错误信息，Claude Code 会立即看到“你修改了代码行 X”并自行回滚。这比写在 CLAUDE.md 里的规则可靠得多。

### 9.3 自定义命令 / Skills（`.claude/commands/`）

| 命令 | 作用 |
|------|------|
| `/osca-translate <path>` | 调 `osca queue` 取该路径待办 → 阅读上下文 → 生成注释 → `osca verify` |
| `/osca-sync` | 运行 `osca sync`，阅读报告，补写 AI 摘要 |
| `/osca-analyze <module>` | 生成/更新 `osca/docs/modules/<module>.md`，带 anchors front matter |
| `/osca-review <path>` | 逐符号展示注释与源码，辅助人工审核 |

这些命令与 hooks 都放在 copier 模板中，新项目自动获得。

---

## 10. 术语库设计

`terminology/trading.yml`：

```yaml
schema: osca-terms/v1
domain: trading
terms:
  order_book:
    en: Order Book
    zh: 订单簿
    avoid: [订单书, 委托簿]
    note: L2/L3 盘口数据结构
  market_data:
    en: Market Data
    zh: 市场数据
    avoid: [行情资料]
  execution_engine:
    en: Execution Engine
    zh: 执行引擎
  fill:
    en: Fill
    zh: 成交
    avoid: [填充]            # 机器翻译最常见的错误
  instrument:
    en: Instrument
    zh: 交易品种
    avoid: [仪器, 工具]
```

`terminology/rust.yml` 中的“保留英文”示例：

```yaml
  trait:    { en: trait,    zh: trait,  keep_english: true, note: 不译为“特征/特质” }
  lifetime: { en: lifetime, zh: 生命周期 }
  borrow:   { en: borrow,   zh: 借用 }
  future:   { en: Future,   zh: Future, keep_english: true }
```

机制：

- 优先级：项目级 `.osca/terminology.yaml` > 领域术语 > `general.yml`；
- `osca terms lint` 检查 `【zh】` 行中的 `avoid` 用词，进入 L0 CI；
- 术语变更时 `osca terms find fill` 列出所有项目中受影响的注释，可批量生成修订 PR；
- 术语库本身有 JSON Schema 校验，避免格式错误。

---

## 11. 总仓库聚合与 `osca list`

`aggregate.yml` 每日运行：读取 `projects/*.yaml` → 通过 `gh api` 拉取每个学习仓库默认分支上的 `.osca/status.json` → 重写 README 中 `<!-- osca:status:start -->` 与 `<!-- osca:status:end -->` 之间的表格。

```
$ osca list
OpenSource Code Atlas                         translation  reviewed  anchor     behind
Trading
├── NautilusTrader                                74%         50%     v1.221.0   0 tags
├── Lean                                          32%         10%     v2.5.17    2 tags
└── Backtrader                                    18%          5%     1.9.78     0
Rust
├── Tokio                                         41%         30%     tokio-1.48 1 tag
...
```

`behind` 列（落后上游多少个 tag）让你一眼看出哪个项目需要同步。

---

## 12. 实施路线图

核心思路：**先用一个项目把闭环跑通，再模板化；第二个项目用不同语言来验证通用性。**

> **验证闭环的技巧**：试点项目的初始锚点故意设在**倒数第二个 release tag**。这样翻译完一个模块后，第一天就能执行一次真实的 `osca sync` 到最新 tag，验证 diff → 受影响符号 → 重译 → verify → review 的全流程，而不用等上游发布新版本。

### Phase 0 · 基础规范（第 1 周）

- 初始化 `opensource-code-atlas` 目录骨架、README、CLAUDE.md
- 写 ADR：0001 `【zh】` 标记与剥离不变式、0002 分支模型、0003 状态派生模型
- 写 `comment-style.md`、`branching.md`
- 种子术语库：general / rust / python / trading 各 30～50 条
- **验收**：规范文档评审通过；手工在 2 个 Rust 文件 + 1 个 pyx 文件上试写注释，确认规范可执行

### Phase 1 · 双源仓库 + 最小工具（第 1～2 周）

- `osca` CLI 骨架：`strip`、`verify`（剥离不变式 + overlay）、`sync`（无冲突路径）、文件级 `status`
- 创建 `osca-nautilus-trader`：推送上游历史，`mirror/develop` 锚定到倒数第二个 tag，建立 `study/zh-CN`，禁用上游工作流，配置分支保护
- 人工 + Claude Code 翻译一个小而核心的模块：`crates/model/src/orderbook/`
- **验收**：`osca verify` 在 CI 中运行；故意改一个代码字符能被拦截

### Phase 2 · 符号索引与增量检测（第 3～4 周）

- Tree-sitter 适配器：Rust、Python（Cython `.pyx` 先按 Python 语法尽力解析，失败的文件降级为文件级跟踪）
- 符号 ID、三类哈希、`symbols.jsonl`、派生状态
- `osca impact` 报告、`osca resolve` 自动解冲突、重命名识别
- 4.2 节的 AST 位置 lint
- **验收（闭环 MVP）**：从倒数第二个 tag 同步到最新 tag，报告准确列出 orderbook 模块中受影响的符号，冲突全自动解决，stale 状态正确

### Phase 3 · AI 翻译流水线与审核（第 5～6 周）

- `osca translate`：结构化补丁、术语注入、prompt caching、批处理模式
- `osca review`、`osca queue`、术语 lint
- Claude Code hooks + 4 个自定义命令
- CI L0/L1；`status.json` 生成
- **验收**：用 `osca translate --queue` 完成 `crates/model` 全部翻译，人工审核抽样通过；hook 能实时拦截 AI 的越界编辑

### Phase 4 · 模板化与第二个项目（第 7～8 周）

- 抽取 copier 模板 `templates/study-repo`；实现 `osca new`
- 分析文档 anchors 机制；第一批 NautilusTrader 架构文档（总览、订单生命周期、回测事件循环、消息总线）
- 总仓库 `aggregate.yml` + `osca list`
- 用**不同语言**接入第二个项目验证通用性：推荐 **NATS Server（Go）** 或 **Freqtrade（Python）**
- **验收**：第二个项目从 `osca new` 到第一次 sync 成功 **< 1 小时**；`copier update` 能把模板改动同步到两个项目

### Phase 5 · 规模化（第 9 周起）

- 增加语言适配器（Go、C/C++、TypeScript、Java）——每个适配器 = 一组 Tree-sitter query + 注释语法
- 定时同步：总仓库按周触发各项目 `osca sync`（可用 Claude Code 定时任务或 GitHub Actions）
- 阅读站点：mdBook / VitePress 渲染 `osca/docs` + 带中文注释的源码浏览（可后置）
- 按分类逐步接入：Trading → Rust → AI → Database → Infrastructure

---

## 13. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| 上游大规模重构（文件移动、模块拆分） | 大量注释失效 | `git diff -M` 重命名检测 + 符号 body 哈希相似度重新挂载；挂不上的进入 orphaned 附录而非丢弃 |
| 上游 force-push / 改写历史 | 锚点失效 | `sync` 前做祖先检查，失败即停止；跟踪 tag 可大幅降低概率 |
| sync PR 被误 squash | 下次同步全量冲突 | 仓库设置 + PR 模板提示；`osca sync` 检测 study 与 mirror 的 merge-base 是否等于记录的锚点 |
| AI 讲解出现事实错误 | 误导学习 | reviewed / translated 状态分离；status 中 Reviewed 单独统计；关键模块要求人工审核 |
| 剥离不变式的盲区（字符串内插入） | 静默改变程序行为 | §4.2 AST 检查兜底，列入 L0 |
| 许可证 | 公开发布的合规性 | `projects/*.yaml` 记录 license；保留上游 LICENSE 与版权声明；在 `osca/README.zh-CN.md` 声明为非官方学习版本；对 SSPL/BSL/RSAL 等非 OSI 许可证的项目（如部分新版 Redis）在公开前单独确认 |
| 上游自带 CLAUDE.md / .claude / workflows | 冲突或误运行 | §2.2 单行 import、§8 禁用上游工作流 |
| 仓库体积（如 Kubernetes、PostgreSQL） | clone/CI 慢 | 学习仓库保留完整历史（diff 需要）；CI 用 `fetch-depth` 按需拉取 |
| 范围蔓延 | 每个项目都翻不完 | `scope.include` 先聚焦核心模块；Coverage 按 scope 计算，而不是全仓库 |

---

## 14. 第一周可执行清单

```bash
# 1. 总仓库
git clone git@github.com:dajy5120/opensource-code-atlas.git
#    建立 §2.1 目录骨架、docs/adr、docs/conventions、terminology 种子、tools/osca（uv init --package）

# 2. 学习仓库（Phase 1 中由 osca new 自动化，这里是手工版本）
gh repo create dajy5120/osca-nautilus-trader --public   # 或 --private，视许可证决定
git clone https://github.com/nautechsystems/nautilus_trader osca-nautilus-trader
cd osca-nautilus-trader
git remote rename origin upstream
git remote add origin git@github.com:dajy5120/osca-nautilus-trader.git
git tag --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -2   # 选倒数第二个作为锚点
git switch -c mirror/develop <倒数第二个tag>
git switch -c study/zh-CN
#    新增 .osca/project.yaml、.osca/sync.yaml、.osca/CLAUDE.md、CLAUDE.md（或追加 import 行）
git push -u origin mirror/develop study/zh-CN
gh repo edit --default-branch study/zh-CN
gh workflow list   # 然后对所有上游工作流执行 gh workflow disable

# 3. 选第一个翻译目标：crates/model/src/orderbook/，按 comment-style 手工 + Claude Code 试写
```

完成标志：一个 PR 把 orderbook 模块的中文注释合入 `study/zh-CN`，并且该 PR 的 CI 中 `osca verify` 是绿色的。

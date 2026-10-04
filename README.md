# OpenSource Code Atlas（OSCA）

> **A living atlas for studying, annotating, translating, and tracking the evolution of classic open-source codebases.**
>
> 一个持续跟踪经典开源项目演进，用于源码学习、中文注释、架构分析和知识沉淀的开源源码地图。

本仓库是 OSCA 的**总索引**：只保存“地图”（项目登记、术语库、规范、模板、工具），不保存任何上游源码。每个被研究的开源项目都有一个独立的学习仓库 `osca-<project>`。

## 核心原则

- **保留上游原始源码**：`mirror/*` 分支永远等于官方源码。
- **中文注释直接写在源码里**：`study/zh-CN` = 上游源码 + 独占一行的 `【zh】` 注释。
- **代码一个字节都不改**：`strip_zh(study) == upstream@anchor`，由 `osca verify` 在 CI 与 Claude Code hook 中强制检查。
- **持续跟随上游**：记录锚点提交，按 release tag 增量同步，自动报告需要复核的注释。
- **架构分析用 Markdown**：放在学习仓库的 `osca/docs/`。

## 项目

<!-- osca:status:start -->
| 分类 | 项目 | 上游 | 学习仓库 | 锚点 | 落后 | 覆盖率 | 已审核 | 注释行 | 分析文档 |
|------|------|------|----------|------|------|--------|--------|--------|----------|
| Trading · Rust | NautilusTrader | [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) | [osca-nautilus-trader](https://github.com/dajy5120/osca-nautilus-trader) | v1.231.0 | ✓ 最新 | 0.3% | 0.0% | 145 | — |
| Trading · Rust · GUI | Flowsurface | [flowsurface-rs/flowsurface](https://github.com/flowsurface-rs/flowsurface) | [osca-flowsurface](https://github.com/dajy5120/osca-flowsurface) | v0.9.0 | ✓ 最新 | 1.9% | 0.0% | 108 | — |

> 由 `osca atlas status --write-readme` 自动生成于 2026-10-04 05:53 UTC；覆盖率按符号统计。
<!-- osca:status:end -->

> 进度表由 `aggregate` 工作流每日从各学习仓库的 `.osca/status.json` 与上游 tag 自动汇总（`osca atlas status --write-readme`）。

## 目录

```
catalog/        分类定义
projects/       项目登记（一项目一文件）
terminology/    统一术语库（跨项目共享）
templates/      学习仓库 copier 模板
tools/osca/     osca 命令行工具
docs/           实施方案、ADR、规范
```

## 快速开始

```bash
uv tool install "git+https://github.com/dajy5120/opensource-code-atlas#subdirectory=tools/osca"

cd osca-nautilus-trader
osca verify      # 检查剥离不变式
osca status      # 翻译覆盖率（符号级）
osca queue       # 需要复核的注释
osca translate crates/model --dry-run   # AI 批量注释（结构化补丁）
osca sync --dry-run

# 在本仓库
osca list                      # 分类树
osca atlas status --write-readme

# 接入新项目（约 1 分钟）
osca new <id> --upstream <url> --anchor <tag> --registry projects ...
cd osca-<id> && osca publish --public
```

## 文档

- [实施方案](docs/IMPLEMENTATION_PLAN.md)
- [ADR](docs/adr/README.md)
- [中文注释风格指南](docs/conventions/comment-style.md)
- [分支与同步操作手册](docs/conventions/branching.md)
- [架构分析文档规范](docs/conventions/analysis-docs.md)

## 路线图

- [x] Phase 0：规范、ADR、术语库
- [x] Phase 1：双源仓库 + `osca verify / status / sync`
- [x] Phase 2：Tree-sitter 符号索引、符号级增量检测、自动解冲突、复核队列
- [x] Phase 3：AI 翻译流水线（结构化注释补丁）、术语 lint、Claude Code 命令
- [x] Phase 4：`osca new / publish`、分析文档锚点、总仓库每日聚合、第二个项目（Flowsurface）

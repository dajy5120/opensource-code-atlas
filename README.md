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
| 分类 | 项目 | 上游 | 学习仓库 | 锚点 | 覆盖率 |
|------|------|------|----------|------|--------|
| Trading | NautilusTrader | [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) | [osca-nautilus-trader](https://github.com/dajy5120/osca-nautilus-trader) | v1.231.0 | orderbook 模块（9 文件） |
<!-- osca:status:end -->

> 进度表将在 Phase 4 由 CI 从各学习仓库的 `.osca/status.json` 自动生成。

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
osca status      # 翻译覆盖率
osca sync --dry-run
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
- [ ] Phase 2：Tree-sitter 符号索引、符号级增量检测
- [ ] Phase 3：AI 翻译流水线（结构化注释补丁）、审核命令
- [ ] Phase 4：模板化、总仓库状态聚合、第二个项目

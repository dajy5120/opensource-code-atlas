# 架构分析文档规范

分析文档放在学习仓库的 `osca/docs/` 下（不放 `docs/`，那是上游目录）：

```
osca/docs/
├── architecture/   # 总览、分层、依赖关系
├── modules/        # 按 crate / package 的模块分析
├── flows/          # 关键流程：订单生命周期、回测事件循环…
└── notes/          # 学习笔记、实验结论
```

## Front matter

```yaml
---
title: 订单生命周期
upstream_commit: 27a8e54e7ac3     # 写作/最近复核时的锚点
anchors:                           # 本文依赖的源码符号（Phase 2 起用于自动判断文档是否过时）
  - crates/execution/src/engine/mod.rs#ExecutionEngine
  - crates/risk/src/engine/mod.rs#RiskEngine
status: draft                      # draft | reviewed
---
```

## 写作要求

1. 先给结论（这个模块解决什么问题、核心抽象是什么），再展开细节。
2. 图示统一使用 Mermaid（GitHub 可直接渲染）。
3. 引用源码用 `路径:行号` 或符号 ID，并注明对应的 `upstream_commit`。
4. 与源码中 `【zh】` 注释分工：注释讲“这一段”，文档讲“这一片”。

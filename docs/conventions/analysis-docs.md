# 架构分析文档规范

分析文档放在学习仓库的 `osca/docs/` 下（不放 `docs/`，那是上游目录）：

```
osca/docs/
├── architecture/   # 总览、分层、依赖关系
├── modules/        # 按 crate / package 的模块分析
├── flows/          # 关键流程：订单生命周期、回测事件循环…
└── notes/          # 学习笔记、实验结论
```

## Front matter 与锚点

```yaml
---
title: 订单簿模块
status: draft                      # draft | reviewed（人工维护的写作状态）
anchors:                           # 本文依赖的源码（ADR 0006）
  - crates/model/src/orderbook/book.rs#OrderBook                       # 单个符号（符号 ID 见 `osca index --show <file>`）
  - crates/model/src/orderbook/book.rs#impl OrderBook::apply_delta_unchecked
  - crates/model/src/orderbook/ladder.rs                               # 整个文件：任一符号变化都算
---
```

- `osca docs update` 记录文档内容与每个锚点当时的代码哈希（`.osca/state/docs.jsonl`），并刷新 `status.json`。
- 上游改动锚点代码后，文档变为 **stale**：`osca docs list` 和同步报告会列出变化的锚点。
- 处理方式与注释相同：修改文档（编辑即刷新，再 `osca docs update`），或确认仍正确后 `osca docs approve <文档>`。
- 锚点不存在（符号被删除 / 改名）时文档为 **broken**，需要修正 `anchors`。
- 锚点宁精勿滥：选文档中**具体描述了其行为**的符号；整文件锚点只用于概述性文档。

## 写作要求

1. 先给结论（这个模块解决什么问题、核心抽象是什么），再展开细节。
2. 图示统一使用 Mermaid（GitHub 可直接渲染）。
3. 引用源码用 `路径:行号` 或符号 ID，并注明对应的 `upstream_commit`。
4. 与源码中 `【zh】` 注释分工：注释讲“这一段”，文档讲“这一片”。

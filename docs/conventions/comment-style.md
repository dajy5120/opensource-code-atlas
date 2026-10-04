# 中文注释风格指南

> 适用于所有 OSCA 学习仓库。格式规则由 `osca verify` 强制，内容规则由 Review 把关。

## 1. 格式（硬性）

1. **独占一行**，以 `[zh]` 开头：`// [zh] …`、`/// [zh] …`、`# [zh] …`。多行说明每一行都带标记。
2. **禁止行尾注释**：`let x = 1; // [zh] …` ✗
3. **禁止插入**字符串、原始字符串、docstring、rustdoc 代码块（```）内部。
4. **只增不改**：不修改、删除、移动任何上游行，包括空白、空行和英文注释。
5. **不运行格式化器**：学习分支上不要运行 rustfmt / ruff / black / pre-commit，也不要 `pre-commit install`。

## 2. 放置位置

### Rust

- 条目（struct / enum / fn / trait / impl / mod）有英文 `///` 文档时：把 `/// [zh]` 行**追加在英文文档之后、`#[...]` 属性之前**，这样 IDE 悬停能同时看到中英文。
- 条目没有英文文档、或位于函数体内部：使用 `// [zh]`（避免 `unused_doc_comments` lint 与 “doc comment 后无条目” 编译错误）。
- 模块级说明：在文件开头的 `//!` 文档之后追加 `//! [zh]`。

```rust
/// Provides an order book which can handle L1/L2/L3 granularity data.
/// [zh] 订单簿（Order Book）：维护单个交易品种的买卖盘口，
/// [zh] 支持 L1（最优报价）/ L2（按价位聚合）/ L3（逐笔订单）三种粒度。
#[derive(Clone, Debug)]
pub struct OrderBook {
    // [zh] 卖盘按价格升序排列，因此第一个价位就是最优卖价。
    pub asks: BookLadder,
```

### Python / Cython

- **不修改 docstring**（会改变运行时 `__doc__`）。
- 说明写在 `def` / `class` / `cdef class` **及其装饰器之上**；函数体内写在被说明语句之上。

```python
# [zh] 风险引擎：所有订单在送达执行引擎前都要经过这里的事前风控（Pre-trade Risk）检查。
@cython.final
cdef class RiskEngine(Component):
```

## 3. 内容

1. **翻译 + 讲解**，不是逐句直译。优先回答：这段代码**为什么**这样写？在系统中处于什么位置？有哪些不变式、边界情况？
2. **不复述代码**：`// [zh] i 加 1` ✗
3. **术语**以 `terminology/` 为准。首次出现写 `中文（English）`，之后可只写中文；`keep_english: true` 的词保留英文（trait、crate、Future、panic…）。注意 `avoid` 列表中的误译。
4. **长度**：单个条目的注释一般不超过 8 行；更长的分析写进 `osca/docs/`，在注释中引用：`// [zh] 详见 osca/docs/flows/order-lifecycle.md`。
5. **语气**：陈述句，中文标点，中英文之间加空格，代码标识符用反引号：`` `apply_delta` ``。
6. **不确定就标注**：`// [zh] 推测：…（待确认）`，Review 时重点检查。

## 4. 优先级

翻译资源有限时，按以下顺序：

1. 核心数据结构与 trait（例如 `OrderBook`、`Order`、`MessageBus`）；
2. 公共 API 与模块入口（`mod.rs`、`__init__.py`）；
3. 关键流程中的复杂函数（状态机、撮合、风控）；
4. 其余公共条目；
5. 私有辅助函数——通常只在逻辑不直观时注释。

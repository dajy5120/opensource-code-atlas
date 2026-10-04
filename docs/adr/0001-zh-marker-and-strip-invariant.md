# ADR 0001：`【zh】` 注释标记与剥离不变式

- 状态：Accepted
- 日期：2026-10-04（同日修订：标记由 `[zh]` 改为 `【zh】`，见文末）

## 背景

学习仓库要在上游源码中直接加入中文注释，同时满足：

1. 上游代码一个字节都不能被改动（保证可编译、可对照）；
2. 能长期跟随上游合并更新；
3. 能被工具统计、定位、剥离。

“能编译”只是一个弱保证：删掉一个 `+ 1`、调换两个参数，代码照样能编译。

## 决策

1. 每一行中文注释**独占一行**，注释体以 `【zh】` 开头：
   - Rust / C 系：`// 【zh】 …`、`/// 【zh】 …`、`//! 【zh】 …`
   - Python / Cython / Shell / TOML / YAML：`# 【zh】 …`
   - SQL / Lua：`-- 【zh】 …`
2. 不支持行尾注释、块注释（`/* */`），也不在 Markdown 等无行注释语法的文件中加注。
3. **剥离不变式**：对锚点提交 `A` 与学习分支工作区之间的每个差异文件 `f`：
   - `f` 位于 OSCA overlay（`.osca/`、`osca/`、`.github/workflows/osca-*.yml`）→ 放行；
   - `f` 是上游没有的 `.claude/**` 或 `CLAUDE.md` → 放行；
   - `f` 是上游的 `CLAUDE.md` → 只允许在末尾追加一行 `@.osca/CLAUDE.md`；
   - 否则必须满足 `strip_zh(study:f) == A:f`（逐字节）；
   - 不允许删除上游文件、不允许在 overlay 外新增文件。
4. 注释标记按文件类型识别：Rust 文件中的 `# 【zh】` 不会被识别（它在 Rust 中不是注释），因此会被判为代码改动。

由 `osca verify` 实现，在 CI、Claude Code hook 中强制执行。

## 后果

- 优点：毫秒到秒级、无需编译即可证明“除注释外代码未改动”；剥离后即得上游原文；`【zh】` 行可被精确计数，支撑覆盖率统计；合并冲突只可能出现在注释附近，且有确定的解决策略（上游代码优先，注释重新挂载）。
- 盲区：在多行字符串、docstring、rustdoc 代码块内部插入一行 `// 【zh】`，剥离检查能通过但改变了程序语义。Phase 2 用 Tree-sitter 位置检查兜底；在此之前由注释规范与人工 Review 约束。
- 代价：每行都要带标记，注释略显冗长；可接受。

## 修订：标记使用全角 `【zh】` 而非 `[zh]`（2026-10-04）

最初的标记是 ASCII 方括号 `[zh]`。在 NautilusTrader 试点中，为 `crates/model/src/orderbook/`
添加注释后：

- `osca verify` 通过（代码确实未改动）；
- `cargo check -p nautilus-model` 通过；
- **`cargo doc -p nautilus-model --no-deps` 失败**：63 处 `error: unresolved link to \`zh\``。

原因：rustdoc 把 `/// [zh]` 中的 `[zh]` 当作 Markdown 快捷引用链接，尝试解析为 intra-doc link，
而上游启用了 `rustdoc::broken_intra_doc_links` 并视为错误。Markdown 文档注释（Rust、Go 等）都有类似风险。

改用全角括号 `【zh】`：

- 不是任何 Markdown / rustdoc / Python 语法，不会被解析；
- 中文输入法下直接按 `[` `]` 即可输入；
- 在上游英文源码中几乎不可能自然出现，误判风险更低；
- 标记仍可通过 `.osca/project.yaml` 的 `study.marker` 配置。

教训：剥离不变式只保证“代码未改”，不保证“工具链无告警”。L1 CI 必须包含文档构建（`cargo doc`），
而不仅是 `cargo check`。

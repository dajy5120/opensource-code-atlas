# ADR 0005：AI 翻译流水线——结构化注释补丁

- 状态：Accepted
- 日期：2026-10-04

## 背景

几十个项目、数万个符号不可能全靠手写注释。但让模型直接编辑源文件有两个风险：模型可能改动代码，
而且产出无法按符号增量地追踪与复核。

## 决策

`osca translate` 采用**结构化注释补丁**：

1. **规划**：从状态模型（ADR 0003/0004）取出 `pending`（计入覆盖率的符号）与 `stale` 的符号，按文件分组，每个请求最多
   `ai.max_symbols_per_request` 个符号。
2. **请求**：
   - system：注释写作规则与输出约定（固定文本，启用 prompt caching）；
   - 文件块：带行号的完整源码（`#[cfg(test)]` / `mod tests` 折叠为一行占位，行号不变）+ 该文件命中的术语（启用缓存，同一文件的后续分块可复用）；
   - 目标块：符号 ID、范围、状态；stale 符号附原注释、变化类别（签名 / 英文文档 / 实现）与上次同步的上游 diff。
3. **输出**：JSON Schema 约束的结构化输出（`output_config.format`）：
   ```json
   {"annotations": [{"symbol": "...", "placement": "doc" | "line", "line": 123, "text": ["中文…"]}],
    "unchanged": ["仍然正确的 stale 符号"], "notes": "不确定之处"}
   ```
   模型只给出**纯文本**与位置；注释符号（`///` / `//` / `//!` / `#`）、【zh】 标记、缩进都由工具决定。
4. **应用**（每个文件全有或全无）：
   - 对返回了注释的符号，删除其原有 【zh】 行并插入新行（修订 = 完整替换）；
   - `"doc"` 由工具放到文档位置（Rust：英文 `///` 之后、属性之前；模块：`//!`；Python：`def` / `class` 之上）；
   - `"line"` 必须指向符号范围内的代码行，否则拒绝该符号；
   - 写入后运行 `osca verify`（剥离不变式 + 位置 lint），失败则整个文件回滚。
5. **状态**：AI 写入的注释记为 `translated`（`by: ai:<model>`），**永远不会**被标为 reviewed；
   模型认为仍正确的 stale 注释列入 `unchanged`，保持 stale，等人工 `osca review approve`。

### 模型与调用

- 默认模型 `claude-opus-5-5`，`effort: medium`（可在 `.osca/project.yaml` 的 `ai:` 中调整）。
- 同步调用使用流式（`max_tokens` 64000）并开启服务端拒绝回退（`fallbacks: "default"`，beta `server-side-fallback-2026-07-01`）；
  处理 `refusal` 与 `max_tokens` 停止原因。
- `--batch` 走 Message Batches API（半价、异步，最长 24 小时；Batches 不支持 `fallbacks`），每个文件一个请求；
  `osca translate --collect <batch_id>` 应用结果，提交后文件发生变化的结果会被拒绝。
- `--dry-run` 给出请求数、符号数和 token 粗估（区分缓存读取）。工作区不干净时拒绝运行，保证 AI 改动可以单独审查。

### 术语

`osca terms lint` 检查 【zh】 行中的 `avoid` 译法。为避免误报，只有当英文术语出现在注释所属符号的代码中（或注释本身）时才判定违规：
“填充”是 *fill* 的错误译法，但用来说 padding 完全正确。

## 后果

- 代码从构造上不会被模型修改；所有 AI 产出都经过与人工注释相同的 verify / lint / 状态流转。
- 质量依赖人工复核：`osca status` 中 Translated 与 Reviewed 分开统计。
- 成本可预估：orderbook 模块（8 个文件、141 个符号）约 12 万输入 token + 7 万缓存读取。

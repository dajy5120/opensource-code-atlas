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

### 后端与模型

| `ai.backend` | 计费 | 说明 |
|---|---|---|
| `claude-code`（默认） | Claude 订阅额度 | 调用本机 Claude Code：`claude -p --model … --tools "" --system-prompt … --json-schema …`，在临时目录运行（不加载项目 CLAUDE.md），读取 `structured_output` |
| `api` | API Key 按量计费 | Anthropic SDK，流式，prompt caching，服务端拒绝回退（`fallbacks: "default"`）；`--batch` / `--collect` 走 Message Batches（半价） |

默认模型 `claude-sonnet-5-5`，`effort: medium`；`--model` 可临时覆盖。

**试点对比（同一文件 `orderbook/display.rs`，4 个符号，订阅后端）：**

| 模型 | 耗时 | 输出 token | 等价标价 | 准确性 |
|---|---|---|---|---|
| claude-sonnet-5-5 | 13 秒 | 899 | ≈ $0.032 | 逐条核对全部正确，信息量更大 |
| claude-haiku-4-5 | 79 秒 | 10,365 | ≈ $0.068 | 把订单 `status` 过滤误译为“会员身份” |

结论：Haiku 单价虽低，但在这类代码讲解任务上输出更长、更慢，且出现事实错误，实际消耗反而更高。默认使用 Sonnet；
`analysis.rs`（5 个函数）用 Sonnet 15 秒完成，逐条核对无误，并在 `notes` 中主动标出不确定之处。

### 术语

`osca terms lint` 检查 【zh】 行中的 `avoid` 译法。为避免误报，只有当英文术语出现在注释所属符号的代码中（或注释本身）时才判定违规：
“填充”是 *fill* 的错误译法，但用来说 padding 完全正确。

## 后果

- 代码从构造上不会被模型修改；所有 AI 产出都经过与人工注释相同的 verify / lint / 状态流转。
- 质量依赖人工复核：`osca status` 中 Translated 与 Reviewed 分开统计。
- 成本可预估：`--dry-run` 给出 token 粗估；订阅后端每次运行打印等价标价，便于估算额度消耗。

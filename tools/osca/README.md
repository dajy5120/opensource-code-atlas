# osca

OpenSource Code Atlas 命令行工具。

```bash
uv tool install "git+https://github.com/dajy5120/opensource-code-atlas#subdirectory=tools/osca"
```

| 命令 | 作用 |
|------|------|
| `osca init --anchor <tag>` | 在学习仓库中记录初始锚点、创建 mirror 分支、接入 CLAUDE.md |
| `osca verify [FILES]` | 剥离不变式：`strip_zh(study) == upstream@anchor` |
| `osca verify --hook` | Claude Code PostToolUse hook 模式（违规时退出码 2） |
| `osca strip FILE...` | 输出去掉 `【zh】` 行后的源码 |
| `osca status [--json] [--write]` | 翻译覆盖率（符号级：reviewed / translated / stale / pending / orphaned） |
| `osca index [PATHS] [--show] [--write]` | 符号索引；`--write` 记录注释对应的代码哈希 |
| `osca queue [PREFIX] [--state stale]` | 复核队列，按 P0 签名 / P1 文档 / P2 实现排序 |
| `osca review approve TARGET...` | 确认注释仍然正确（符号 ID、文件或目录） |
| `osca impact OLD NEW` | 两个上游版本之间的符号级变化 |
| `osca resolve` | 解决合并冲突：上游代码优先，注释按符号重新挂载 |
| `osca sync [--to REF] [--dry-run] [--pr]` | 同步上游到 `sync/*` 分支并生成变更报告 |
| `osca sync --continue` | 自动解冲突失败、手工处理后完成同步 |
| `osca workflows disable` | 禁用学习仓库中所有非 `osca-*.yml` 的上游工作流 |
| `osca translate [PATHS] [--dry-run] [--batch]` | 用 Claude 为 pending / stale 符号生成结构化注释补丁（ADR 0005） |
| `osca translate --collect BATCH_ID` | 应用已完成的批处理结果 |
| `osca terms lint [PATHS]` | 检查 【zh】 行中 `avoid` 的译法（按上下文） |
| `osca terms validate [DIR]` | 校验术语库 |

`osca translate` 需要 Claude API 凭证（`ANTHROPIC_API_KEY`，或 `ant auth login` 的配置）。

开发：

```bash
uv sync && uv run pytest
```

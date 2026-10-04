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
| `osca strip FILE...` | 输出去掉 `[zh]` 行后的源码 |
| `osca status [--json] [--write]` | 翻译覆盖率（Phase 1：文件级） |
| `osca sync [--to REF] [--dry-run] [--pr]` | 同步上游到 `sync/*` 分支并生成变更报告 |
| `osca sync --continue` | 手工解决冲突后完成同步 |
| `osca workflows disable` | 禁用学习仓库中所有非 `osca-*.yml` 的上游工作流 |
| `osca terms validate [DIR]` | 校验术语库 |

开发：

```bash
uv sync && uv run pytest
```

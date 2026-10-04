# OSCA 总索引仓库

这是 OpenSource Code Atlas 的总仓库：项目登记、术语库、规范、模板和 `osca` 工具。**这里不存放任何上游源码。**

## 结构

- `projects/<id>.yaml`：一项目一文件；进度数字不手写（由 CI 聚合）。
- `terminology/*.yml`：术语库，修改后运行 `uv run --project tools/osca osca terms validate terminology`。
- `templates/study-repo/`：学习仓库 copier 模板（`_answers_file` 位于 `.osca/`，避免触碰上游路径）。
- `tools/osca/`：Python 3.12 + uv + typer。测试：`cd tools/osca && uv run pytest`。
- `docs/adr/`：重大设计变更先写 ADR。

## 不可违背的设计约束

1. 学习仓库中只允许整行 `【zh】` 注释与 `.osca/`、`osca/` 下的文件（ADR 0001）。
2. sync PR 必须以 merge commit 合并（ADR 0002）。
3. 翻译状态由工具派生，不手填（ADR 0003）。

修改 `osca verify` 的语义时，必须同步更新 ADR 0001 与 `docs/conventions/comment-style.md`，并补测试。

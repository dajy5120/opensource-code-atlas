# ADR 0008：定时同步与阅读站点

- 状态：Accepted
- 日期：2026-10-04

## 定时同步（osca-sync 工作流）

学习仓库每周一 02:00 UTC 运行 `osca sync --pr`（也可手动触发并指定 `to`）：拉取上游 tag，合并到 `sync/<tag>`，
自动解冲突（ADR 0004），以同步报告作为 PR 正文。已有打开的 sync PR 时跳过；每次运行后重新禁用上游新增的工作流。
`osca publish` 会打开“允许 GitHub Actions 创建 PR”的仓库设置。合并 sync PR 仍需人工，并且必须选择 merge commit。

**验证**：在 GitHub 上触发：已是最新版本时正确跳过；用 `to=upstream/main` 触发时被祖先检查拦下——
Flowsurface 的 `main` 并不包含 v0.9.0（发布 tag 不在主干历史上），这正是“跟踪 release tag 而不是开发分支”（ADR 0002）的理由。
PR 创建路径在临时克隆中验证（生成 PR，正文为报告，`mirror/main` 未变，随后关闭并删除）。

## 阅读站点（osca-site 工作流）

`osca site build` 生成静态站点，推送到 `study/zh-CN` 后由 GitHub Pages 发布（`https://<owner>.github.io/osca-<id>/`）：

- 首页：上游版本、覆盖率、审核数、过时数、分析文档、按目录分组的已注释文件与进度条；
- 源码页：上游代码（Pygments 高亮，深 / 浅色主题），【zh】 注释渲染为代码行之间的中文批注（换行合并为段落，列表项与空行分段），
  左侧符号目录按状态着色（已审核 / 已翻译 / 过时 / 待翻译），行号可链接；
- 文档页：Markdown（表格）+ Mermaid 图，源码锚点链接到对应源码页的行，变化的锚点标 ⚠。

只为有注释的文件生成源码页，站点体积随注释量增长（NautilusTrader 当前 1.9 MB）。
总仓库 README 的项目表增加“在线阅读”链接。

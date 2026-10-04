# ADR 0006：分析文档锚点与总仓库聚合

- 状态：Accepted
- 日期：2026-10-04

## 分析文档锚点

架构分析文档（`osca/docs/**.md`）与中文注释一样会随上游演进而过时，而且更难发现——文档描述的是跨文件的设计，
没有哪一行代码“紧挨着”它。

决策：文档在 front matter 中声明 `anchors`（符号 ID 或整个文件），沿用 ADR 0003 的派生状态模型：

| 状态 | 条件 |
|---|---|
| new | 新写或修改过，尚未 `osca docs update` 记录 |
| current | 记录时的锚点代码哈希与当前一致 |
| stale | 任一锚点的签名 / 文档 / 实现哈希变化（列出变化的锚点） |
| broken | 锚点符号或文件已不存在 |
| unanchored | 未声明锚点（不跟踪） |

`osca sync` 在合并前记录文档状态，同步报告新增“受影响的分析文档”一节。

**试点验证**：NautilusTrader 的 `osca/docs/modules/orderbook.md`（16 个锚点）置于 v1.230.0 后重放同步到 v1.231.0，
文档被判为 stale，变化的 5 个锚点（`apply_delta_unchecked`、`resolve_no_side_order`、`filtered_view_checked`、
`BookLadder::add`、`pre_process_order`）正是该版本语义变化的位置。

## 总仓库聚合

- 学习仓库在 `translate` / `review approve` / `index --write` / `docs update` / `sync` 之后自动刷新 `.osca/status.json`。
- `osca atlas status` 读取 `projects/*.yaml`，通过 raw.githubusercontent（私有仓库回退到 `gh api`）获取各学习仓库的
  `status.json` 与 `project.yaml`，用 `git ls-remote --tags` 计算落后上游几个 release，生成 README 进度表；`osca list` 输出分类树。
- `aggregate` 工作流每日运行，仅当表格内容（不含时间戳）变化时提交。

## 模板升级

模板改动通过 `uvx copier update -a .osca/copier-answers.yml --vcs-ref main` 回灌到各学习仓库；
冲突只会出现在项目自定义过的值上（如 `scope`、`generated`、`build`），保留项目值即可。

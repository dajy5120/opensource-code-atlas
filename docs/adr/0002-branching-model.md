# ADR 0002：学习仓库分支模型

- 状态：Accepted
- 日期：2026-10-04

## 决策

| 分支 | 含义 | 写入方式 |
|------|------|---------|
| `mirror/<upstream-branch>` | 官方源码镜像 | 仅 `osca sync` fast-forward |
| `study/zh-CN` | 默认分支：上游 + `[zh]` 注释 + OSCA 元数据 | 只通过 PR |
| `sync/<ref>` | 一次上游同步 | `osca sync` 生成，PR 必须 **merge commit** |
| `tr/<scope>` | 翻译工作 | PR，可 squash |
| `exp/<category>/<name>` | 实验（architecture / performance / api / strategy / prototype） | 自由修改，永不合回 |

1. 学习仓库是普通 GitHub 仓库 + `upstream` remote，**不使用 GitHub Fork**（避免 PR 误指向官方仓库、可独立设为私有）。
2. 镜像分支不叫 `upstream/main`：它与远程跟踪引用 `refs/remotes/upstream/main` 同名，Git 会报 ambiguous refname。
3. 上游更新以 **merge** 方式进入学习分支，不用 rebase：保留上游提交原样（SHA 不变），`git merge-base` 即可得到锚点，学习提交历史也不被改写。
4. sync PR 禁止 squash / rebase 合并：squash 会丢失上游父提交，下一次同步会把整段历史当作冲突。
5. 快速演进的项目跟踪 **release tag**（`upstream.track: tags`），而不是开发分支 HEAD，以降低注释返工频率。

## 后果

- `study/zh-CN` 的 first-parent 历史清晰地呈现“同步点 + 翻译提交”。
- 需要在 GitHub 仓库设置中允许 merge commit，并在 PR 模板中提示合并方式。

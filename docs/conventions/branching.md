# 分支与同步操作手册

决策依据见 [ADR 0002](../adr/0002-branching-model.md)。

## 日常翻译

```bash
git switch study/zh-CN && git pull
git switch -c tr/model-orderbook
# … 添加 【zh】 注释 …
osca verify
osca status
git commit -am "zh: crates/model orderbook"
git push -u origin tr/model-orderbook && gh pr create --base study/zh-CN
```

## 同步上游

```bash
git switch study/zh-CN && git pull
osca sync --dry-run          # 看看会同步到哪个 tag
osca sync --pr               # fetch → ff mirror → merge 到 sync/<tag> → 报告 → PR
```

有冲突时：

1. `osca sync` 会停下并列出冲突文件；
2. 冲突块中**上游代码永远优先**，再把仍然适用的 `【zh】` 行放回对应位置（不再适用的删除，并在报告中记下）；
3. `git add <files> && osca sync --continue`；
4. `git push origin mirror/<branch> sync/<tag>` 并开 PR。

**合并 sync PR 时必须选择 “Create a merge commit”。**

合并后，阅读 `.osca/reports/sync-*.md` 中“需要复核中文注释的文件”，开 `tr/review-<tag>` 分支逐一复核。

## 实验

```bash
git switch -c exp/performance/orderbook-bench study/zh-CN
# 任意修改；结论写进 osca/docs/notes/ 并通过普通 PR 合入 study/zh-CN
```

实验分支永远不合回 `study/zh-CN`。

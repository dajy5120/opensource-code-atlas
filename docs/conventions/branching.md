# 分支与同步操作手册

决策依据见 [ADR 0002](../adr/0002-branching-model.md)。

## 日常翻译

```bash
git switch study/zh-CN && git pull
git switch -c tr/model-orderbook
# … 添加 【zh】 注释 …
osca verify
osca index --write     # 记录新注释对应的代码哈希
osca status
git commit -am "zh: crates/model orderbook"
git push -u origin tr/model-orderbook && gh pr create --base study/zh-CN
```

## AI 批量注释

```bash
git switch -c tr/model-orderbook study/zh-CN
osca translate crates/model/src/orderbook --dry-run   # 计划与 token 估算
osca translate crates/model/src/orderbook             # 调用 Claude，逐文件 verify，失败回滚
git diff                                              # 人工审查
osca review approve <确认无误的符号或文件>
git commit -am "zh(ai): crates/model orderbook"
```

## 同步上游

```bash
git switch study/zh-CN && git pull
osca sync --dry-run          # 看看会同步到哪个 tag
osca sync --pr               # 记录注释状态 → ff mirror → merge 到 sync/<tag> → 自动解冲突 → 报告 → PR
```

冲突由 `osca sync` 自动解决（上游代码优先，中文注释按符号重新挂载，见 ADR 0004）。
极少数无法自动处理的文件（如被修改过的非源码文件）会让同步停下：手工解决后 `git add` 并运行 `osca sync --continue`。

**合并 sync PR 时必须选择 “Create a merge commit”。**

## 同步后的复核

```bash
osca queue                       # 需要复核的注释，按 P0 签名 / P1 英文文档 / P2 实现 排序
osca index --show <file>         # 查看某个文件中各符号的状态
```

对每一项：

- 注释已经不准确 → 直接修改 【zh】 行（编辑即视为重新翻译），然后 `osca index --write`；
- 注释仍然正确 → `osca review approve "<path>#<symbol>"`（也可以传文件或目录）。

提交时带上 `.osca/state/symbols.jsonl` 的变化。

## 实验

```bash
git switch -c exp/performance/orderbook-bench study/zh-CN
# 任意修改；结论写进 osca/docs/notes/ 并通过普通 PR 合入 study/zh-CN
```

实验分支永远不合回 `study/zh-CN`。

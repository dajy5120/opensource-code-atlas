# ADR 0003：派生式翻译状态

- 状态：Accepted（Phase 2 已实现符号级，见 ADR 0004）
- 日期：2026-10-04

## 背景

参考设计中每个文件维护一份手写的 `status: translated / needs_review`。在上千个文件、每月一次同步的规模下，手写状态必然与真实情况脱节。

## 决策

状态不存储、不手填，而是由工具从**事实**派生：

- Phase 1（文件级）：文件含 `【zh】` 行 → `translated`，否则 `pending`。
- Phase 2（符号级）：Tree-sitter 为每个符号计算 `sig` / `doc` / `body` 三个哈希（剔除 `【zh】` 行后）。`.osca/state/symbols.jsonl` 记录“注释写成时”与“审核通过时”的哈希；当前哈希与记录不一致即为 `stale`，审核记录随之失效。

| 派生状态 | 条件 |
|----------|------|
| pending | 无 `【zh】` 注释 |
| translated | 有注释，注释时哈希 == 当前哈希，无有效审核 |
| reviewed | 审核时记录的哈希 == 当前哈希 |
| stale | 注释时哈希 ≠ 当前哈希（并标明 sig / doc / body 哪类变化） |
| orphaned | 符号已被上游删除，注释残留 |

人工维护的配置（`.osca/project.yaml`）与工具写入的状态（`sync.yaml`、`state/`、`status.json`）分文件存放。

## 后果

- 上游改动后，审核过的注释自动失效为 stale，无需人记得改状态。
- 覆盖率可随时重新计算，不会“腐烂”。

## 实现说明（Phase 2）

- `.osca/state/symbols.jsonl` **只记录有注释的符号**（而不是全部 18,000+ 个符号），避免状态文件随上游频繁变动：

  ```json
  {"id": "crates/model/src/orderbook/book.rs#impl OrderBook::apply_delta",
   "at": {"sig": "…", "doc": "…", "body": "…"}, "zh": "…", "by": "osca",
   "review": {"by": "dajy5120", "at": "2026-10-05", "hashes": {"sig": "…", "doc": "…", "body": "…", "zh": "…"}}}
  ```

- 状态刷新规则：
  - **编辑注释 = 重新翻译**：`zh` 哈希变化时，`at` 刷新为当前代码哈希，审核记录作废；
  - **注释没改、代码变了 = stale**：`osca index --write` 不会刷新 stale 记录；
  - **确认注释仍正确**：`osca review approve <符号|文件|目录>`，状态变为 reviewed；
  - 符号重命名 / 移动：孤儿记录按相同的注释哈希迁移到新符号（随后因签名变化显示为 stale）。
- `osca sync` 在合并前自动记录注释状态，保证新写的注释也能在本次同步中被正确判为 stale。

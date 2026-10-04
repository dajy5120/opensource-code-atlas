# ADR 0007：更多语言与跨文件上下文

- 状态：Accepted
- 日期：2026-10-04

## 跨文件类型上下文

试译时模型多次在 `notes` 中说明“某类型定义在其他文件，描述为推断”。`osca translate` 现在为每个文件收集
目标符号中引用的 CamelCase 类型名，在翻译范围内查找其定义（struct / enum / trait / class / interface / type），
排除本文件已定义和有歧义（多个文件同名）的名字，最多附加 12 个定义、每个截断到 30 行，
作为“相关类型定义”放在可缓存的文件块中。Flowsurface `data/src/aggr/time.rs` 附加了 12 个定义、约 2.8K 字符，
其中包括此前模型看不到的 `KlineDataPoint`。

## 通用 tree-sitter 引擎

Go、TypeScript / TSX、JavaScript、C、C++ 共用 `lang/generic.py`：每种语言只声明一份 `Spec`
（条目节点 → 种类、命名函数、容器、包装节点 `export_statement` / `template_declaration`、字符串节点）。
Rust / Python / Cython 保留专用提取器。

| 语言 | 符号 | 符号 ID 示例 |
|---|---|---|
| Go | func、method（以接收者类型限定）、type | `server/client.go#client.processPub` |
| TS / JS | function、class + method、interface、type、enum、`const f = () => …` | `src/ky.ts#Ky.fetch` |
| C / C++ | function、struct / class / union / enum、namespace（容器） | `include/fmt/format.h#fmt::detail::write` |

冒烟测试（浅克隆）：NATS Server `server/*.go` 69 个文件 → 4,622 个符号，0.8 秒；ky 31 个文件 → 197 个符号；
fmt 20 个文件 → 198 个符号。

**局限**：宏密集的 C++（如 fmt 的 `FMT_BEGIN_NAMESPACE`）会让 tree-sitter 进入错误恢复。含解析错误的条目不作为符号，
容器的内容仍会被遍历；相关注释归入外层符号或 `<module>`。

`osca new` 的默认排除规则增加 `*_test.go`、`*.test.ts`、`*.spec.ts`、`testdata/`。

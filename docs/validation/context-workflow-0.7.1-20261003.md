# 前提、批处理与空闲原生压缩验证（0.7.1）

2026-10-03，在冻结基线 `8ead3fa` 之上完成路线D的三项既有授权验证与辅助用量统计闭环，版本保持 0.7.1。本记录只保留合成、脱敏观察；原始 session、探针报告、回执与日志保存在私有证据目录。正式验收由主会话按冻结合同执行，本文件不构成验收 PASS。

## 依赖与已核接口

- 已安装 Pi `1.0.0`（`node_modules/@earendil-works/pi-coding-agent`）源码核实：`AgentSession.compact()` 先 `await abort()`，成功才 `appendCompaction(...usage)`；失败/取消只发 `compaction_end` 与 `session_compact_failed`，不写成功条目。session 条目类型 `compaction`/`branch_summary` 带可选 `usage`，`usage` 条目带任意 `kind` 与 `usage`；JSON 模式 `round.jsonl` 透出 `compaction_start`/`compaction_end`。
- 脚本化 provider 固定 usage（input=10/output=5/totalTokens=15）只用于机制观察；SDK 的 `tokensBefore` 也引用该固定值，因此本记录不把它当真实 token 估计，更不声明费用节省。
- 活动 worker 不触发 compact：探针确认 `compact` 不在工具列表，活动态无任何 compaction 事件/条目，原任务完成且标记未被 abort。

## 三项验证（合成、真实 Pi SDK + QuickJS、本地 scripted provider）

### 1. 廉价前提与依赖阻断

固定合成输入 `ctx-input.txt` 缺失时，`tools.check` 结构化返回 `ok:false, exit:1` 并写不可变失败回执；脚本检查 `ok` 后不运行依赖命令，依赖 receipt 与 marker 都不存在。写入合成输入后，同一命令 `id` 的检查通过（exit 0），依赖命令真实执行并留下 exit 0 回执；原先的失败回执逐字节保持不变。

| 观察 | 缺失输入 | 修正输入后 |
| --- | --- | --- |
| 前提回执 exit | 1 | 0（另保留原 exit 1 回执） |
| 依赖回执 | 无 | 1 个，exit 0 |
| 依赖 marker | 无 | 存在 |
| 脚本判断 | `dependentSkipped: true` | 依赖真实执行 |

### 2. 直接 read 与 codemode 批处理的实测对照

同一组 3 个固定合成文件（约 6.6–6.8 KB/个，各 3 行 KEEP 标记）分别走直接多轮 `read` 与一次 codemode 批量读取/筛选；两条路径都真实读取每个文件，缺失文件在两边都显示为错误，筛选结果与独立按文件内容重算的期望一致。

| 观察 | 直接多轮 read | codemode 一次批处理 |
| --- | --- | --- |
| scripted-provider 请求数 | 5（3 次读取 + 1 次缺失读取 + 1 次回答） | 2（1 个脚本 + 1 次回答） |
| 本轮新增并进入后续请求的序列化工具结果字节 | 约 21.2 KB（所有文件正文随每轮请求重复进入） | 约 2.8 KB（一次筛选结果 + 嵌套调用记录） |
| 墙钟（单次观测） | 约 5 ms | 约 21 ms |
| 读取结果 | 3 个文件正文 + 缺失错误 | 与直接读取逐字节相同的文件正文 + 缺失错误 |

脚本化 provider 没有真实 token 计费，以上只是请求次数与字节的合成对照；墙钟为单机单次噪声内观察，不是性能承诺。

### 3. 空闲原生压缩与恢复安全

合成 12 轮历史后，仅在空闲会话调用真实 `session.compact()`：

- 成功条目：1 个 `compaction` 条目，usage 与两次 scripted 摘要调用之和一致（input 20/output 10/totalTokens 30）；`tokensBefore` 受固定 usage 影响，仅作机制观察。session 文件追加约 12 KB，旧字节前缀逐字节不变。
- 重新打开同一 session 后：session id、provider/model pin（`probe/scripted`）、codemode store 值、重新注入的合同、候选 head/dirty 与全部回执 hash 保持不变；失败回执仍为 exit 1，没有因摘要变成通过。
- 负控：摘要 error 响应、取消（先 `abortCompaction()` 再放行迟到的摘要流）、缺模型三种情况都抛错且不新增任何 compaction 条目；活动态负控中压缩事件为 0、条目数不变、任务标记写入成功。

## 辅助用量统计闭环

- `round.summary.json` 新增 `metrics.auxiliary`：只从显式 session 的严格本轮窗口（不含时间指标的 ±1s 容差）解析 `compaction`/`branch_summary`/`usage` 条目，按明确 entry id 去重，逐项报告种类/次数/known token/reported cost 与 known/incomplete/unknown。
- 旧 assistant `usage`/`usage_complete`/`reported_cost_usd` 字段语义不变；`metrics.total` 只在 assistant 与 auxiliary 都完整时给出合计，缺失侧保持 null，不重复计费。
- 缺 usage 的辅助条目不按 0；raw 有压缩失败/未完成信号而没有成功条目、缺 session 来源、身份缺失或多候选歧义时保持未知/incomplete；可靠且无信号的干净 session 报已知零。助手内层时间戳的窗口裁切不影响辅助完整性（辅助使用独立的 entry 级解析健康度）。
- 合成校准：18 项定向测试覆盖严格窗口、entry id 去重、部分/缺失 usage、失败/未完成信号、成功报告缺条目、总数完整性、多候选歧义与 board 聚合。真实 `session.compact` 生成的条目经 `pi_summary.summarize` 交叉核验，辅助种类/次数/usage/cost 与真实条目一致。
- 看板只聚合已存 summary（`derivedMetrics.auxiliary`、`auxiliaryUsage`、`totalComplete`），刷新时不解析 session、不重建 summary；旧 summary 保持未知，不原地改写。

## 边界与限制

- 本验证全部使用合成身份、临时路径和本地 scripted provider，无网络、无付费模型、无真实业务输入；它证明机制与保留语义，不证明真实任务质量、真实 token 节省或费用收益。
- 墙钟与字节是单机单次合成观察；直接读取的字节优势取决于文件大小与筛选比例，本记录不外推为普遍结论。
- `tokensBefore`/合成 usage 是 scripted provider 固定值，不能作为真实上下文估计；真实压缩的摘要质量、缓存前缀重建成本与费用影响仍未验证，因此不自动提前触发压缩、不修改全局自动压缩阈值、不暴露生产 compact 工具。
- 失败/取消负控覆盖真实 SDK 代码路径，但 provider 为本地脚本；未连接真实 provider 对照。
- 公开记录不含原始报告、session 文件、回执与私有路径；原始终态证据由主会话在私有目录核验。

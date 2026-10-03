# 前提、批处理与空闲原生压缩验证（0.7.1）

2026-10-03，在冻结基线 `8ead3fa` 之上完成路线D的三项既有授权验证与辅助用量统计闭环，版本保持 0.7.1。本记录只保留合成、脱敏观察；原始 session、探针报告、回执与日志保存在私有证据目录。正式验收由主会话按冻结合同执行，本文件不构成验收 PASS。

## 依赖与已核接口

- 已安装 Pi `1.0.0`（`node_modules/@earendil-works/pi-coding-agent`）源码核实：`AgentSession.compact()` 先 `await abort()`，成功才 `appendCompaction(...usage)`；失败/取消只发 `compaction_end` 与 `session_compact_failed`，不写成功条目。session 条目类型 `compaction`/`branch_summary` 带可选 `usage`，`usage` 条目带任意 `kind` 与 `usage`；JSON 模式 `round.jsonl` 透出 `compaction_start`/`compaction_end`。
- 脚本化 provider 固定 usage（input=10/output=5/totalTokens=15）只用于机制观察；SDK 的 `tokensBefore` 也引用该固定值，因此本记录不把它当真实 token 估计，更不声明费用节省。
- 活动 worker 不触发 compact：探针确认 `compact` 不在工具列表，活动态无任何 compaction 事件/条目，原任务完成且标记未被 abort。该结论限本插件不新增主动 compact；Pi 原生在 turn 间按默认阈值的自动压缩不受本验证覆盖。

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
| 本轮 case 工具结果新增的上下文体积（本 case 最后一次请求减去本 case 首请求的工具结果序列化字节，不是所有 provider 请求的累计传输量） | 约 21.2 KB（所有文件正文随每轮请求重复进入） | 约 2.8 KB（一次筛选结果 + 嵌套调用记录） |
| 墙钟（单次观测） | 约 5 ms | 约 21 ms |
| 读取结果 | 3 个文件正文 + 缺失错误 | 与直接读取逐字节相同的文件正文 + 缺失错误 |

脚本化 provider 没有真实 token 计费，以上只是请求次数与字节的合成对照；墙钟为单机单次噪声内观察，不是性能承诺。

### 3. 空闲原生压缩与恢复安全

合成 12 轮历史后，仅在空闲会话调用真实 `session.compact()`：

- 成功条目：1 个 `compaction` 条目，usage 与两次 scripted 摘要调用之和一致（input 20/output 10/totalTokens 30）；`tokensBefore` 受固定 usage 影响，仅作机制观察。session 文件追加约 12 KB，旧字节前缀逐字节不变。
- 重新打开同一 session 后：session id、provider/model pin（`probe/scripted`）、codemode store 值、重新注入的合同、候选 head/dirty 与全部回执 hash 保持不变；失败回执仍为 exit 1，没有因摘要变成通过。
- 负控：摘要 error 响应、取消（先 `abortCompaction()` 再放行迟到的摘要流）、缺模型三种情况都抛错且不新增任何 compaction 条目；活动态负控中压缩事件为 0、条目数不变、任务标记写入成功。该活动态观察只证明本插件未新增主动 compact、未暴露 compact 工具；它不能推广成 Pi 原生自动压缩永远不会在 run 内发生（原生仍可能在工具完成后的 turn 间按默认阈值触发），因此继续保留默认阈值、不提前自动压缩。

## 辅助用量统计闭环

- `round.summary.json` 新增 `metrics.auxiliary`：只从显式 session 的严格本轮窗口（不含时间指标的 ±1s 容差）解析 `compaction`/`branch_summary`/`usage` 条目，按明确 entry id 去重，逐项报告种类/次数/known token/reported cost 与 known/incomplete/unknown。
- 输入有效性：token 字段只接受有限非负整数（整数值的浮点也接受），cost 只接受有限非负数值；无效字段不参与已知和，逐字段记入 `invalidFields` 与叶子的 `invalid`，JSON 不输出 NaN/Infinity。缺值保持 unknown/partial。
- 身份冲突：同一明确 entry id 的字节/规范化内容一致重放可去重；内容不同的同 id 冲突显式计为 `identityConflicts`/`conflictedEntries`，冲突记录不参与任何已知和，结果与先后顺序无关（反转顺序得到相同结论），不按首个赢家计作准确费用；扫描期映射在汇总后丢弃，不新增持久身份库。
- 信号来源健康：`summarize` 将 round JSONL 的存在性与坏行计入完整性（`signalsVerified`）；可靠完整 round + 可靠无辅助 session 才报 known 0；round 缺失/坏行可隐藏 `compaction_start/end` 时保持 unknown/partial，不把实际未报告费用当 0。
- 旧 assistant `usage`/`usage_complete`/`reported_cost_usd` 字段语义不变；`metrics.total` 只在两侧都完整且值有限非负时给出合计，缺失/无效侧保持 null，不重复计费。
- 缺 usage 的辅助条目不按 0；raw 有压缩失败/未完成信号而没有成功条目、缺 session 来源、身份缺失或多候选歧义时保持未知/incomplete。助手内层时间戳的窗口裁切不影响辅助完整性（辅助使用独立的 entry 级解析健康度）。
- 合成校准：新增同 id 冲突正/反转负例、NaN/Infinity/负数/非整数/负 cost 输入有效性、round 坏行与缺失 log 信号健康、以及 board 非有限存储叶子不污染已知和的定向测试；原严格窗口、缺失/部分 usage、失败/未完成信号、成功报告缺条目、多候选歧义与 board 聚合用例保留不变。真实 `session.compact` 生成的条目经 `pi_summary.summarize` 交叉核验，辅助种类/次数/usage/cost 与真实条目一致。
- 看板只聚合已存 summary（`derivedMetrics.auxiliary`、`auxiliaryUsage`、`totalComplete`），刷新时不解析 session、不重建 summary；非有限存储值不进入已知和，旧 summary 保持未知，不原地改写。

## 边界与限制

- 本验证全部使用合成身份、临时路径和本地 scripted provider，无网络、无付费模型、无真实业务输入；它证明机制与保留语义，不证明真实任务质量、真实 token 节省或费用收益。
- 墙钟与字节是单机单次合成观察；直接读取的字节优势取决于文件大小与筛选比例，本记录不外推为普遍结论。
- `tokensBefore`/合成 usage 是 scripted provider 固定值，不能作为真实上下文估计；真实压缩的摘要质量、缓存前缀重建成本与费用影响仍未验证，因此不自动提前触发压缩、不修改全局自动压缩阈值、不暴露生产 compact 工具。
- 失败/取消负控覆盖真实 SDK 代码路径，但 provider 为本地脚本；未连接真实 provider 对照。
- 公开记录不含原始报告、session 文件、回执与私有路径；原始终态证据由主会话在私有目录核验。

## 主会话最终核验

主会话独立复现非法数值与同ID冲突反序负控，确认unknown/partial及JSON有限性；核验准确干净候选、全部六项回执和日志SHA-256。400项完整回归、25项定向、8项上下文、10项基础Pi、5组并发与隐私检查均通过。主会话另行复跑8项真实上下文探针通过，原始观察留在私有目录。交付已接受并整合至本地main，未发布或重装。

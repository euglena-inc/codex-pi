# 流式日志与运行指标验证（0.7.1）

2026-10-03，在冻结基线 `1339726` 之上实现路线B的流式日志与 summary/metrics 派生指标，版本保持 0.7.1；随后按 round-1 审查的三组同根因缺口返修：不可读日志的事实保留、汇总/回执核验的端到端内存边界、指标 completeness 与来源身份统一保守。本记录只保留合成、脱敏结论；原始回执、日志与测量保存在私有证据目录，正式验收由主会话按冻结合同执行。旧实现、旧回执和历史会话均未改写。

## 范围与事实

| 项目 | 结果 |
| --- | --- |
| 定向测试 `tests/test_runtime_efficiency.py` | 38 项通过、零跳过：跨块多字节、Go/unittest 优先级与最后 `Ran`、重复/矛盾/超长/空/缺失/非法 UTF-8、尾部行数与字节上限、hash/计数/尾部同次读取、check 回执 hash 对照、权限/中途读取失败负控、窗口过滤、并集不求和、开区间并集、坏时间戳、缺 session/窗口返回 null、session header id 校验与多候选歧义、多轮不混窗、不读其他任务、usage 去重与启发式 identity、check known/complete、看板按轮次 known/incomplete/unknown、版本保持 0.7.1 |
| 其他受影响模块开发复跑 | 预算/回执/看板/生命周期/终态/模块/守卫/阶段/归档/交付卡等受影响测试复跑通过；这些是开发运行，不等于合同回执 |
| benchmark 合成 10/200MiB | 同输入 helper→终态 summary 两条路径均验证回执 hash；旧新 hash、Go verbose 计数、尾部与独立 expected facts 一致；计时 fixture 已知正确总量一致 |
| 完整回归 | 本记录不包含完整回归；最终由固定候选的合同 `regression` check（`final:true`）执行，实际次数记入交付报告 |

## Round-2 缺口复现与修复

1. **读取失败误作空日志**：`analyze_log` 曾 `except OSError` 返回空 SHA256/bytes 0，`Path.open` 的 `PermissionError` 被伪装成“空日志成功”。现在只有 `FileNotFoundError`（确实不存在）保留历史空日志语义；已有文件权限失败和读取中途 I/O 失败直接抛出，不写回执。负控覆盖 chmod 000 的开始失败（原文件保留、无 `.json` 回执、无 running 标记）、模拟中途读失败，以及真实 `pi_check.py` 的权限失败路径。
2. **端到端内存不有界**：`pi_summary.receipts` 曾 `hashlib.sha256(log.read_bytes())`，终态 summary/result 会再次整读大日志。现在统一用 `pi_size.sha256_file` 流式核验，保持“不存在/不可读→不可信、原始日志不动”的原证语义；benchmark 在同一独立子进程序列中顺序执行 helper 与 summary，峰值取两者最大值。
3. **完整性/来源不统一**：一条无 `toolCall` 的 assistant 加一条 `toolCallId` 不匹配的 toolResult 曾得到 `status=ok`、`toolSeconds=0`；现在 tool 完整性要求无未闭合区间且无孤儿 toolResult，已知的测得并集仍保留但 `complete=false`、status `partial`。只有一条有时间的时间回执曾标 `label=exact` 且被看板记为完整，现在 `elapsedSeconds.complete` 要求无缺失时间且无 unknown 退出，已知部分保留、看板按轮列出 known/incomplete/unknown。模型区间在会话坏行下不标完整。`openToolSeconds` 改为开区间并集（并发未闭合调用不再相加）。session 文件按持久 header 的 `id` 校验，多候选同 id 报 `session_identity_ambiguous`，不按文件名排序猜权威。usage 用统一 unique 计数（无 timestamp 但已累计的消息不再漏计）；identity 优先 `responseId`，时间+usage 仅作启发式并标 `identityReliable=false`；不同消息不会被错误合并，无法确定时保持 `unknown`。

## 旧/新合成对照（macOS，单机单次顺序观测，helper→summary）

| 输入 | 旧 wall | 新 wall | 旧 peak RSS | 新 peak RSS |
| --- | --- | --- | --- | --- |
| 10 MiB | ~0.28 s | ~0.44 s | ~81.1 MiB | ~25.4 MiB |
| 200 MiB | ~4.28 s | ~7.37 s | ~1179.8 MiB | ~25.3 MiB |

- 候选 200MiB 相对 10MiB 峰值增量约 -0.05 MiB（噪声内，合同限 32 MiB）；同输入 200MiB 峰值比旧实现少约 1.154 GiB。summary 单步 wall 旧约 0.11 s、新约 0.10 s，但旧路径峰值随日志线性增长。
- wall 只记录事实、不声明倍速。该合成场景 helper 候选慢于旧实现：新路径以有界内存换取逐行边界处理；实际 check 日志通常远小于 10MiB。
- 旧/新 hash、Go verbose 计数与尾部完全一致；对照运行的是基线提交物化的只读 helper 与 summary，未修改旧实现。
- 除合成日志的本地 hash 生成外无网络、模型或业务原文。

## 指标定义与未知边界

- 模型响应区间：原生持久 session 中 assistant 消息内部 `timestamp` 到外层条目写入 `timestamp`；只取调用方显式传入的 session 目录/id 中、落于本轮窗口的条目。窗口为轮次起止秒，+1s 容差只吸收同一 wall clock 抖动，不跨轮合并。
- 工具区间：assistant 条目写入时间到匹配 `toolCallId` 的 toolResult 内部时间；重叠、并发与父子区间取并集，不与模型区间相加。未闭合工具与无法匹配的 toolResult 使 `toolSeconds.complete=false`，未闭合时长的 coverage 也按区间并集计算。
- 未归因余量：仅当模型与工具都完整时给出；否则 null，不给出会下游误用的部分值。
- check 已完成耗时：仅累计退出码已知且未中断/取消回执的 `ended_at - started_at`；零次检查是已知的 0。缺时间或 unknown 退出时保留已知和但 `complete=false`，board 把该轮列入 incomplete。
- usage：只累计唯一 assistant 最终 `message_end`；`reasoning` 属于 `output` 子项，不重复相加；`agent_end`/`turn_end`/`message_update` 不累计；仅按带provider/model命名空间的明确responseId或message id去重，`usage_complete`使用实际去重后记录数。无可靠身份的记录逐条计入并标明identity不可靠，绝不依据时间戳/用量猜测合并。
- 缺 session、缺窗口、session 无本轮条目、header 不匹配/歧义、非法或超长记录：新指标为 `null` + label/reason，不输出假 0；旧 summary 缺 `metrics` 或 `complete` 时看板按 unknown/incomplete 处理且不改写原证据。
- 看板周期刷新只读已有状态、回执和 summary，不扫描会话；精确日志 hash 只在 check 回执路径生成。
- 超长单行超过硬上限时整个计数保持 unknown，丢弃的前缀不会被重新锚定为新行；原始日志仍完整落盘。

## 限制

- 峰值 RSS 与 wall 是单一 macOS 主机、合成日志的一次顺序对照，不是普遍保证；Linux 单位由 benchmark 换算为字节并在结果中标注。
- 本记录不构成验收 PASS；正式定向、回归、benchmark、原生 codemode 探针与隐私扫描的回执见主会话审查时引用的私有证据。

主会话接管修复：独立反例确认同timestamp/usage的不同响应不得合并；修复后两条输入各10的记录合计20，明确响应ID的重复仍只计一次。孤立toolResult没有可测区间时返回null而不是0。原始Pi候选及两次质量拒绝记录保留。

## 主会话最终核验

接管候选已完成39项定向、364项完整回归、10项真实Pi探针、端到端benchmark和隐私扫描，均退出0，测试零跳过。回执绑定同一干净候选，日志hash独立核验；完整回归本次运行一次约368秒。已整合到本地主分支，原Pi失败交付未被追认，接管交接事件已独立解决。

本次端到端200MiB合成输入旧/新峰值RSS为1237008384/26607616字节；候选10→200MiB峰值仅增加16384字节。helper加summary合计旧/新墙钟约4.20/7.47秒，数据与hash/计数/尾部一致。该结果支持内存有界，不代表整体提速；测试及后台负载环境不能视为严格空闲机器的吞吐基准。

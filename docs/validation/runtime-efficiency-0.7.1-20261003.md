# 流式日志与运行指标验证（0.7.1）

2026-10-03，在冻结基线 `1339726` 之上实现路线B的流式日志与 summary/metrics 派生指标，版本保持 0.7.1。本记录只保留合成、脱敏结论；原始回执、日志与测量保存在私有证据目录，正式验收由主会话按冻结合同执行。旧实现、旧回执和历史会话均未改写。

## 范围与事实

| 项目 | 结果 |
| --- | --- |
| 定向测试 `tests/test_runtime_efficiency.py` | 21 项通过、零跳过：跨块多字节、Go/unittest 优先级与最后 `Ran`、重复/矛盾/超长/空/缺失/非法 UTF-8、尾部行数与字节上限、hash/计数/尾部同次读取、check 回执 hash 对照、窗口过滤、并集不求和、开区间与坏时间戳、缺 session/窗口返回 null、usage 去重与 reasoning 子项、check 完成耗时、看板聚合不扫会话且不改写旧 summary、版本保持 0.7.1 |
| 其他受影响模块开发复跑 | 预算/回执/看板/生命周期/终态/模块/守卫/阶段/归档/交付卡等受影响测试复跑通过；这些是开发运行，不等于合同回执 |
| benchmark 合成 10/200MiB | 旧新 hash、Go verbose 计数、尾部与独立 expected facts 一致；计时 fixture 已知正确总量一致 |
| 完整回归 | 本记录不包含完整回归；最终由固定候选的合同 `regression` check（`final:true`）执行，实际次数记入交付报告 |

## 旧/新合成对照（macOS，单机单次顺序观测）

| 输入 | 旧 wall | 新 wall | 旧 peak RSS | 新 peak RSS |
| --- | --- | --- | --- | --- |
| 10 MiB | ~0.27 s | ~0.49 s | ~79.9 MiB | ~24.6 MiB |
| 200 MiB | ~4.28 s | ~7.49 s | ~1179.8 MiB | ~24.7 MiB |

- 候选 200MiB 相对 10MiB 峰值增量约 0.14 MiB（合同限 32 MiB）；同输入 200MiB 峰值比旧实现少约 1.128 GiB。
- wall 只记录事实、不声明倍速。该合成场景候选慢于旧实现：新路径以有界内存换取逐行边界处理；实际 check 日志通常远小于 10MiB。
- 旧/新 hash、Go verbose 计数与尾部完全一致；对照运行的是基线提交物化的只读 helper，未修改旧实现。
- 除合成日志的本地 hash 生成外无网络、模型或业务原文。

## 指标定义与未知边界

- 模型响应区间：原生持久 session 中 assistant 消息内部 `timestamp` 到外层条目写入 `timestamp`；只取调用方显式传入的 session 目录/id 中、落于本轮窗口的条目。窗口为轮次起止秒，+1s 容差只吸收同一 wall clock 抖动，不跨轮合并。
- 工具区间：assistant 条目写入时间到匹配 `toolCallId` 的 toolResult 内部时间；重叠、并发与父子区间取并集，不与模型区间相加；未闭合工具不计入并集，并显式计入 coverage。
- 未归因余量：本轮 wall 减模型/工具并集，标 `estimated`，不是接受判定。
- check 已完成耗时：仅累计退出码已知且未中断/取消回执的 `ended_at - started_at`；中断与未知项不制造完成耗时。
- usage：只累计唯一 assistant 最终 `message_end`；`reasoning` 属于 `output` 子项，不重复相加；`agent_end`/`turn_end`/`message_update` 不累计。
- 缺 session、缺窗口、session 无本轮条目、非法或超长记录：新指标为 `null` + label/reason，不输出假 0；旧 summary 缺 `metrics` 时看板聚合标记未知且不改写原证据。
- 看板周期刷新只读已有状态、回执和 summary，不扫描会话；精确日志 hash 只在 check 回执路径生成。
- 超长单行超过硬上限时整个计数保持 unknown，丢弃的前缀不会被重新锚定为新行；原始日志仍完整落盘。

## 限制

- 峰值 RSS 与 wall 是单一 macOS 主机、合成日志的一次顺序对照，不是普遍保证；Linux 单位由 benchmark 换算为字节并在结果中标注。
- 本记录不构成验收 PASS；正式定向、回归、benchmark、原生 codemode 探针与隐私扫描的回执见主会话审查时引用的私有证据。

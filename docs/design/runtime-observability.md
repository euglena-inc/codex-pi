# 插件流式日志与运行开销观测

## 范围和基线

承接已接受的0.7.1预算/局部检查机制，完成 worker-efficiency 路线B。保留Python+TypeScript架构、Pi原生工具和SQLite。只改插件日志处理、summary/metrics必要接线、测试、一个复用型性能验证脚本及说明。不改消费项目、并发调度、compaction、模型政策、发布和缓存；版本保持0.7.1至本轮整体整合。主会话在合同中冻结准确baseline。

## 已确认问题与设计

pi_check在子进程结束后整文件read_bytes、decode、regex多遍读取，内存随日志体积增长；hash和计数可以流式完成。监督循环sleep与board有界状态读取不是已证明的持续CPU瓶颈，不盲改刷新周期或加缓存库。

1. 一个流式日志分析入口同时产出完整SHA-256、原语义测试计数和有限log_tail。固定大小字节块，增量UTF-8处理，有限行状态和尾缓冲。保留最后一个Ran摘要以及随后首个非空结果行的现有保守语义；Go verbose计数仍优先。无计数、截断、矛盾和超长不可判行保持unknown，不能丢前缀后误匹配。兼容现有unittest_counts/log_tail可调用接口，但不要维护两份计数算法。
2. 完整原始日志仍落盘，receipt的真实exit/timeout/cancel/resource字段、head/dirty、hash/原子发布不变。不存在日志时仍按原行为报告。流式hash与摘要必须来自同次读取；不要为尾部再解码完整文件。超长单行状态要有硬上限。
3. 扩展既有round.summary和board metrics，保留旧字段：未缓存输入/缓存读取/输出/缓存比例，请求上下文首末峰值，真实check次数/已完成耗时/中断或未知项，模型响应区间、工具区间并集及未归因余量，标注exact/estimated/unknown。只统计唯一assistant最终usage，reasoning是output子项，不重复turn_end/agent_end/delta。provider费用保持reported。
4. 模型响应时间用原生持久session的assistant内部timestamp至外层写入timestamp；工具用实际可用时间边界，父子/并发区间取并集，不简单相加。调用方显式传入session来源/轮次时间窗口，避免猜目录或扫描其他任务。没有可靠来源就null及coverage/reason，不能输出假0；中断、部分尾部、缺时间戳、重复和多轮恢复都要覆盖。此派生统计不是接受判定。
5. summary在原终态汇总/显式查询路径计算；board周期刷新不能新增全量会话读取。metrics复用summary的派生数据，旧summary新增指标未知、不原地改旧证据。若当前源不足，不加大而全的重放或状态框架；最小只读接线与小模块即可。readiness/最终log验证仍按原证执行。
6. 修正check拒绝文案“raise timeoutSeconds only if estimate is wrong”的倒置语义：只能在授权上限内调整timeout以覆盖可信估计，或有证据后修正估计；不能鼓励编低估计或扩预算。仅文案，不改已接受准入行为。

## 可复现验证

新增 tests/test_runtime_efficiency.py：用独立expected facts验证多字节跨块、尾部、Go/unittest优先级、重复/非法/超长行、空文件、无计数；串行/并行/嵌套时间、部分/取消/恢复多轮、无usage/无session、缓存比例与reasoning不叠加。修改既有受影响测试，禁止镜像实现当oracle。

提供 scripts/benchmark_runtime.py --baseline-ref REF：从给定本仓Git基线物化所需只读helper到临时目录，不动当前源码/历史。旧新实现分别在独立子进程分析相同10MiB和200MiB合成日志，生成器分块写入，比较hash/计数/尾部并测wall及peak RSS（区分macOS/Linux单位）。候选200MiB相对10MiB的峰值增量应不超过32MiB，并显著小于同输入旧实现；超出则非零并保留证据，不skip。记录时间而不强求无依据倍速。顺序运行内存对照，峰值不叠加；无网络/模型/业务原文，输出简短结果，原始测量保留private路径。也记录计时指标合成fixture的已知正确总量。

本阶段先跑必要定向测试，最终提交后用新check执行：定向test_runtime_efficiency、benchmark（REF见合同）、完整Python回归、真实Pi/QuickJS探针、隐私检查。完整回归声明targetedCommand并用final:true，预计480秒、timeout900秒；先跑便宜前提，测试过程中固定candidate，无必要不重复全量。不得把benchmark合成速度当整个业务任务提速。

交付完整候选、逐项真实回执、旧新内存/时间对照、指标定义/未知边界和实际全量次数。原始证据私有，公开仅合成和脱敏结论。提交前隐私检查。主会话审查整合后才冻结路线C的多核/内存准入，不把其接口设计留在本阶段扩大实施。

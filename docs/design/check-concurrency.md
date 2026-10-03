# 插件内有界检查并发

## 前提、目标和范围

依赖预算/targeted机制及流式日志/指标成果被主会话接受后，才派发本阶段。保持一个Pi实施worker，利用原生codemode并发其独立check；不增加模型worker、Rust、daemon、数据库、全机调度器或业务项目改动。版本保持本轮未发布0.7.1。准确baseline在合同冻结；本文件是后续设计，不是已实现声明。

本机14核/36GiB仅用于本次对照，不写死为公共默认。目标是可解释的有界并发、内存有余量、取消/截止不失效；不追求满核。未知资源、浏览器/容器或共享资源默认独占。改动仅限插件runtime、测试、现有合成probe扩展、协作指引与脱敏验证。

## 最小协议

复用phase合同和worker.json，不新增配置文件。phase可选checkExecution对象：maxConcurrent（正整数，最多4）、cpuSlots（正整数，不超过当前可见CPU数的有效cap）、memoryMiB（有限正数，进程内估算总预算）。acceptanceItems可选checkResources对象：parallelSafe（bool）、cpuSlots（正整数）、memoryMiB（正数）、exclusiveKeys（最多16个非空短字符串）。metadata按规范化argv匹配，同argv矛盾声明在合同验证时拒绝；Pi工具调用参数不能下调冻结资源需求。

未声明checkExecution时新worker按单路执行check；未声明某命令资源或parallelSafe!=true时该check独占，避免外层allSettled意外启动多个重型检查。旧冻结helper/旧会话证据不改写。缺字段的旧合同hash不漂移。显式资源需求超过池容量时立即结构化拒绝，不进入永远等不到的队列，不伪造receipt。

在TypeScript扩展中只设一个小型、每worker进程内的许可池。资源预算涵盖声明的子runner并发；CPU slot是估计占用、非CPU affinity，memory是准入估计、非OS硬上限。用户没有声明时不猜测内存峰值。独占任务等待池空闲；并行安全任务受数量/CPU/memory和exclusiveKeys共同约束。先到先得避免独占任务饥饿，不持有board或SQLite锁，不持久化第二任务状态。

## 等待、deadline、压力与失败

所有拒绝返回ok:false、receipt:null、明确reason；排队本身不是检查回执。信号取消、参数错误、超时和异常都释放许可，不杀非本任务进程。等待时间从原round/codemode有效预算扣除，队列不会延长预算；等待结束、真正spawn前重新核验预算、final的候选clean和资源条件。不得只在入队时核验。工具结果增加queueSeconds、采用的资源声明/可见压力状态等紧凑观测，不新增大日志。

macOS优先用只读sysctl kern.memorystatus_vm_pressure_level（已核本机1表示当前正常）；Linux使用/proc/meminfo的MemAvailable/总量作明确标注的可用量指标，不能等同同种压力分级。跨平台探测通过一处小helper/函数，不引入psutil等重依赖。查询有短超时/输出上限；高压力或Linux可用量不足时拒绝新增check并保留运行中任务；压力未知时只允许独占串行，不宣称资源充足。不要自动改Docker内存或系统设置。

内存压力仅在实际准入边界采样，不能靠轮询声称硬限制。运行中未知进程/脱离进程组/其他应用/其他插件实例不受池控制；明确写入文档。本轮无跨实例全机硬额度，不能宣称36GiB机器绝不超用。若证据以后证明必须跨实例协调，再另行设计。

## 实测策略与验收

初始实测1路与2路，资源和效果允许再测4路。CPU受控实验总声明先不超过8 slots，内存估算总额先不超过8GiB；是本次实验起点、不是默认机器配置。Docker/浏览器不进入首轮并发实验。使用临时目录、独立marker和有限CPU/I/O/内存子进程，负载相同，输出完整、并行不改源码。

沿用现有真实Pi SDK scripted-provider探针的方式，扩展或复用现有验证脚本，不能仅用假的ExtensionAPI声称并发真实生效。用barrier/独立时间戳证明实际overlap、最大并发/CPU/memory预留未超；共享key/未知任务独占；超容量拒绝；排队取消释放；候选在等待期间变dirty或预算耗尽时不spawn；codemode外层截止仍受控。资源压力正反例可注入且须标记合成，另记录本机真实采样，不通过改系统制造压力。

对同一候选同一负载顺序运行1/2/4路比较墙钟、各子进程RSS与实际并发、残留进程。RSS的共享页/子孙/容器边界明确标注，不拿累计CPU时间当墙钟。2路更慢则不强行宣称提速；正确性、memory未知降级和检查覆盖是硬验收。报告最适合本机的实测并发，而不是按核数自动开满。

开发先定向测试，最终固定候选后原完整Python回归、真实Pi/QuickJS、上述真实并发探针/对照和隐私检查。phase自身完整回归保持独占，最终检查资源参数由Main冻结。不得为实现并发一次性把现有suite全部parallelize或重写业务测试。最终报告实际check数量、等待/执行时间、压力快照、序列化负例、每路对照和限制；主会话核原证后再接受。

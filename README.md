# Codex + Pi

当前 Codex 主任务负责设计、派工与验收；Pi 的模型由项目配置选择（模型政策只在 [协作 Skill](skills/collaborate/SKILL.md) 中陈述）。项目只需 `.agents/codex-pi.json` 和自己的领域约束，公共插件维护进程、同会话返修、看板和原始证据。

## 0.7.1 检查预算、流式日志与指标

`check` 在创建子进程前重读本轮真实 `deadlineAt`，固定预留 60 秒收尾。预计耗时与 timeout 分离：调用与合同的有限正数估计取较大值，均无估计时以有效的请求/合同上限作保守需求，不把阶段剩余缩小当作预计耗时。先算最终有效执行窗口（请求/合同上限、remaining−60、codemode 外层−清理余量），需求超过该窗口时返回结构化 `ok:false`（含 reason、requiredSeconds、allowedSeconds、remainingSeconds、reserveSeconds、`receipt:null`），不 spawn、不写回执、不产生零测试假回执；期限读取/身份校验失败同样拒绝。实际传入 helper 的 timeout 就是该窗口本身，保留小数、不向下取整。codemode 脚本内的嵌套检查同时计入该脚本自身的外层期限；扩展无法验证外层期限时拒绝并提示直接调用 `check`，已准入的检查不会被外层期限提前杀死。真实执行的返回带实际 `elapsedSeconds`、`allowedSeconds` 与采用的 `estimateSource`，估计不被写成实际耗时。拒绝提示只能在授权上限内调整 timeout 以覆盖可信估计，或在有证据时修正估计，不鼓励编低估计或扩预算。

合同验收项可选 `targetedCommand`（仅局部调试建议，永不替代正式 command）与 `estimatedSeconds`；同一规范化 argv 的元数据矛盾在合同验证时拒绝。与带 `targetedCommand` 的全量命令 argv 相同的 `check` 默认拒绝并返回该建议，只有显式 `final:true` 且工作树干净才运行；匹配按 argv 而非 id，定向检查不能覆盖正式验收。不自动缓存 PASS、不制造复用回执、不限制全量只跑一次：定向通过、固定最终提交后全量仍须真实执行。没有新服务、状态库或通用调度器；旧冻结 worker 与缺少可选字段的旧合同保持原行为。

检查日志改为单次流式分析：固定字节块同时产出完整 SHA-256、保守的 Go/unittest 计数和有限 `log_tail`，不为尾部再解码整个文件。多字节跨块、重复/矛盾摘要、空/缺失与非法 UTF-8 保持原有保守语义；超过硬上限的单行保持 unknown，不丢前缀后误匹配。只有日志确实不存在才按历史上的空日志兼容处理；已有文件权限失败或读取中途 I/O 失败显式失败，不生成看似正常的完整 hash 或成功回执。原始日志仍完整落盘，回执的 exit/timeout/cancel/resource、head/dirty 和 hash/原子发布语义不变。summary 的终态回执核验也用流式 hash，不再把大检查日志整读进内存。

`round.summary.json` 新增 `metrics` 紧凑分解：未缓存输入/缓存读取/输出/缓存比例、请求上下文首末峰值、真实 check 次数与已完成耗时（中断/未知不计入）、模型响应区间、工具区间并集及未归因余量，并标注 exact/estimated/unknown。usage 只累计唯一 assistant 最终消息，reasoning 是 output 子项不重复相加，重复投递的 `agent_end`/`turn_end` 不累计。时间指标由调用方显式传入 session 目录/id 与本轮时间窗口，从原生持久 session 的写入时间和消息内部时间派生；没有可靠来源返回 null 和 reason，不输出假 0。每个派生指标区分 known/coverage/complete：已知部分可保留，但缺失、坏行、未闭合工具、无法匹配的 toolResult 或缺少耗时的回执都使该项不完整，不冒充完整；模型区间在会话坏行下不标完整。session 文件按持久 header 的 id 校验，不按文件名排序猜权威；多候选同 id 或多轮条目按显式窗口过滤。usage 用统一的 unique 计数与可靠 identity（优先 responseId，时间+usage 仅作启发式并显式标注），无 identity 的消息仍只计一次。summary 只在轮次终态汇总或显式查询路径计算，看板周期刷新只读既有状态/回执/摘要，不做全量会话读取；旧 summary 的新指标保持未知，不原地改写。`pi_board.py metrics` 原有字段保留，新增 `derivedMetrics` 聚合，为每轮列出 known/incomplete/unknown，complete 只在全部轮次都完整时为真。

新增 `scripts/benchmark_runtime.py --baseline-ref REF`：用 `git archive` 从基线只读物化旧 helper 到临时目录，旧新实现分别在独立子进程分析相同合成 10MiB/200MiB 日志，比较 hash/计数/尾部并测 wall 与 peak RSS（区分 macOS/Linux 单位、顺序运行不叠加）。候选 200MiB 峰值相对 10MiB 的增长有界，并须显著低于同输入旧实现；超出则非零退出并保留证据。脚本同时用已知总量的合成计时 fixture 校验指标分解，原始测量只留私有目录，公开只给无业务内容的紧凑结论。benchmark 在独立子进程中顺序执行 helper 与终态 summary 两条同输入路径，峰值按两者最大值计、不叠加，验证汇总回执核验同样有界；同输入 hash/计数/尾部对照与 10/200MiB 峰值增量阈值保留。合成对照与未知边界见 [验证记录](docs/validation/runtime-efficiency-0.7.1-20261003.md)。

## 0.7.0 原生 codemode

新 worker 在显式 worker 扩展内注册 Pi 公共 `createCodemodeExtension({models:false, mode:"on"})`；`--no-extensions`、skills 与 prompt templates 隔离不变，也不重复注册 `builtin:codemode`。`codemode` 加入只读和可写两种任务工具选择，不扩大底层只读能力。脚本只能通过 `tools.<name>` 调用同一守卫下的工具：每次嵌套调用都经过 Pi 的 `tool_call`/`tool_result` 校验和现有路径守卫（禁止路径仍 fail closed，并写入 `worker-blocks.jsonl`）；嵌套 `codemode` 被禁止。脚本首行 `// @options` 按上游语义解析，脚本截止时间以 phase 命令上限为顶并带有界缺省，非法选项在运行前拒绝；取消会传播到嵌套工具，已完成的调用不回滚。`check`/`progress`/`readiness` 增加 `outputSchema`/`structuredContent`：失败检查仍写不可变回执并给出显式 exit/timeout/cancel 语义，调用方必须检查语义失败而不是 Promise 是否 fulfil。`store`/`load` 仍是 Pi 自有会话分支状态，仅成功脚本提交、可原生 resume，不新增持久化层；旧冻结 worker 的证据解释保持。真实 Pi 1.0.0 / QuickJS 探针与脱敏限制见 [验证记录](docs/validation/codemode-0.7.0-20261003.md)。

## 0.6.5 聚焦 Codex-Pi

Sol-Luna 已迁出为独立个人 Skill，使用 Codex 原生子代理，不需要此插件调度。插件只分发 `$collaborate` 和 Pi 运行时；对照工具及原生协作资料随独立 Skill 维护。现有 Pi 任务、hooks 和冻结 helper 行为保留。旧安装中的 Sol-Luna 入口需通过正常插件更新移除，不手改缓存。

## 0.6.4 终态恢复

大型 Pi `agent_end` 聚合记录不再挤掉前面的完整终态消息；旧 round 若只因旧读取器留下 `unknown`，新控制器会在 writer 已释放且 round metadata 与候选 HEAD 匹配时只读重判原始证据。状态文件、原始会话、冻结 helper 和预算保持不变；错误、截断、不完整消息及活动 writer 仍阻止完成或接续。

## 协作方式

1. Codex 给 Pi 一个完整任务段，明确目标、范围、验收和资源上限。
2. Pi 在独立工作树内实施；现有 supervisor 每约 15 秒刷新本地看板。
3. 普通进度和未变化状态不调用主模型，也不入队。待审、阻塞、失败或异常（如持续未修复的检查失败）才通过 `codex queue` 向原桌面任务发送一张不超过 1200 字节的交付卡：任务、轮次、事件 ID、完整候选提交、逐验收项结果、审查额度、Pi 最终报告节选和一条命令提示。CLI 只输出单行紧凑 JSON，`result` 只给紧凑视图。
4. Codex 派工前先做一次可复用的整任务分析，写进现有设计和 brief；Pi 每次完整交付后复用它审查。
5. 新任务固定两次完整 Pi 交付，第二次质量失败后由同一主会话直接接手。计数、外部缺料和接手规则只在 Skill 中陈述。

这里的 Pi 路线中 Codex CLI 只传递消息，不运行第二个模型。没有新增 MCP、heartbeat 或常驻服务。桌面空闲时可以自动续行；桌面正在对话时，消息等当前轮次结束。主任务可以随时回答用户追问。

看板保留任务目标、当前阶段、执行轮次、候选提交、检查回执、待处理事件和主任务决定。高频刷新由脚本完成；主模型只读有事发生的精简事件，必要时再打开对应日志。详见 [协作规则](skills/collaborate/SKILL.md)、[运行命令](skills/collaborate/references/runtime.md) 和 [交接恢复](skills/collaborate/references/handoff.md)。

## 项目配置

```json
{
  "schemaVersion": 1,
  "model": "deepseek/deepseek-flash",
  "thinking": "max",
  "constraints": ["AGENTS.md"],
  "checks": ["该任务的真实验收命令与项目复核要求"],
  "maxWorkers": 1,
  "timeoutSeconds": 14400
}
```

`checks` 是验收指引，插件不会把它当作通过证明。`maxWorkers` 是容量上限；项目的依赖和并行资格仍由项目约束决定。整轮超时和单个命令的时限分别设置。Pi 退出码 0 仅表示执行结束。

`model` 必须取自 `pi_task.py project` 列出的允许模型；选择、按任务冻结和不回退的规则见 Skill。若选用 NewAPI 路由，本机 Pi 需已配置对应 provider 和凭证。

## 故障与费用边界

- 声明了时限、目录和容量上限的检查，由本地脚本执行保护；文件复制保留符号链接。普通测试失败先由 Pi 在范围内修复。
- 中断主任务会暂停自动交接；Pi 是否取消需另行决定。发送结果不明时保留事件，显式恢复，避免重复唤醒。
- supervisor 自身被杀死时不能自行上报；下次正常交互可检查恢复证据。不能承诺任意故障都在两三分钟内发现。
- 每次真正唤醒仍会输入主会话上下文。节省来自取消空轮询和缩小证据读取；不承诺未经对照测量的固定降幅。

## 0.6.0 变化摘要与升级注意

### 0.6.1 / 0.6.2 本地修复

Pi 的最后一条 assistant 若为 `error`、`aborted` 或未完整结束，即使 CLI 退出 0 也不是完成交付。状态、readiness 和通知共用终态判定；执行故障发送 `execution_failed`，不消耗完整质量拒绝计数，也不自动重试或换模型。

新 board 使用插件自己的 SQLite 库（Python 标准库，非 Codex 应用队列数据库）。卡片和通知额度是有界读取的当前投影，事件、准确决策和 queue claim 历史持久保留；不再用一个共享 JSON 的总字节上限限制全部任务。历史占用随真实记录增长，不能承诺无限保留且固定总磁盘大小。`show --cursor`、`events`、`decisions` 提供分页。

已有共享 JSON 用 `pi_board.py recover-store --repo R` 显式转换：先核全部 writer 锁（含未登记任务），保存原 JSON，事务导入后才切换格式；旧控制入口暂停哨兵使其保守拒绝继续。旧任务的接受、pin、计数、session、worktree、原轮次和 helper 都不改。0.6 任务可用 `pi_task.py adopt-runtime --repo R --task T --dry-run` 核恢复，再生成独立的未来轮次 runtime；准备不恢复暂停、不发模型请求、不延长 deadline。0.5 worker 不采用新 runtime。具体步骤和限制见 [恢复验证](docs/validation/recovery-0.6.1-20261002.md)。

- 交付卡不超过 1200 字节；CLI 输出单行紧凑 JSON，`result` 只有紧凑一种形状；普通进度里程碑只留在看板。
- Pi 须为 1.0.0 及以上（`start` 与 `continue` 会拒绝更旧或读不出版本的 Pi）。每轮通过 `-e` 加载冻结的 `pi_worker.ts` 扩展：它在工具调用前拦截主检出与其他 worktree 的路径、越界写入，给 bash 填默认超时并限上限，提供原生工具 `check`、`progress`、`readiness`，把合同作为系统提示注入，并在只缺证据时于同一会话内续跑一次。扩展没有加载的轮次一律失败（`WORKER_EXTENSION_NOT_LOADED`）。brief 只含任务文本和固定短头；失败的检查返回 `log_tail`。
- 删除旧 Stop-hook 投递、`pi_task.py check`、`wait`、`upgrade`、`result --full`，`pi_board.py packet`、`dispatch`，以及 Python 侧的命令守卫、越界判定、supervisor 新进程自动续跑和旧版本固定上限（现统一为两次完整交付）；hooks 只保留 Interrupt 暂停与 cli-queue 恢复。
- 新增 `pi_board.py metrics --repo R [--task T]`：每任务一行紧凑 JSON（轮数、Pi 用量与费用合计、评审决定、是否接手、面向 Codex 的字节数）；未取得的用量或费用保持未知，不以部分和冒充完整。
- **升级注意**：0.6.0 与 0.5.x 任务不兼容，不提供迁移。仍在运行或待收尾的旧任务，用其冻结的 `tools/` 下 helper 收尾并自行审查；旧 Stop 绑定记录由用户自行清理。桌面队列投递和真实 Pi 扩展在安装前需单独做真实验证（见 [交接恢复](skills/collaborate/references/handoff.md) 的 Real desktop validation）。

## 安装与维护

插件由独立 Git 仓库管理，通过仓库自带的 marketplace 分发。Pi 路线依赖 Python 3.10+、Git、Pi CLI 1.0.0 及以上；自动通知另需支持 `queue` 的 Codex CLI 和桌面应用。认证沿用现有本地配置，插件不复制密钥。Pi 运行时适用于 macOS/Linux，Python 部分只用标准库、文件锁和进程组。

其他 Codex 环境可以直接安装公开仓库自带的 marketplace：

```sh
codex plugin marketplace add euglena-inc/codex-pi --ref main
codex plugin add codex-pi@codex-pi
```

在桌面应用中重新加载插件，并按提示审核、信任 Hooks；用新会话加载新版技能。安装只提供协作工具，不会复制本机的 Pi/DeepSeek 凭据或项目配置。已有项目仍需自己的 `.agents/codex-pi.json` 和领域约束。

更新通过正式插件安装流程完成；若应用要求重新审核、信任 Hooks，由用户在应用内完成。不得手改缓存或信任记录。运行中任务保留原 helper 快照，在安全终态后采用新版本。已有任务的迁移需验证原会话和工作树没有改变。

测试：`python3 -m unittest discover -s tests -p 'test_*.py' -v`。脚本测试不等于桌面验收；实际入口验证见 [CLI queue 实测](docs/validation/cli-queue-20260926.md)。社区产品比较见 [调查记录](docs/community-research.md)。

## 开发与隐私

在独立插件仓库中开发；项目约束由使用方维护。发布前运行 `python3 scripts/check_public_privacy.py --history`。公开仓库只保留脱敏的验证说明，原始会话、任务看板、回执和运行日志保存在本地私有目录。详见 [隐私维护](docs/privacy.md)。

### 检查并发（0.7.1）

新worker默认逐个执行`check`。合同可声明`checkExecution`（`maxConcurrent`最多4、`cpuSlots`、`memoryMiB`），并为独立验收命令声明`checkResources`（`parallelSafe`、CPU/内存估算、`exclusiveKeys`）。只有资源声明完整且系统压力可验证的独立检查能并行；共享键、未知检查或未知压力独占。排队消耗原预算，出队后重验deadline和最终候选；取消释放许可。

这是每worker的软准入，不是全机内存隔离，也不控制任意bash/其他应用。真实Pi合成对照：`python3 scripts/validate_codemode.py --concurrency`。CPU密集或容器任务先测1/2路，不按逻辑核数直接开满。

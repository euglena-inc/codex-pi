# Codex + Pi

当前 Codex 主任务负责设计、派工与验收；Pi 的模型由项目配置选择（模型政策只在 [协作 Skill](skills/collaborate/SKILL.md) 中陈述）。项目只需 `.agents/codex-pi.json` 和自己的领域约束，公共插件维护进程、同会话返修、看板和原始证据。

## 0.8.7 按需加载分层（度量改为“一次派工实际载入字节”）

- 体积度量从“每文件字节”改为“一个普通轮次实际载入的字节”：常驻集仍为 `SKILL.md` + `references/runtime.md` + `references/task-packet.md` + `references/handoff.md`，现共 **21358 字节**，在上限 21504 内（对 42692 字节基线 −49.97%）；不常驻内容移到新的 `references/runtime-ops.md`（单独上限 4608），只在指定条件下加载。
- 新增按需文件 [运行时操作](skills/collaborate/references/runtime-ops.md)：任务 store 修复与 pre-0.6 转换/`adopt-runtime`、并行检查的资源准入字段、成果观测收尾与六段限制、网络代理策略与传输故障分类。[协作 Skill](skills/collaborate/SKILL.md) 保留一句常驻指针并写明何时才需要加载；普通派工、检查与评审轮次不会读它。规则正文未删，只是择时加载；机制细节仍以[运行时实现](runtime/IMPLEMENTATION.md)为单一归宿。
- `tests/test_docs.py` 守护升级：常驻集逐文件与总额预算、每个 `references/*.md` 必须明确归属常驻或按需某一层、按需文件必须被 `SKILL.md` 链接且自带“Load when”条件，“拆分”因此不可能被用来静默删除内容；之前的方向子命令覆盖（每个公开子命令必须出现在 `skills/**`）继续适用。本轮负控：从 `SKILL.md` 移除指向 `runtime-ops.md` 的链接后守护失败，恢复后通过。
- 边界：本版本只改文档分层与预算口径，不改运行时算法、验收、计数、所有权或 receipt 语义，不声称模型能力、速度或真实 token 费用收益。

### 0.8.6 要点

#### 模型可见指引体积预算

- 首次将四份模型可见协作文档压入冻结预算且不丢失[规则清单](docs/design/doc-size-budget.md)：机制正文（worker 轮次、网络诊断、检查并发、费用范围）迁入[运行时实现](runtime/IMPLEMENTATION.md)，模型可见文件只保留规则、用法与指针。当时总量 22373 字节（−47.6%），未达最初估计的 −50%：复核发现 5 个公开子命令无任何现行语法、3 条基线规则被删，恢复这些覆盖的代价已在[设计](docs/design/doc-size-budget.md)的 Budget revision 中记录，不靠删规则凑数；本口径已由 0.8.7 的载入集度量接替。
- 新增确定性的 `tests/test_docs.py`：逐文件与总额预算、`skills/`、`docs/`、`runtime/` 下相对链接可解析、`skills/**` 文档里的 `pi_task.py`/`pi_board.py` 子命令与 flag 在真实 `--help` 中存在、**每个公开子命令都必须出现在模型可见文档中**（反方向覆盖，防止“瘦身”误删唯一语法）、机制标记只在实现文档出现，以及 `.agents/codex-pi.json` 的 constraints 指向 `AGENTS.md` 与真实设计文档、不得指向 `skills/collaborate/SKILL.md`。
- 这些改动只影响模型可见指引、项目派工输入与版本元数据，不改变运行时算法、验收、计数、所有权或模型语义；本发布不声称模型能力、速度或费用优势。

## 0.8.5 收尾记录与成果观测

- 新增 `pi_board.py record-outcome --repo REPO --outcome ID --record-file FILE [--expected-revision N]` 与 `pi_board.py metrics --repo REPO --outcome ID`，并提供 `metrics --outcomes [--cursor ID]` 的有界游标列表。`record-outcome` 只写私有 git-common `codex-pi/outcomes/<ID>/` 下有界的不可变编号修订，复用现有锁与原子写；相同规范化载荷重复写入幂等（覆盖不确定重试），更正必须带当前修订号，陈旧冲突不修改任何文件。
- 收尾是带来源的主会话观察，不是验收、接管权限或执行许可。`metrics --outcome` 只读地投影六个部分（交付、时间、返修/事故、验证、用量/费用、干预与覆盖）：成员只按显式记录的同仓库 `(task, round)` 归属，归档决策/事件经 cursor 分页后立即按所选轮次过滤，未选轮次的接受、失败和接管不混入；Pi 原生执行/质量事实与主会话报告分离，首次受审交付保留其当时的决定而非后来的成功接受；用量按存储 summary 的 assistant+auxiliary+total 口径汇总且只计一次，缺失或旧 summary 的完整总额保持未知，按实际单一模型列出 assistant 用量，混合轮次保持未归属，缺少模型身份的辅助用量不分摊；检查回执统一按所属 round.checks 解析后去重、计量和分组，缺失/矛盾/未验证来源只降级完整性而不静默丢弃，失败计数包含已关联的额外主会话检查；未知不填 0，时间间隔按并集而非相加，无边界与明确无该类工作区分；检查重复只标为潜在重复，不推断浪费；接口/SDK 报告费用不是账单，reasoning 已含在 output 中不重复计费。`metrics`、`refresh` 与 `record-outcome` 不修改执行、board 决定、预算、pause、质量计数，不触发通知或模型调用，也不重扫原始日志/会话。
- 记录、查询与列表共享同一可信根和实际字节上限：outcomes/member 元数据每一层都从已解析的 git-common 私有根拒绝 symlink 与逃逸，历史修订在发布前校验规范化信封大小与必要形状，超限明确报错而非留下自家读取器读不回的成功修订。成员只按显式记录的同仓库任务/轮次关联，不按名称或前驱递归猜组；被引用来源之后缺失/损坏时明确标为不完整。公共示例与测试只使用合成身份和路径；真实任务、会话、凭据与业务证据不进入公共仓库。列表只代表已记录的有界集合，不发布成功率或策略节省。设计与边界见[成果观测设计](docs/design/outcome-metrics.md)。

示例（合成值，`candidate` 必须是仓库中真实存在的完整提交）：

```sh
python3 runtime/pi_board.py record-outcome --repo /abs/repo --outcome retro-1 \
  --record-file /abs/outcome.json
python3 runtime/pi_board.py metrics --repo /abs/repo --outcome retro-1
```

```json
{"members":[{"task":"TASK-1","rounds":[1,2]}],"status":"accepted",
 "candidate":"<full-40-or-64-hex-commit>",
 "evidenceRefs":["docs/design/example.md"],"startedAt":1000,"finishedAt":1300,
 "workComplete":false,
 "extraChecks":["tasks/TASK-1/rounds/1/round.checks/regression.json"]}
```

## 0.8.4 必要验证与测试环境隔离

- 冻结前按实际影响选择检查：开发聚焦失败和受影响路径，审查优先核原回执，必要的广泛整合才跑完整回归；新提交、轮次或发布本身不触发全量。检查脚本包含关系，避免重复套跑。
- 复用证据保留原候选、命令和日志并说明当前适用范围；冻结必跑命令失败后仍须补齐原要求。当前运行时继续要求本轮、准确候选回执，不宣称自动跨轮/版本复用。工作包优先省略 `checkId`（默认等于 `id`）以减少身份错配。
- 测试和独立连接验证工具共用一个环境隔离 helper，清除继承的 Node/诊断注入后再应用显式 fixture 配置；保留父环境和无关变量，生产网络策略不变。正常/污染环境与原故障负控通过真实 Node/CLI 验证。
- 普通文档采用必要格式/JSON/引用检查；模型可见指引与启动配置按相关行为验证。本次共享测试 helper 影响多个调用方，最终仍执行一次完整组合回归。设计见[必要验证与隔离](docs/design/necessary-verification.md)。

## 0.8.3 弱模型优先承担完整成果

- 默认偏好由项目配置的较弱 Pi 模型承接最大且可独立验收的完整成果：先界定完整结果，再按“到接受交付的时间与全部可归因成本”选择直接完成或有界设计后派工；token 消耗、调用占比、文件数与置信度不作为目标或路由依据。只读调查同样可作为完整成果。
- Codex 先收敛后果性未知与跨状态语义，给 Pi 准确事实、真实反馈入口和实现自主权，并在完整交付或重大异常时独立复核。普通范围内工作不增加审批门。
- 直接由 Codex 完成或提前接管仍需具体任务理由（例如未决跨状态语义占据剩余工作、缺少可信反馈、或已证实的返修/协调成本超过剩余收益），理由记录在既有设计或复核记录中。质量上限、提前接管资格、不可变证据与预算语义不变。
- 本发布只调整协作指引与版本元数据，不改变运行时算法、允许模型或默认值。设计与边界见[弱模型优先设计](docs/design/weak-model-first.md)；生命周期、接管与费用范围沿用 [0.8.2 验证记录](docs/validation/bounded-allocation-0.8.2.md)。文档交付只能证明派工流程可行，不证明通用编码能力或速度/费用优势。

## 0.8.2 有界分工、提前接管与成本范围

- Codex 依据实际设计不确定性、反馈和完整交付成本，选择直接完成或有界设计后由 Pi 完成交付；只读调查也可作为完整成果。工作包引用准确事实与检查入口，允许按需读源码。
- 新任务冻结提前接管资格。主会话可用 `pi_board.py takeover` 绑定当前事件、完整候选与理由，核实 writer、监督器和记录的进程组释放后接管；两次质量失败仍是硬上限。接管原因与真实失败次数分开，失败、预算、dirty 和原证保留，旧任务不增加资格。
- 指标显式覆盖 Pi 执行；provider 报告费用与真实账单分开，零报告值不证明免费。Codex 输出字节不冒充 token；缺少外部设计、复核和接管计量时，完整交付成本明确未知。验证范围见[验证记录](docs/validation/bounded-allocation-0.8.2.md)。

## 0.8.1 Qwen 3.8

- 允许项目显式选择 `qwen38/qwen38`。将 `thinking` 设为 `max` 使用 Pi 的最高思考档；默认模型仍为 `deepseek/deepseek-flash`。模型和思考档按任务冻结，不可用时不自动切换。

## 0.8.0 指令清晰度与连接诊断

- worker 合同、工具说明和派工模板明确区分 Promise 完成与检查通过、执行失败与准入拒绝，以及定向检查与正式验收；保持原有权限、预算与验收语义。
- 新增允许模型 `newapi/deepseek-flash`。模型由项目配置选择并按任务冻结，不自动换接口或回退。使用 NewAPI 时，应确认本机 Pi 配置能传递 `thinking: enabled` 和 `reasoning_effort: max`；插件不复制凭据，也不保证网关与官方转发完全等价。
- 可选 `network.proxyUrl` 和 `network.diagnostics` 为新任务冻结显式代理策略与有界、脱敏的连接观察记录。诊断仅观察 supervisor 直接启动的 Pi 主进程，区分连接重置、代理 CONNECT 错误与正常流清理；不自动重试、不改变系统代理，也不承诺修复代理故障。旧任务继续使用原冻结策略和工具。
- 新增可复核的真实 Pi 指令对照工具。实验运行时固定到原始提交，插件升版不改变旧证据的只读复核。36 会话观察到旧版完成 15/18、新版 17/18，但场景带明确提示且评分经过事后校正；不宣称通用能力、速度或费用收益。详见[实验报告](docs/validation/instruction-clarity-pilot-20261003.md)及[连接支持验证](docs/validation/connection-support-20261003.md)。

升级通过正式插件安装流程完成。新版用于新会话和新任务；正在运行或保留的旧任务不自动改写模型、期限、会话和 helper 快照。桌面 Hook 信任仍由应用管理，Python 测试不能替代桌面验证。

## 0.7.1 检查预算、流式日志与指标

`check` 在创建子进程前重读本轮真实 `deadlineAt`，固定预留 60 秒收尾。预计耗时与 timeout 分离：调用与合同的有限正数估计取较大值，均无估计时以有效的请求/合同上限作保守需求，不把阶段剩余缩小当作预计耗时。先算最终有效执行窗口（请求/合同上限、remaining−60、codemode 外层−清理余量），需求超过该窗口时返回结构化 `ok:false`（含 reason、requiredSeconds、allowedSeconds、remainingSeconds、reserveSeconds、`receipt:null`），不 spawn、不写回执、不产生零测试假回执；期限读取/身份校验失败同样拒绝。实际传入 helper 的 timeout 就是该窗口本身，保留小数、不向下取整。codemode 脚本内的嵌套检查同时计入该脚本自身的外层期限；扩展无法验证外层期限时拒绝并提示直接调用 `check`，已准入的检查不会被外层期限提前杀死。真实执行的返回带实际 `elapsedSeconds`、`allowedSeconds` 与采用的 `estimateSource`，估计不被写成实际耗时。拒绝提示只能在授权上限内调整 timeout 以覆盖可信估计，或在有证据时修正估计，不鼓励编低估计或扩预算。

合同验收项可选 `targetedCommand`（仅局部调试建议，永不替代正式 command）与 `estimatedSeconds`；同一规范化 argv 的元数据矛盾在合同验证时拒绝。与带 `targetedCommand` 的全量命令 argv 相同的 `check` 默认拒绝并返回该建议，只有显式 `final:true` 且工作树干净才运行；匹配按 argv 而非 id，定向检查不能覆盖正式验收。不自动缓存 PASS、不制造复用回执、不限制全量只跑一次：合同已声明为必跑的正式命令，在最终候选上仍须有真实通过证据；这一规则不要求每个合同都选择全仓回归。没有新服务、状态库或通用调度器；旧冻结 worker 与缺少可选字段的旧合同保持原行为。

检查日志改为单次流式分析：固定字节块同时产出完整 SHA-256、保守的 Go/unittest 计数和有限 `log_tail`，不为尾部再解码整个文件。多字节跨块、重复/矛盾摘要、空/缺失与非法 UTF-8 保持原有保守语义；超过硬上限的单行保持 unknown，不丢前缀后误匹配。只有日志确实不存在才按历史上的空日志兼容处理；已有文件权限失败或读取中途 I/O 失败显式失败，不生成看似正常的完整 hash 或成功回执。原始日志仍完整落盘，回执的 exit/timeout/cancel/resource、head/dirty 和 hash/原子发布语义不变。summary 的终态回执核验也用流式 hash，不再把大检查日志整读进内存。

`round.summary.json` 新增 `metrics` 紧凑分解：未缓存输入/缓存读取/输出/缓存比例、请求上下文首末峰值、真实 check 次数与已完成耗时（中断/未知不计入）、模型响应区间、工具区间并集及未归因余量，并标注 exact/estimated/unknown。usage 只累计唯一 assistant 最终消息，reasoning 是 output 子项不重复相加，重复投递的 `agent_end`/`turn_end` 不累计。时间指标由调用方显式传入 session 目录/id 与本轮时间窗口，从原生持久 session 的写入时间和消息内部时间派生；没有可靠来源返回 null 和 reason，不输出假 0。每个派生指标区分 known/coverage/complete：已知部分可保留，但缺失、坏行、未闭合工具、无法匹配的 toolResult 或缺少耗时的回执都使该项不完整，不冒充完整；模型区间在会话坏行下不标完整。session 文件按持久 header 的 id 校验，不按文件名排序猜权威；多候选同 id 或多轮条目按显式窗口过滤。usage 用统一的 unique 计数与可靠 identity（优先 responseId，时间+usage 仅作启发式并显式标注），无 identity 的消息仍只计一次。summary 只在轮次终态汇总或显式查询路径计算，看板周期刷新只读既有状态/回执/摘要，不做全量会话读取；旧 summary 的新指标保持未知，不原地改写。`pi_board.py metrics` 原有字段保留，新增 `derivedMetrics` 聚合，为每轮列出 known/incomplete/unknown，complete 只在全部轮次都完整时为真。

辅助用量闭环：`metrics.auxiliary` 仅从显式 session 中严格本轮窗口（不借用时间指标的 ±1s 容差）内的 `compaction`/`branch_summary`/`usage` 条目取额外费用，按明确 entry id 去重，逐项给出种类/次数、已知 token 与 reported cost 及 known/incomplete/unknown；token 只接受有限非负整数、cost 只接受有限非负数值，无效字段不参与已知和并标记为 invalid，JSON 不输出 NaN/Infinity；字节/规范化内容一致的同 id 重放可去重，内容不同的同 id 冲突显式记 incomplete 且不按先后选赢家；缺 usage 的条目不按 0；round JSONL 缺失或坏行使信号来源不可核时保持 unknown/partial，原始记录有压缩失败/未完成而没有成功条目时不冒充确定 0，缺 session 来源同样保持未知，只有可靠完整 round + 可靠且无信号的干净 session 才报已知零。旧 assistant `usage`/`usage_complete`/`reported_cost_usd` 语义不变、不重复计费，`metrics.total` 只在两侧都完整且值有效时给出合计；看板只聚合已存 summary，非有限存储值不进入已知和，不重扫 session。

新增互斥的 `python3 scripts/validate_codemode.py --context-workflow` 真实 Pi 合成探针：缺输入的前提检查失败产生不可变失败回执且不启动依赖命令，修正合成输入后同一检查通过且依赖命令真实执行（脚本检查结构化 `ok`，不靠 Promise 是否 fulfil）；固定相同合成文件比较直接多轮 `read` 与一次 codemode 批量读取/筛选，记录真实 scripted-provider 请求数、本 case 工具结果新增的上下文体积（最后一次请求减本 case 首请求的工具结果字节，不是累计传输量）、墙钟和相同结果；空闲会话走真实 `session.compact`，核对成功条目与 usage、追加式前缀、session id/模型 pin、codemode store、重新注入的合同、候选与失败回执不变，并用失败/取消/缺模型负控和活动态负控确认不制造成功条目、不触发 compact/abort，也不暴露生产 compact 工具。活动态观察只证明本插件未新增主动 compact，不能推广成 Pi 原生自动压缩永远不会在 run 内发生（原生仍可在工具完成后的 turn 间按默认阈值触发）；脚本化 provider 的固定 usage 与合成 token 估计只用于机制观察，不是真实 token/费用节省证据。脱敏结果与未知边界见 [验证记录](docs/validation/context-workflow-0.7.1-20261003.md)。

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
5. 新任务最多两次完整 Pi 交付，可由主会话有依据提前接管；第二次质量失败后由同一主会话接手。计数、外部缺料和接手规则只在 Skill 中陈述。

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

`model` 必须取自 `pi_task.py project` 列出的允许模型（当前含 `deepseek/deepseek-flash`、`newapi/glm-5.3`、`newapi/deepseek-flash`、`qwen38/qwen38`）；选择、按任务冻结和不回退的规则见 Skill。Pi 当前将 `qwen38/qwen38` 列为支持 thinking 的模型；将它设为项目模型时，使用最高思考档 `"thinking": "max"`，例如 `{"model":"qwen38/qwen38","thinking":"max"}`。模型由项目显式选择，默认仍为 `deepseek/deepseek-flash`；本机 Pi 仍需配置相应 provider 和凭证。

### 网络代理与传输诊断（连接支持）

`.agents/codex-pi.json` 可选 `network`，只允许两个键：

```json
{
  "schemaVersion": 1,
  "model": "newapi/deepseek-flash",
  "thinking": "max",
  "constraints": ["AGENTS.md"],
  "checks": ["该任务的真实验收命令与项目复核要求"],
  "maxWorkers": 1,
  "timeoutSeconds": 14400,
  "network": {"proxyUrl": "http://proxy.invalid:3128", "diagnostics": true}
}
```

`proxyUrl` 只接受无凭据的 `http`/`https` 代理地址：必须显式主机与端口、根路径、无查询/片段；校验在任何任务副作用前完成，拒绝时不回显原值（示例地址为合成地址）。新任务在 `task.json` 冻结策略，`continue` 只使用冻结策略，后续项目配置修改只影响新任务；没有 `network` 键的旧任务保持原有继承行为。显式路由只应用到 Pi 子进程（覆盖大小写与 `ALL_PROXY` 等冲突形式），子工具进程继承同一策略，`NO_PROXY`/`no_proxy` 排除规则保留；supervisor 与 Codex 队列传输不被改道。未配置显式地址时继续使用环境里已有的代理变量：插件不做自动发现、不自动重试、不切换模型或路由。

`diagnostics: true` 时，运行时把候选的 `pi_network_diagnostics.mjs` 以进程级 `NODE_OPTIONS` 预加载（保留原有选项、路径按 URL 编码）并设置进程级 sidecar 变量。观察器只用 `node:diagnostics_channel` 订阅 undici 请求事件：不替换 fetch/dispatcher、不改请求、不把普通流清理 `AbortError` 记为连接失败，写入失败也不影响模型调用。只有 supervisor 直接派生的 Pi 主进程会写 sidecar（继承的工具子进程只继承代理策略、不写诊断），同一轮真正只有一个 writer，总量硬上限 64 KiB；错误分类按有界 cause 链优先采信明确的代理/网络证据，缺失或超长消息不能指向代理，`AbortError` 名称才是正常清理，`UND_ERR_ABORTED`/`DESTROYED`/`CLOSED` 不能单独证明清理。记录写入 `rounds/N/round.network.jsonl`，字段有界且只含相对时间/耗时、阶段、安全分类、白名单错误码、CONNECT 数字状态与进程标识；不写 URL、host、headers、body、原始错误文本、凭据或会话内容，超限时写截断标记而不冒充完整覆盖。读取端只读有界前缀并校验 phase/class/code/status 的类型与长度，缺失/超限/非法 UTF-8/截断/伪造记录一律按 unreadable 处理且不回显伪造值。`status`/`result` 的 `network` 块只给紧凑的代理策略/来源与诊断文件引用和计数；诊断文件缺失或不可信是 unknown，不是健康。分类只是观察，不是验收结论。

恢复建议：TLS 前 ECONNRESET/重置更指向传输；CONNECT 503 表示代理隧道失败。应校验或显式切换用户自己配置的代理路由，不承诺永久修复；路由失败时保留会话，恢复后显式 `continue`。原生 Pi 重试配置与本插件无关，不被修改。机制验证与真实边界见 [连接支持验证](docs/validation/connection-support-20261003.md)。

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

在桌面应用中重新加载插件，并按提示审核、信任 Hooks；用新会话加载新版技能。安装只提供协作工具，不会复制本机的 Pi、模型 provider 凭据或项目配置。已有项目仍需自己的 `.agents/codex-pi.json` 和领域约束。

更新通过正式插件安装流程完成；若应用要求重新审核、信任 Hooks，由用户在应用内完成。不得手改缓存或信任记录。运行中任务保留原 helper 快照，在安全终态后采用新版本。已有任务的迁移需验证原会话和工作树没有改变。

测试：`python3 -m unittest discover -s tests -p 'test_*.py' -v`。脚本测试不等于桌面验收；实际入口验证见 [CLI queue 实测](docs/validation/cli-queue-20260926.md)。社区产品比较见 [调查记录](docs/community-research.md)。

## 开发与隐私

在独立插件仓库中开发；项目约束由使用方维护。发布前运行 `python3 scripts/check_public_privacy.py --history`。公开仓库只保留脱敏的验证说明，原始会话、任务看板、回执和运行日志保存在本地私有目录。详见 [隐私维护](docs/privacy.md)。

### 检查并发（0.7.1）

新worker默认逐个执行`check`。合同可声明`checkExecution`（`maxConcurrent`最多4、`cpuSlots`、`memoryMiB`），并为独立验收命令声明`checkResources`（`parallelSafe`、CPU/内存估算、`exclusiveKeys`）。只有资源声明完整且系统压力可验证的独立检查能并行；共享键、未知检查或未知压力独占。排队消耗原预算，出队后重验deadline和最终候选；取消释放许可。

这是每worker的软准入，不是全机内存隔离，也不控制任意bash/其他应用。真实Pi合成对照：`python3 scripts/validate_codemode.py --concurrency`。CPU密集或容器任务先测1/2路，不按逻辑核数直接开满。

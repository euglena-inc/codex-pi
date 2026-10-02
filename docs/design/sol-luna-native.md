# 原生 Sol-Luna 协作方案

日期：2026-10-02。状态：0.6.3 源码实现；原生派工与同一子代理继续在合成
项目验证，详见 [验证记录](../validation/sol-luna-native-20261002.md)。尚未安装
发布，也没有完整 Sol-token 对照。Codex-Pi 保留原机制和任务合同。

## 推荐选择

Sol-Luna 采用 Codex 原生子代理。插件新增 `skills/sol-luna`，与
`skills/collaborate` 并列；只维护职责、完整交付、返修和比较口径。
Codex 负责创建子代理、工具执行、等待和同一子代理继续。无需新建 Python
supervisor、消息队列、MCP、heartbeat、独立模型 CLI 或自定义 agent 调度器。

优先显式指定模型，而不是安装时改用户的全局配置。需要可复用角色的消费项目
可以自行配置 `.codex/agents/*.toml`。官方记录了项目与个人 custom agent 路径，
但插件打包文档没有确认本插件根目录的 agent TOML 会自动注册；第一版不依赖它。
[`Subagents`](https://learn.chatgpt.com/docs/agent-configuration/subagents)，
[`Package your plugin`](https://developers.openai.com/plugins/build/plugins)。

## 官方依据与本机证据

- 原生子代理支持分工、不同模型/推理设置和后续调度；默认模型可能继承父代理，
  因而必须验证实际选用 Luna。子代理会带来额外模型工作，不天然减少总 token。
  [`Subagents`](https://learn.chatgpt.com/docs/agent-configuration/subagents)。
- 支持子代理默认模型和推理配置；显式参数、默认配置、自定义角色的优先级不能
  只凭提示词猜测。当前方案用明确的原生工具参数，核实际元数据。
  [`Config reference`](https://learn.chatgpt.com/docs/config-file/config-reference)。
- 模型选择和推理等级受客户端、账户与工作区影响；官方建议 Luna 从 high 起步。
  本次用户固定选择 **Sol 6.1 medium＋Luna max**，替代此前 Luna high 起步的
  提案。不自动升级、降级、换模型或启用 Ultra；固定参数不是性能保证。
  [`Models`](https://learn.chatgpt.com/docs/models)。
- app-server 有 token usage 事件，也能生成本机版本的协议 schema。
  [`App-server`](https://learn.chatgpt.com/docs/app-server)。
- 本机 CLI 为 0.157.1；本轮只读 `model/list` 返回了 `gpt-6-luna` 和
  low/medium/high/xhigh/max，没有返回 `gpt-6.1-sol`。当前桌面会话的工具声明
  支持两者并支持 `fork_turns="none"`、显式模型与同一 agent 的 follow-up。
  声明与目录读取不是实际模型请求成功的证明；不自动升级 CLI 或修改配置。
- 本机生成的 schema 有 `thread/tokenUsage/updated` 的 last/total 字段及
  input、cached input、output、reasoning output。当前会话的 collaboration
  工具没有直接暴露逐 agent token 统计。协议存在不等于采集已经实现。

## 社区反馈及其解释

更完整的资料分级见 [2026-10-02 调查](../community-research.md#native-sol-luna--2026-10-02)。

社区已有 Sol 6.1 Medium 搭配 Luna Max 实施的使用报告，强调任务设计完整。
这证明有人尝试了该分工，不能证明本项目使用同样设置能节省相同比例。
[`Reddit：Sol 6.1 + Luna`](https://www.reddit.com/r/codex/comments/1wuo421/psa_sol_61_slowness_might_get_fixed_in_next_few/)。

官方仓库 issue 有长任务大量递归子代理、继承历史、重复审查与测试的失控报告。
这些是用户报告，尚未在本项目复现；启示是限制拓扑、减少重复工作，而不是
把报告的录制 token 数当作账单或当前客户端必然行为。
[`#38989`](https://github.com/openai/codex/issues/38989)。

继承历史可能损失 prompt-cache lineage 的 issue 针对更早 CLI 和 GPT-5.5；
本次读取时仍 open，不能据此断言 GPT-6.1 也存在相同故障。选择 fresh context
本身能避免复制无关长历史，缓存收益另测。
[`#24704`](https://github.com/openai/codex/issues/24704)。

过去还有模型选择字段被隐藏、子代理继承 Sol 的报告，相关 issue 已 closed。
不照搬旧配置 workaround；在目标客户端核当前工具 schema 和实际模型。
[`#31814`](https://github.com/openai/codex/issues/31814)。

另一组社区讨论认为“额度下降慢”也可能来自生成速度慢，而非工作更省。
按完成任务统计 token、质量和耗时，不能只比较一小时后的百分比。
[`Reddit：6.1 usage`](https://www.reddit.com/r/codex/comments/1wuclfk/61_sol_usage/)。

## 具体机制

1. 用户选择路线。`collaborate` 使用现有 Pi；`sol-luna` 使用原生 Luna。
   活跃任务不自动换路线，模型缺失不静默回退。
2. Sol 从必要源码确定完整目标、关键设计、验收和预算。一份可复用说明交给
   一个 fresh-context Luna。搜索、编辑、测试、普通修复由 Luna 自主完成。
3. 默认单个写入者；只有实质独立结果才增加并行，且分别隔离工作树和资源。
   子代理默认共享检出，工作树指令不等于沙箱。Sol 等写入者释放后再整合。
4. 使用原生等待，完整交付或真实阻塞才需要主模型判断。插件不轮询模型、
   不强制子代理发里程碑、不读取完整子会话。宿主仍可能产生原生通知和主模型
   唤醒；没有承诺同 Pi supervisor 一样的事件抑制与跨重启恢复。
5. Sol 只读决策所需 diff、证据和关键源码，但覆盖真实入口与受影响边界。
   简短交付卡是索引，不代替证据。第一轮质量拒绝后给同一 Luna 完整修正方案。
   两次完整质量交付失败后 Sol 接手；内部红测、外部阻塞和执行故障另记。
6. 两次计数、预算、候选与写入者由主会话现有任务记录维护，是 Skill 约定。
   没有复制 Pi 的持久化准入/接手锁或硬 token 上限，不声称具备这些保证。

省 Sol token 的机制是减少 Sol 的搜索、编辑、失败测试与微型派工轮次，
同时把中间输出留在 Luna 上下文。进一步省总费用要抵消 Luna 输入、返修、
主模型验收和接手成本，不能从模型单价直接推出。

## 双路线对照

| 维度 | Codex-Pi | 原生 Sol-Luna |
| --- | --- | --- |
| 执行者 | 项目冻结的 Pi 模型 | gpt-6-luna 原生子代理 |
| 调度 | 现有 Python supervisor 与 CLI queue | 宿主原生子代理工具 |
| 隔离 | 独立 worktree 与 Pi 扩展守卫 | 默认共享检出，按需安排 worktree |
| 普通返修 | 原 Pi 会话 | 原 Luna 子代理 |
| 质量接手 | 现有持久化计数与锁 | 主会话记录的约定 |
| 执行恢复 | 已有冻结 helper 和恢复流程 | 目标客户端原生恢复能力，待实测 |
| Sol 用量 | 不能由 facing bytes 换算 | 不能由摘要长度或账户额度换算 |

两个路线可以并存，也可以在同一目标的独立工作树做对照。公平测量最好使用
同基线、同验收、同 Sol 设置且可分别归属的主会话或轮次；同一 Sol 轮次若
同时处理两路，不能精确拆分其 token。创建额外用户聊天只在用户明确请求时做。
比较包括设计、验收、返修、接手和最终质量，保留失败样本。详细口径见
[comparison](../../skills/sol-luna/references/comparison.md)。

## 落地范围与验证

新增独立 Skill、跨项目使用说明与离线 `scripts/compare_routes.py`，插件版本
0.6.3。只更新 Pi 运行时版本元数据，Pi 调度、hooks、守卫和冻结任务行为保留；
没有修改缓存、信任或用户配置。比较工具按项目/任务/基线/验收分组，要求完整
接受结果、模型核验和 exclusive usage 才输出比例；不是自动费用采集器。

本轮用原生工具显式传入 Luna/max/fresh context，两个独立合成项目完成交付，
其中一个通过同一 agent 后续添加回归、保留失败证据、修复并交付；主会话核
精确提交和检查。实际 served model/effort 没有工具证据，标为 unknown；本轮
不是已核验的 Sol Medium/Luna Max 费用对照。指定参数、原生执行和实际模型
证明是三种不同证据，不能互相替代。

未验证的范围：中断后子进程释放、客户端重启后的同一 agent 恢复、正式安装
后的入口加载，以及费用计量归属。Skill 明确写入这些限制，不承诺持久守卫或
硬 token 上限。后续真实比较仍需核 token 事件的归属、缓存与历史重复问题。

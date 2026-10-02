# Codex + Pi / Sol + Luna

当前 Codex 主任务负责设计、派工与验收；Pi 的模型由项目配置选择（模型政策只在 [协作 Skill](skills/collaborate/SKILL.md) 中陈述）。项目只需 `.agents/codex-pi.json` 和自己的领域约束，公共插件维护进程、同会话返修、看板和原始证据。

插件提供两条独立路线：`$collaborate` 保留 Codex-Pi；[`$sol-luna`](skills/sol-luna/SKILL.md) 使用 **Sol 6.1 Medium＋原生 Luna Max**。同一插件可跨项目复用；项目只保留自己的领域约束，Sol-Luna 不需要 Pi 配置、额外服务或复制 agent 角色。新项目用新的子代理，同一结果返修用原子代理。实际父模型须在客户端选择，Skill 不强制修改模型设置。

Sol-Luna 的 [项目用法](skills/sol-luna/references/projects.md)、[设计与证据边界](docs/design/sol-luna-native.md) 和 [对照口径](skills/sol-luna/references/comparison.md) 分别按需读取。原生子代理默认共享检出，不能视作 Pi 的工作树守卫；计数和预算由主会话维护。没有承诺固定 token 节省比例或任意重启恢复。活跃 Pi 任务不迁移。

## 0.6.3 双路线

新增原生 `sol-luna` Skill 和离线 `scripts/compare_routes.py`。对照按项目、基线和验收分组；缺少真实用量、模型核验或完整接受结果时不输出节省比例。现有 Pi 调度、hooks、守卫和冻结任务策略保留。源码交付与正式安装、真实费用对照分别验证。

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

插件由独立 Git 仓库管理，通过仓库自带的 marketplace 分发。Pi 路线依赖 Python 3.10+、Git、Pi CLI 1.0.0 及以上；自动通知另需支持 `queue` 的 Codex CLI 和桌面应用。Sol-Luna 路线依赖具备显式模型选择、fresh context 和同一子代理 continuation 的原生 Codex 客户端；离线比较工具使用 Python 3.10+。认证沿用现有本地配置，插件不复制密钥。Pi 运行时适用于 macOS/Linux，Python 部分只用标准库、文件锁和进程组。

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

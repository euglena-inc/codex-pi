# 检查预算与局部修复

## 目标和阶段

基线 3b3ce45fe1f2424b5098515cb7eed7e695c89392。只优化两个已观察问题：剩余时间明显不够仍启动长检查；局部修复反复触发昂贵全量检查。保持当前单 Pi、原生 codemode、冻结 helper、不可变回执和主会话接受机制。不得修改消费项目、正在运行的其他 worker、缓存、凭据、hook 信任或发布。

本任务两个可独立审查的阶段使用同一 task/session/worktree：A 实现机制并通过定向检查；主会话审查 A 后以现有 adopt-runtime 准备下一轮；B 真正使用新 check 机制完成拒绝负控、局部检查和最终全量验收。A 完成不等于整任务完成；B 不得只运行 mock 冒充机制实际使用。总工作控制在原项目默认四小时内，阶段 A 两小时，B 在剩余预算内至多半小时；额外返修不能重置历史或无授权扩额。

## 已核事实和最小设计

当前 check 只按 commandTimeoutSeconds 限制单命令，supervisor 的实际轮次 deadlineAt 已在启动 Pi 前写入 round.state.json。复用该权威截止时间，不新增时钟/预算状态库。当前 phase acceptanceItems 已有 command/checkId，可在原字段上作可选扩展。局部修复和最终检查仍使用现有 check 工具和回执，不另造调度器或测试框架。

### 时间准入

- 在现有 check 路径内、创建子进程之前读取当前轮次真实 deadlineAt，重新计算 remainingSeconds，固定预留 60 秒收尾。读取/身份/有限数校验失败时，新配置启用的机制拒绝启动，不把 unknown 当无限预算；保留旧冻结运行时行为。
- 工具新增可选 estimatedSeconds；合同验收项也可给同名字段作为主会话预算依据。预计耗时与实际 timeout 分开：合同估计与调用估计都存在时取较大值；均无估计时以有效 timeout 作为保守启动所需时间。估计必须有限正数。明确说明估计不是完成保证，不增加历史扫描器或预测模型。
- 只有 available=remainingSeconds-60 大于等于所需时间时才启动。实际子命令 timeout 还要取请求上限、合同命令上限、available 的最小值。codemode 外层截止时间也必须计入有效上限，不能外层600秒先杀死已准入的长 check；若原生调用上下文无法传播这一上限，就明确禁止在 codemode 中运行超过其可验证包络的检查并提示直接 check，不能声称已覆盖。
- 不足时返回结构化 ok:false、明确 reason、remainingSeconds、requiredSeconds、reserveSeconds，receipt:null 且不 spawn，不生成成功/跳过/零测试的假回执。结果及原始工具事件足够作为拒绝证据，不建新的审计库。到期保护仍由原 supervisor 兜底。
- 在成功/失败实际执行返回中提供真实 elapsedSeconds 及采用的预计时间来源，供后续计划修正估计；不把估计写成实际耗时。

### 局部优先、最终全量

- 合同验收项新增可选 targetedCommand（一个真实局部检查命令）。它只是调试建议，永远不替代该项 command 的正式验收。
- 当 check 的规范化 argv 匹配带 targetedCommand 的全量验收命令时，默认拒绝并返回 targetedCommand；调用者必须显式 final:true 且工作树干净才能运行该全量检查。按 argv 匹配而非 id 防止改 id 意外绕开；同一规范化命令出现矛盾 metadata 时合同验证拒绝。final 不是新接受权限，也不是禁止失败后重跑。
- 不自动缓存 PASS、不制造复用回执、不强制全量只能跑一次。定向检查通过、固定最终提交后完整全量仍须真实执行；若最终检查失败，先修受影响项，再对新候选重跑必要完整验收。普通没有 targetedCommand 的旧合同不受“全量”分类限制。
- 在现有 brief/skill/runtime 文档用短说明要求先做廉价前提检查、定向修复、固定候选、预留全量与收尾时间。不要从用户命令字符串猜依赖图或自动并行共享资源测试。

## 实施和验证

Pi 负责实现、普通自修、必要测试和局部提交，版本更新为 0.7.1。优先复用当前函数；仅在明确共享逻辑需要时增加小模块，并纳入 HELPER_FILES。无需另增配置文件、服务、数据库、通用runner或重复解析层。范围：runtime、tests、scripts（仅已有探针必要适配）、skills/collaborate、README、docs、插件版本文件。

定向测试应覆盖真实公开路径：足够预算的子进程实际运行；预算不足无子进程/无回执/marker未产生；读取未知预算失败关闭；命令上限与剩余时间共同限制；codemode嵌套限制；不同id同argv的全量仍需final；dirty最终检查拒绝；局部检查不能覆盖正式验收；旧合同/新合同、冻结快照和采用后的同session保持。用合成目录/身份、独立文件副作用作反证，不能只检查提示文本或镜像算法。

A 正式命令：`python3 -m unittest discover -s tests -p 'test_check_budget.py'`（新行为实际用例），`python3 -m unittest discover -s tests -p 'test_worker_extension.py'`（扩展回归），`python3 scripts/check_public_privacy.py`。开发阶段只跑受影响项，不先重复全量。所有正式回执在最终代码/文档提交后取得。

B 在同task采用新helper后：真实 check 请求一个大于本轮剩余预算的估计，验证明确拒绝且指定子命令副作用不存在；对全量命令省略final验证拒绝并给局部命令；实际执行局部命令，再final:true运行完整Python套件、真实Pi/QuickJS探针和隐私检查。主会话对账工具事件、回执、候选、实际执行次数。拒绝负控有意触发一次即可，不耗尽预算或真的睡到截止。

最终记录目标检查/全量检查实际次数、耗时、两类拒绝的无副作用证据和同session采用事实。没有等价A/B实验就不声称百分比节省。保留失败和旧回执，原始日志和分析只存私有路径；公开文档仅脱敏事实。必须运行隐私检查后提交，不推送或发布。

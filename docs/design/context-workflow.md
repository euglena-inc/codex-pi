# 前提检查、批处理与原生压缩的安全验证

## 依赖和范围

仅在有界并发成果被主会话接受后派发路线D。保留Python+TypeScript、单Pi、现有session与原始证据。复用现有真实Pi SDK scripted-provider探针，不新增调度服务、模型worker、摘要数据库或生产自动压缩器。本阶段完善协作指引、对照验证及必要的附加模型用量统计；不修改业务项目、全局Pi设置、provider凭据或发布。

## 已核接口与决定

Pi 1.0.0公开AgentSession.compact(customInstructions)会先await abort，再生成摘要、appendCompaction和刷新projection。因此不能在活动工具/检查中调用它来“节省token”。本阶段只在探针的明确空闲会话验证native compact，不把compact暴露成活动worker可调用的工具，不提前降低原生自动压缩阈值。SessionManager的compaction条目保存usage，可与assistant用量分开核对；原始消息继续留在session文件。

### 廉价前提与批处理

在现有task packet/brief短说明中要求先执行本任务实际存在的廉价前提命令，失败停止依赖的昂贵检查；preconditions文本不能冒充已执行。不得设计通用preflight框架或访问真实封存业务输入。

真实SDK合成反例：输入缺失的check失败，有不可变失败回执，依赖子命令marker不存在；修正合成输入后原检查通过，依赖命令实际执行。采用现有check结构化ok，不靠Promise fulfilled判断。

固定相同合成文件和输出目标，比较直接read多轮与codemode一次批量读取/筛选。记录实际scripted-provider请求次数、进入后续请求的序列化工具结果字节、墙钟和相同结果；脚本化provider的固定usage不是实际节省token的证据。每个文件都真实读取，失败可见，不能用预先写死的摘要代替工具工作。原生model/credential策略不变。

### 原生压缩安全

复用真实SDK创建有足够历史的合成会话，摘要调用仍经过真实Pi compact实现和本地scripted-provider；只有空闲时运行，活动状态负控不得执行compact/abort或改变原任务。压缩前后及重新打开session后独立核对：session id、旧文件字节前缀/原始失败记录、codemode store、重新注入的合同、候选及失败回执不被摘要变成通过，模型pin不变。缺模型/摘要失败/取消不制造成功条目。摘要正文是否能忠实处理真实业务仍是未验证限制，不因合成摘要正确就自动启用生产压缩。

记录native projected context估计前后、summary调用次数、耗时与报告usage。没有等价真实provider对照不声称费用百分比节省；明确一次压缩本身要花费用并可能重建缓存前缀。保留原生默认自动压缩策略，不改用户配置。

### 用量统计闭环

当前summary只计assistant最终消息，会漏session中compaction/branch_summary/usage条目的额外费用。复用现有显式session来源和一次有界解析，对本轮严格时间窗口内的辅助usage按明确entry id去重，独立报告种类/次数/已知token与reported cost/完整性。不要把时间窗口的±1秒计时容差用于跨轮费用归属，不重复计入assistant。缺usage的辅助条目不能当零；缺session来源时也不能声称辅助费用为零。

保持旧assistant统计字段含义，新增明确的辅助统计和总体完整性说明；board只聚合已存summary，不在刷新时重扫session。原始session失败、替换、歧义和超长记录保守unknown。默认无辅助条目的可靠完整session可报告已知零，可靠性与“找不到来源”区别清楚。独立合成记录校准，真实compact生成条目再交叉核验。

## 验收与完成

扩展既有验证入口增加互斥的--context-workflow模式，复用provider/session基础设施；新增必要定向测试。先定向与真实模式探针，最终候选固定后全量回归、原有native/concurrency探针和隐私检查。资源策略默认独占，长检查预留实测时间；所有正式回执绑定候选。原始观察私有，公开只写脱敏结果及未知边界。

交付三项可复现实验、附加usage不漏计的指标、短协作指导和生产决策：已验证空闲native压缩机制，但不自动提前触发或声称真实任务质量/费用收益。主会话审查后统一更新六项完成状态，不以规划文档冒充功能实现。

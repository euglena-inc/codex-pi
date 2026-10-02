# 0.7.0 原生 codemode 验证（2026-10-03）

> 本文是脱敏的本地验证摘要。公开仓库不提供原始会话、回执或脚本输出；历史结论不能替代当前候选的复跑与主会话桌面验收。

候选实现位于 `runtime/pi_worker.ts`、`runtime/pi_core.py`、`runtime/pi_brief.py` 及本版本测试/探针。正式验收命令与 phase 合同一致：

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/validate_codemode.py
python3 scripts/check_public_privacy.py
```

## 真实 Pi / QuickJS 探针

`scripts/validate_codemode.py` 在已安装的 Pi 1.0.0 SDK 会话内加载候选 `runtime/pi_worker.ts`，使用进程内脚本化 provider（无网络、无凭据、无付费模型），并运行真实 QuickJS codemode worker。探针临时合成主检出、worktree、冻结 tools、checks 与 worker 配置；不读取用户凭据、业务任务或看板证据。

探针记录的属性与负向对照：

| 属性 | 观测 | 负向对照 |
| --- | --- | --- |
| 原生注册与装载证明 | 真实探针里 `codemode` 在 active/all 工具中；`worker.ready` 含 `codemode:true` 且在首个工具执行前存在 | 离线 harness 里 stub 缺失导出时不写 marker 并阻断全部调用（真实探针只覆盖成功注册路径） |
| `models` 缺失 | 脚本内 `typeof models === "undefined"` | `typeof tools === "object"`（真实工具面仍在） |
| 结构化成功/失败 | 成功 `check` 返回 `ok:true, exit_code:0`；失败返回 `ok:false, exit_code:1`，嵌套调用 `isError:true` | 失败脚本的 codemode 外层仍可成功返回该结构化数据；语义由 `ok` 判断 |
| 嵌套禁止写 | 写入主检出路径被 `forbidden-path` 拒绝，目标文件未变，`worker-blocks.jsonl` 有记录 | 同一脚本写入 worktree 路径成功 |
| 截止时间约束 | 省略 options 的脚本在约 2 秒（配置的有界缺省）超时；`timeout_ms:999999999` 被压到命令上限约 6 秒 | 快速脚本在默认截止时间内正常完成 |
| 非法选项 | `timeout_ms:0` 的脚本在运行前被拒，无嵌套调用 | 合法 options 的脚本正常执行 |
| 取消传播 | 会话 abort 后嵌套 bash 子进程不再存活，codemode 结果显式失败/取消 | 未 abort 的同一脚本由截止时间正常收束 |
| store 成功与 resume | 成功脚本写入的值在同一会话的后续脚本与从会话文件恢复的新会话中都可 `load` | 失败脚本写入的值在同一分支后续脚本与 resume 后均为 `undefined`；未验证真正的分支/fork 切换 |

探针一次运行约 10 秒，退出 0 仅表示 9 项观测全部通过，不代表主会话验收。

### 保留原始证据

`CODEX_PI_PROBE_KEEP=1 python3 scripts/validate_codemode.py` 在进程退出后保留临时目录并打印其路径；未设置该变量时，无论成功或失败都在 `finally` 中删除该探针目录。验证方式是在 probe 子进程退出后由父进程直接读取保留路径下的 `report.json`：

```sh
OUT=$(CODEX_PI_PROBE_KEEP=1 python3 scripts/validate_codemode.py)
RETAINED=$(printf '%s\n' "$OUT" | sed -n 's/^probe evidence retained outside tracked files: //p' | tail -1)
test -r "$RETAINED/report.json"      # the probe process has already exited
```

未设置 `CODEX_PI_PROBE_KEEP` 的同一条命令不会留下 `codex-pi-codemode-probe-*` 目录。保留目录属于私有路径，不进入公开仓库。

## 离线回归

- `tests/test_worker_extension.py` 通过 Node 原生 type stripping 与离线 ExtensionAPI harness 驱动真实扩展：装载顺序、缺失导出、codemode 选项规范化（缺省/超额/非法/空源码）、源码文本不因包含禁止路径被扫描、嵌套 `codemode` 阻断，以及 `check`/`progress`/`readiness` 的结构化结果。
- `tests/test_codemode.py` 用合成 round 事件流验证：顶层与嵌套调用计数各一次、嵌套 bash 的退出观测保留、嵌套工具 usage 不叠加到 assistant usage、失败与取消不制造假的成功。
- 既有 `tests/test_archive.py` 的采用测试继续验证采用不改 task/session/helpers；探针另外验证原生 resume 保留成功写入。

## 主会话最终验收

2026-10-03，主会话核对了最终候选的三项不可变回执：完整 Python 回归 300 项通过、真实 Pi/QuickJS 探针 9 项通过，均无跳过；隐私扫描退出 0、零发现，扫描文件数不作为测试数。回执的候选 HEAD、干净工作树和日志 SHA-256 均已核验。

另外以候选 0.7.0 冻结运行时、Pi 1.0.0 和项目配置的 `deepseek/deepseek-flash` / `max` 运行了独立、三分钟限额的真实 worker 冒烟任务。原始事件确认两次 codemode 调用：第一次嵌套写入及读回固定字节文件，并观察 `typeof models` 为 `undefined`；第二次在提交后嵌套调用 `check`，固定字节断言与十秒等待成功，返回结构化成功结果和绑定该提交的回执。所有 writer 释放后，交付卡实际抵达原桌面聊天并由主会话审查接受。

冒烟文件只留在独立验证工作树，没有整合进插件源码。实施候选经审查接受后整合到主分支，整合后的文件树与已验证候选一致；本节为后续文档补充。原始会话和回执保留在私有证据目录。未做对照性能实验，不声称固定 token 或时间降幅。

## 验证边界

- 守卫仍是工具边界上的路径 token 检查，不是 OS 沙箱；`bash` 保留本机能力，运行期拼出的路径不可见。
- 探针的 provider 是本地脚本化输入；上述真实 worker 验证只覆盖本次配置的 provider，不证明所有 provider 行为。
- 桌面 queue 投递由独立冒烟验证；未重新安装插件、发布或修改 hook 信任。
- 未决：无。设计 API（Pi 1.0.0 `createCodemodeExtension`、嵌套 `parentToolCallId`、结构化结果）与实现一致。

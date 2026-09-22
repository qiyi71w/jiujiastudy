# 05 — 安全开启或关闭整个账号服务

**What to build:** `/canvas off` 暂停账号采集、AI 和自动日报而 Bot 保持可查询管理；`/canvas on` 立即采集并恢复计划，运行中切换不会推送失效结果。

**Blocked by:** 03 — 停止和恢复单项任务提醒；04 — 每日按随机时间交付可恢复的日报

**Status:** ready-for-agent

**Guardrails:**
- 先持久关闭状态及失效代际再确认命令。下一外部请求、快照提交、日报形成与每次业务发送前检查许可；发出去的外部请求/消息不保证撤回。
- 快速 off/on 不复活旧任务，不通过杀死 Bot 实现关闭；重复 on/off 幂等。
- on 始终按已确认规格立即采集。若当天没有已形成的有效计划日报，本次计入当天并发所有启用端；若当天已扫描，本次结果仅回复发起端。
- 当天此前已有有效计划日报但部分端待投时，恢复该日报的剩余投递，不能把已成功端再发一遍；这是恢复投递，不用再次采集生成它。on 自身的即时采集独立用于命令回执。关闭期间不追发多日历史。
- 关闭仍可 help/status/旧 report、任务与已有设置修改，操作不得调用 Canvas/模型；课程管理06和AI08沿用同一许可边界。

- [ ] 真实 Telegram 开关和状态正确，重启保持；off 后 refresh 拒绝，旧 report 标时。
- [x] 关闭时 tasks 查看、停止、恢复与时间/时区设置可用，Canvas/AI 请求为零，数据及意愿保留（受控 HTTPS MockCanvas 与持久档案）。
- [x] 受控慢分页请求返回前 off，后续请求、提交和发送均停止；快速 on 不复活旧结果（受控 HTTPS 与 Telegram 发送边界）。
- [ ] 在计划日报形成前取消、形成后未投、部分端已投三种状态执行 on，均满足以上即时刷新、计划计数与独立恢复投递规则；重复 on 不刷新。
- [ ] 更新 help 可用条件；以外部请求、持久状态和实际回执证明。08验证模型执行中 off，07/09验证跨端切换。

原生要求：Telegram 测试 Bot和可控慢 HTTP 服务。覆盖 V09/V10，模型与双端在后续票验收。

**Source:** [Canvas 多账号每日提醒与双端私信服务](../spec.md)。本票列出的验收是完成门槛；模拟覆盖与真实平台验收分别记录，缺少必需原生条件时不得以模拟结果关闭任务。

## Completion record

2026-09-22：账号开关代码及离线受控验收完成，任务单保持打开；真实 Telegram 私聊与双端部分投递尚无对应原生证据，不能以模拟代替。

- `tools/cc_account.py` 持久保存服务开关与每次切换的失效代际；off 在回执前取消采集中计划，保留快照、任务决定及设置。on 立即采集：未形成的当日计划日报立即形成并投递；已形成的日报保持固定正文和逐端成功记录，未投端恢复投递；已扫描日的本次刷新只回复发起端。重复 on/off 不采集。
- `tools/cc_service_security.py` 在每个 HTTPS 请求与分页/重试前检查代际；采集提交和 Telegram 分段业务发送也检查许可。接收命令时记录代际，排队的旧 on 不会在 off 后重新开启；旧命令回执及异常回执不会跨代际发送。help/status、report、tasks 和 schedule 在关闭状态继续可用。
- 本地运行 `python3 -B -m unittest -q test_account_service test_partial_collect test_time_and_manual.RelAcrossZones`：54 项通过。受控 HTTPS MockCanvas 记录关闭后无后续分页或快照提交、快速 off/on 只提交新代际结果；固定时钟覆盖计划形成前、已形成待投、已成功不重投、提前 on 立即投递、重复 on、关闭管理与重启持久性。Telegram 模拟发送器检验接收端与代际边界；此证据不等于真实客户端收件。
- 原生门槛：当前进程未配置 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_TEST_BOT_TOKEN` 或测试档案路径；未启动真实 Telegram 测试 Bot，故第 16、20 条的真实私聊状态、回执、重启观察仍未验证。第二端尚待任务 07，无法证明“部分端已投、仅恢复未投端”的原生双端投递；第 19 条仍未勾选。受控慢 HTTP 已覆盖采集边界，但不能冒称已做原生 Bot 联合测试。模型执行中 off 属 08，跨端切换属 07/09。

### 最终审查目标与裁决

- 根目录 `/home/dev/dev/jiujiastudy`；固定点、review base、审查时 HEAD 均为 `6bcc12e7723250640eddd6374d3a9cc4faeea1e6`。本票 owned delta 为 `tools/cc_account.py`、`tools/cc_service_security.py`、`tools/cc_telegram.py` 的开关边界、`tests/test_account_service.py` 新增开关验收及菜单、`README.md` 开关说明和本任务单。原有 Telegram 纯文本呈现、相应测试/README 差异以及其他未提交文件不属于本票。
- Standards、Spec 独立 `FULL_REVIEW`：Spec 发现提前 on 未立即投日报（SPEC-05-1）、旧 on 仍可能回复（SPEC-05-2）及 help 未说明关闭条件（SPEC-05-3）；修复前分别复现失败，修复后聚焦路径转绿。`VERIFICATION` 发现排队/重复 on 的失效竞态尚存，并指出有效 on 异常时误抑制回执（SPEC-05-NEW）；收敛修复前又复现三条失败，修复后均通过，54 项相关检查通过。双轴 `FINAL_REVIEW` 判定所有四项 resolved，未发现修复引入的新阻断；代码审查裁决 `SUCCESS`，**零 follow-up 项**。
- 代码审查成功与原生验收完成是独立门槛；本票仍未关闭，未验证标准保持未勾选。

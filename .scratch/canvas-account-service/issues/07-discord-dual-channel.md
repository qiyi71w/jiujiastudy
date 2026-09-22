# 07 — 通过 Discord 私信使用共享账号服务

**What to build:** 账号支持 Telegram-only、Discord-only 或双端；两端共享设置和任务状态，定时日报分别投递，命令仅在发起端回复。

**Blocked by:** 06 — 管理自动加入的课程与退出课程

**Status:** needs-info

**Guardrails:**
- 通过传递依赖已获得任务管理、计划、开关和课程操作，使用共享操作目录和逐端投递记录，不复制业务逻辑。
- 管理员预设同一学习者的两端身份，专属 Discord Bot 仅私信；陌生人、服务器频道、过期按钮拒绝。无按用户名关联、任意转发、自助绑定或频道回退。
- 采用无需公网入站端口的官方出站交互方式，核实 Discord 私信命令/安装权限。AI08通过同一操作目录接入，09验证最终全命令，不在此伪造AI。

- [ ] Discord-only 配置不要求 Telegram 凭据，真实 Discord 私信 help/status/report/refresh/on/off/courses/tasks 及按钮完整可用。
- [ ] 实际双端课程/任务/时间/开关共同生效，status 显示同一账号渠道启用及可用状态，查询不触发 Canvas/AI。
- [ ] 日报各端独立投递，只重试失败端且无第二次采集；命令仅发起端，on 当日补日报规则与05一致。
- [ ] 接收身份轮换不转发旧身份问答；私信不可达不向服务器频道发送，跨账号/未授权操作无数据泄漏。
- [ ] 真实两端长任务及时确认接收再发结果，分段、转义正确，不可信课程文本不触发额外 mention 或伪装控件；帮助反映所有已交付命令和可用条件。

原生要求：Telegram/Discord 专属测试 Bot、同一学习者身份及未授权身份、私信访问条件；平台故障可用受控传输补充。覆盖 V02/V06/V08/V10/V12/V18。

**Source:** [Canvas 多账号每日提醒与双端私信服务](../spec.md)。本票列出的验收是完成门槛；模拟覆盖与真实平台验收分别记录，缺少必需原生条件时不得以模拟结果关闭任务。

## Completion record

2026-09-22：实现 Discord-only、Telegram-only 和双端配置；Discord 出站 Gateway 的 Bot 私信全局 `/canvas` 命令及按钮使用共享 `AccountService`。课程、任务、计划和总开关共享状态；定时日报逐端记录回执，只重试未送达端。两端身份在管理员配置中固定，操作与每段投递重新校验绑定；Discord 不向服务器频道回退。

- 隔离 HTTPS MockCanvas、模拟 Telegram/Discord 传输与固定时钟：验证双端投递重试不重复采集、Discord-only 与身份轮换、跨端共享课程/任务/计划/开关、私信与组件授权、按钮消费、长文本分段与 mention 中和。Discord 空闲连接心跳、Telegram off→on 提前确认及轮换后旧进程不得覆盖新身份状态三项修复均有修复前失败、修复后通过的聚焦复现。`PYTHONPATH=/tmp/jiujiastudy-discord-deps python3 -B -m unittest -q test_account_service`：58 项通过；`git diff --check` 通过。上述模拟不是平台原生验收。
- 原生门槛尚未满足：本次环境没有 Discord 专属测试 Bot token、绑定用户 Discord ID、同一学习者的两端私信可访问条件及未授权测试身份。未运行真实 Discord `/canvas` 私信命令、按钮、断连/不可达、长任务或双端同日报；五条验收均未勾选。需提供隔离测试 Bot 和两端身份/私信访问条件，再以真实 Telegram、Discord 私信及受控 Canvas 数据逐条验证，不能以离线结果关闭本票。
- 审查目标 `/home/dev/dev/jiujiastudy`；固定点、review base 与审查时 HEAD 均为 `ba27dd844992e535ed5510c9137cb3a1077da387`。Standards/Spec 独立 FULL_REVIEW 后，SPEC-07-1（空闲 Gateway 健康状态过期）、SPEC-07-2（Telegram on 确认迟到）、SPEC-07-3（轮换后旧进程覆盖状态）均由聚焦场景先复现、后修复；VERIFICATION 两轴均判定 resolved，无新增阻断。裁决 `SUCCESS`，零 follow-up 项；原生验收是独立的未完成门槛。

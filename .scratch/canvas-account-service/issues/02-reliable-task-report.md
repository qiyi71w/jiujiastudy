# 02 — 在刷新日报中可靠呈现提交状态与部分失败

**What to build:** 学习者重复刷新时能看到正确的提交变化，某门课程失败不妨碍其他课程更新；同时刷新及重启不会丢失未通知变化或把旧数据误当新数据。

**Status:** resolved

**Guardrails:**
- 以 Canvas 提交和豁免记录为准，已提交未评分不催交，有分数不是已提交的充分证据；线下未知显示需确认，不采用 record done 或备注替代提交状态。
- 个人延期、组作业、测验及明确重新提交按实际字段语义；无法确认保留未知，不能从低分/批语猜测重新提交。
- 保留当前及上一成功快照、逐课新鲜度、变化记录与必要投递标记。初次快照不是全量新增；同一变化不重复提示，手动刷新不消费未来定时通知的变更归属。
- 同账号复用进行中采集并串行更新，不设手动冷却，仍遵守服务端限流。按已验证命令目的分离结果消费者，后续定时目的可挂接同一次采集。
- 避免扩大本地采集读取范围；服务端新课沿用既有筛选自动加入，用户停止课程覆盖由06实现。

- [x] 同一 MockCanvas 场景经未交→已提交→评分变化后正确更新，豁免过滤和未知状态正确；已提交任务仅改期不重新催交。
- [x] 日报具体包含01列出的全部类别；提交变化只作为新变化提示一次，无日期和失败范围不会被省略。
- [x] 一课失败其他课照常更新，失败课程旧数据保留并标时，缺失不误判删除/完成；全部失败清楚报错，不覆盖上一成功快照。
- [x] 首次基线、后续新增、改期、提交与新课程提示经过重复扫描和重启保持正确，尚未用于定时日报的变化不会被手动查看抹掉。
- [x] 两个并发 refresh 复用进行中采集，结束后新请求立即允许；状态不会互相覆盖，采集结果具备完整/部分失败依据供 AI 使用。
- [x] 通过实际 Telegram refresh/report 观察失败说明和数据时间；离线测试复用 PartialCollect、CourseListRefresh、RelAcrossZones 与隔离 CLI 场景，验证真实状态与请求而非内部字段搬运。

原生要求：Telegram 测试 Bot；特殊 Canvas 状态和故障用受控 fixtures，不能声称已验证学校所有类型。覆盖 V05/V08/V11/V16/V17，最终跨端与真实学校范围归09。

**Source:** [Canvas 多账号每日提醒与双端私信服务](../spec.md)。本票列出的验收是完成门槛；模拟覆盖与真实平台验收分别记录，缺少必需原生条件时不得以模拟结果关闭任务。

## Completion record

### 实现与验收状态

2026-09-22：实现、离线验收、代码审查及真实 Telegram 私聊验收全部通过。授权用户实际发送 `/refresh`、`/report`，原生回执及客户端确认均已记录，六项验收全部完成，本票关闭。Canvas 特殊状态使用受控 fixtures，真实学校综合语义仍归 09。

- `tools/cc_account.py`：以提交记录、豁免及明确 `redo_request` 判定提醒；使用请求用户适用的作业 `due_at`。逐课成功合并，失败课保留旧事实与时间；全部失败只记录本次尝试，不推进任务快照。
- 当前及上一成功快照、逐课新鲜度、单调变化编号和 `manual` / `scheduled` 投递义务保存在 `service-report.json`。初次逐课快照是基线；已确认手动投递不会删除定时义务，发送失败或重启保留未确认变化。
- 采集锁与状态锁分开；重叠刷新复用已在进行的尝试，采集过程中到达的投递确认不会被旧状态覆盖。结果提供 `complete` 和逐课 `failures`，供后续消费者判断是否可以使用。
- `tools/cc_telegram.py`：轮询期间可并发接收刷新；日报投递串行，发送前重读待通知变化，全部分段成功后才确认。网络回执未知或发送后进程中断仍可能重复，不承诺严格 exactly-once。
- `README.md` 已同步操作、失败/快照语义及通知保留说明。本地采集、备注和学习完成流程不变；定时投递实现仍属 04，真实学校综合语义仍属 09。

### 已执行证据

1. 在 `tests/` 执行 `python3 -B -m unittest -v test_account_service test_partial_collect test_time_and_manual.RelAcrossZones test_cli test_golden`：58 项通过。涵盖指定的 PartialCollect、CourseListRefresh、RelAcrossZones、隔离 CLI 及四地区 golden；未扩大到全仓库测试。
2. 补充新课全失败与投递并发边界后，`python3 -B -m unittest -v test_account_service`：18 项全部通过。真实回环 HTTPS 请求覆盖未交→提交→评分、豁免、线下未知、组作业、测验、个别延期、明确重新提交、部分/全部失败、并发复用、立即再次刷新、重启恢复、发送失败重试和采集中确认投递。
3. 新增关键回归先观察失败，再实现：仅有评分导致任务漏催；单课失败导致完整性误报；缺少投递确认入口；并发请求直接返回“已有刷新”；过时 `cached_due_date` 遮盖有效延期；所有作业请求失败时丢失新课程通知。对应聚焦检查随后通过。
4. 独立临时 smoke 实际调用账号服务和 HTTPS MockCanvas：首次基线、后续新增、改期、提交过滤、零分无日期任务、部分合并、全部失败不提升、上一成功快照保留均通过；新 Python 进程读取 `report` 与失败后的持久报告一致。受控 Canvas 只观察到 GET 请求；临时档案已清理。
5. 真实 Telegram 测试 Bot 的受限凭据可用，使用仓库外隔离档案和 HTTPS MockCanvas；模拟 ACCT1101 的 HTTP 403，PSYC2012 同时更新。实际执行账号 `refresh` / `report` 并经真实 `TelegramBot.send()` 投递，Telegram 返回 message_id 54、55、56、57：两个日报各两段，回执正文包含失败课程、旧数据时间、采集完整性与其他课程更新。此项证明真实出站传输，不替代用户私聊入站或客户端观感。
6. 首轮真实 long polling 验收未收到所需入站消息，收尾时停止临时轮询并释放 Bot。随后恢复隔离场景，实际私聊命令及客户端确认均通过，详见下方“原生验收结论”。
7. 提交隔离检查：从当前 HEAD 的索引导出独立临时源码树，仅叠加本票增量，保留用户原有菜单/显示修改在工作区未暂存；该候选树的 18 项账号服务测试全部通过。初次用 `git archive` 因项目 export-ignore 排除了测试支撑文件，随后改用完整索引导出；没有把测试环境缺文件算作产品失败。

### API 语义依据

- [Canvas Assignments](https://developerdocs.instructure.com/services/canvas/resources/assignments)：`due_at` 已是对请求用户应用 override 后的日期；`lock_at` 表示锁定时间，不能覆盖无截止日期事实。
- [Canvas Submissions](https://developerdocs.instructure.com/services/canvas/resources/submissions)：`submitted_at`、提交 workflow 和 `excused` 是事实证据；`redo_request` 表示教师明确重新分配，评分或批语不构成重新提交要求。
- 特殊提交类型与故障的证据来自受控 fixtures；不声称已验证学校所有类型或真实学校个人延期实现。

### 最终审查目标与裁决

- 根目录：`/home/dev/dev/jiujiastudy`；固定点、review base、审查时 HEAD 均为 `3d37b13230764483910c859466065c222c762e18`。
- 目标：本票相对开始工作时 dirty tree 的 owned delta，涉及 `tools/cc_account.py`、`tools/cc_telegram.py`、`tests/test_account_service.py`、`README.md` 和本任务单。开始前已有的命令菜单/显示修改、任务 01 记录及其他规划配置不属于本票修复范围。
- Standards 和 Spec 在独立上下文执行 `FULL_REVIEW`；父代理在批次前后核对 HEAD、diff、status、未跟踪列表及内容一致。两轴均无候选问题，修复型流程裁决为 `SUCCESS`，没有开放 `IN_SCOPE` 项，**零 follow-up 项**；无需追加修复轮。
- 当前 checkout 的 CodeGraph 已初始化并在审查前同步；临时 CRG 完整构建为 minimal，与上述 HEAD 对齐、无解析错误。源码在审查期间冻结；图仅补充定位。任务创建的临时索引在收尾清理。
- 实现提交：`865520f`。本次收尾仅补充原生验收记录，未修改产品代码；代码审查与原生验收两道门禁均通过。

### 原生验收结论

2026-09-22 恢复仓库外隔离场景，沿用真实测试 Bot 及授权私聊绑定；ACCT1101 作业接口受控返回 HTTP 403，PSYC2012 正常更新。

- 真实 `/refresh` 入站：update_id `736988405`；先收到确认消息 `60`，再收到日报 `61`。此次受控 Canvas 请求数为 4。
- 真实 `/report` 入站：update_id `736988406`；收到快照日报 `63`。此次 Canvas 请求数为 0。
- 日报回执 `61`、`63` 均含 ACCT1101 失败说明、旧数据时间 `2026-09-22T19:10:43+00:00`、完整性“不完整”、本次尝试时间，以及 PSYC2012 的“其他课程已更新”。
- 用户对上述明确预期回复“通过”：实际客户端内容与显示验收完成；按用户实测结论记录，不重复要求验证。
- 临时测试服务与档案在收尾清理，释放 Bot。原测试档案保持独立；本次原生证据证明 Telegram 私聊链路，不声称覆盖真实学校所有 Canvas 类型。

# 01 — 在 Telegram 私信接通安全的 Canvas 刷新

**What to build:** 管理员配置明确站点和专属 Bot 后，授权学习者可以用 help/status/report/refresh 查看一次成功采集生成的规则日报，数据持久保存供后续查看。

**Blocked by:** 原生验收材料：真实 Telegram 测试 Bot 秘密文件及授权、未授权测试身份。

**Status:** needs-info

**Guardrails:**
- Canvas 显式 HTTPS 地址与凭据绑定，缺失拒绝请求，不猜站点；验证、分页与重定向禁止跨 origin 或降级发送秘密，更换站点需重新绑定。秘密通过受限配置注入，不进日志/消息/示例，Telegram URL token 也脱敏。
- Telegram long polling，仅管理员预设身份的私聊可用，不采用先到先绑定；用户不能选择任意站点、路径、账号或收件人，绑定只由管理员维护。
- 优先复用既有 collect/radar/config 公共逻辑；服务端显式刷新绕过缓存，本地缓存、课程发现和原有人工确认写入流程保持；不下载课件或采集无关站内信。
- 共享账号操作入口接收已鉴权身份、命令参数、发起渠道、关联标识，输出规则结果、数据时间、完整性和结构化交互动作；渠道仅适配输入输出。命令目录为帮助和后续渠道提供统一操作集合，不建立测试专用公开 API。
- 本票以完整成功刷新为纵向交付；失败必须明确报告，禁止把不完整数据包装为完整日报或覆盖成功快照。02 补齐逐课程失败继续更新、变化历史与并发协调，不在此重做已有领域规则。

- [ ] 真实 Telegram 私信 help/status/report/refresh 可用；无快照明确说明，help/report/status 不触发 Canvas。成功刷新后重启仍能查看标明采集时间的快照。
- [x] 成功日报列出未来七天待交、逾期未交、新增与改期、最近已提交、新课程和日期不明任务；附来源链接、数据时间及完整性说明，无待交仍确认。复用现有事实计算，不宣称学习完成等于提交。
- [x] 新课程按现有筛选规则进入采集，初始快照作为基线；02 完成重复扫描变化可靠性。
- [ ] 真实 Bot 长任务先确认接收、结束后回结果；分段转义正确，不可信课程文本不会触发额外 mention 或伪装交互按钮。未授权用户和群聊不泄露数据。
- [x] 受控请求证明显式 origin、重定向、站点变更和日志脱敏；既有本地 CLI 聚焦回归通过。提供实际启动说明，不交付未接通的 scaffold。

原生要求：Telegram 测试 Bot、授权与未授权身份；Canvas 成功路径可用隔离 MockCanvas 演示，真实学校凭据须获准。真实 Canvas 综合语义验收归09。缺少 Bot 凭据时不能称原生验收通过。覆盖 V02/V03/V17/V18 的首条路径。

**Source:** [Canvas 多账号每日提醒与双端私信服务](../spec.md)。本票列出的验收是完成门槛；模拟覆盖与真实平台验收分别记录，缺少必需原生条件时不得以模拟结果关闭任务。

## Completion record

### 实现与验收状态

实现日期：2026-09-22。代码审查通过；原生 Telegram 验收未完成，本票保持开放。

- `tools/cc_account.py`：共享命令目录与账号操作，复用 `Ctx`、`refresh_courses`、`snapshot_from`、`deadline_rows`；仅完整成功后保存 `service-report.json`，启动时安全初始化缺失的当前版本状态。
- `tools/cc_service_security.py`：受限秘密文件、显式 HTTPS origin 绑定、同 origin API 分页、拒绝重定向、服务端列表响应校验。`tools/canvas_api.py` 的默认解码规则不变，通过受保护的解码扩展点复用原分页实现。
- `tools/cc_telegram.py`：固定身份私聊鉴权、long polling、先确认刷新再回结果、UTF-16 分段及转义。部署步骤见 [README](../../../README.md#telegram-私聊账号服务)。

### 已执行证据

1. `python3 -B -m unittest discover -s tests -p test_account_service.py -v`：10 项通过。使用临时证书的回环 HTTPS MockCanvas；覆盖只读命令零请求、刷新失败保留快照、重启读取、新课与首次基线、权限/origin/绑定拒绝、重定向与分页拒绝、Telegram token URL 错误脱敏、鉴权/确认顺序/分段、首次启动及错误课程列表。
2. 在 `tests/` 执行 `python3 -B -m unittest -v test_cli test_partial_collect test_golden test_doctor test_token_chat`：53 项通过。涵盖本地缓存、课程发现、部分采集、四地区 collect/radar/study 输出、doctor 与 token 流程；未修改既有 golden。
3. 隔离场景实际调用账号服务：未来任务、逾期、无日期、最近已提交、新增、改期分别出现在对应段落，带来源及采集时间；清空作业后明确确认无待交。新课程通过独立 HTTPS 场景验证立即纳入采集，首次作业不计新增。
4. 新 Python 进程读取同一隔离档案的 `report`，与刷新返回的日报及采集时间一致，无需重新采集。
5. 从仅有说明中配置与受限秘密文件的新目录启动真实 CLI 入口，模拟 Telegram API 观察到 `getMe → getWebhookInfo → getUpdates`；无 Canvas 请求。该证据仅证明启动及协议适配，不是原生 Telegram 验收。
6. 既有 `coach.py api post` 隔离 smoke：无 `--confirmed` 返回预览、`sent: false`、退出 0；带 `--confirmed` 而系统窗口不可用时返回 `confirm: unavailable`、`sent: false`、退出 2。两次均未发送 HTTP 写请求；未声称验证真实系统确认窗口。

### 最终审查目标与裁决

- 审查根目录：`/home/dev/dev/jiujiastudy`；固定点、review base、审查时 HEAD 均为 `78a100ee65191b5ef9c913aa4705fbcb100f4db9`。
- 最终目标：该固定点后的本票工作树变更——`README.md`、`tools/canvas_api.py`、`tools/cc_account.py`、`tools/cc_service_security.py`、`tools/cc_telegram.py`、`tests/test_account_service.py`。既有用户规划文件与配置变更不属于审查/修复范围；本完成记录在审查后追加。
- Standards 与 Spec 独立 `FULL_REVIEW`，修复后独立 `VERIFICATION`；每批前后核对 HEAD、diff、status、未跟踪列表及被审查内容一致。最终裁决：`SUCCESS`，没有开放的 `IN_SCOPE` 阻塞项，**零 follow-up 项**。
- 修复复核前，当前 checkout 的 CodeGraph 完整重建；临时 CRG 最小构建与上述 HEAD 对齐。未跟踪文件直接审阅，不以图结果替代源码或执行证据；任务创建的索引在审查后清理。

#### Standards

**STD-01 — IN_SCOPE / resolved：按文档新建档案无法启动。** 原因是缺少 `state.json` 时，`Ctx` 将空状态识别为 v1，服务在接通 Telegram 前拒绝；既有 FakeHome 同时种入 config/state，未覆盖此路径。修复前按文档创建新目录实际复现 `ValueError: 服务需要当前版本的独立档案`，新增回归先失败。修复后先验证当前配置与秘密，再仅对缺失状态调用既有 `minimal_state()`；不覆盖已有旧状态。客观验收已满足：新档案可启动、status 零 Canvas 请求、首次刷新成功、已有 v1 状态仍拒绝。Spec 的同根候选 SPEC-01 合并至本项。

#### Spec

**SPEC-02 — IN_SCOPE / resolved：无效课程清单被当作完整采集。** 复用的本地 `refresh_courses` 会容忍非列表响应，通用分页还会把 JSON null 转为空列表；服务原先误把“无变化”当成发现成功。修复前受控 HTTP 200 `{}` 使成功日报时间由 `2026-03-24T23:00:00Z` 推进到 `2026-03-25T01:00:00Z`，且覆盖旧日报；`{}` 与 null 回归均先失败。修复后服务在每页解码后、分页解释前检查课程/作业列表；异常经既有发现错误路径中止刷新。客观验收已满足：两类响应均明确失败，先前日报与配置保持不变；有效列表分页、新课筛选与首次基线保留，本地默认解码行为保持。

### 尚未验证的必需条件

没有提供真实 Telegram 测试 Bot 的受限秘密文件、授权学习者及未授权测试身份；当前环境未发现相应凭据变量或仓库部署配置。第 1、4 项验收保持未勾选：仍需在真实 Telegram 验证四个命令、长任务回执、分段/转义、陌生私聊及群聊拒绝、刷新后的重启读取。请由管理员注入受限秘密文件并提供文件路径与测试身份；不要将真实秘密写进任务单或仓库。MockCanvas 成功路径已按本票许可使用，未访问真实学校账号；真实学校综合语义验收仍归 09。

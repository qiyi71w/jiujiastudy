# 救驾

给用第二语言上课的留学生的 Canvas 学习手帐：盯 deadline、排本周该学什么、按完成情况给下一步。它是一个 Agent Skill，装在你自己的电脑上，数据存在你自己的文件夹里。

[![tests](https://github.com/jiujiastudy/jiujiastudy/actions/workflows/ci.yml/badge.svg)](https://github.com/jiujiastudy/jiujiastudy/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](#要求)

## 它做什么

- **Deadline 雷达**：每门课下一件要交什么、最急的一件、几件撞在一起的、Canvas 上没写日期的，一页看完。
- **本周清单**：按模块和 deadline 排出每门课这周要看的、要做的，每天一件必做。
- **状态和建议**：按打勾情况和你说的话判断正常、落后、过载，给一句具体建议。
- **课件在后台下**：Canvas 原件自动下到每门课的文件夹，从不让你等。
- **发到 Canvas 之前先过你的手**：发帖、发站内信、交作业都先给预览，再弹系统窗口由你本人点确定。

## 要求

- Python 3.10 或更新（CI 在 3.10–3.14 上测过）。没有的话 AI 会先问你要不要装。
- 一个能读 Agent Skills、能跑命令的 AI 助手：Claude Code、Codex 都行。
- Windows、macOS、Linux 都行。

## 安装

跟你的 AI 说一句（两种说法都行）：

> 帮我安装 救驾：https://github.com/jiujiastudy/jiujiastudy

> 帮我安装 GitHub 上的 jiujiastudy/jiujiastudy

两句都带了仓库地址（`jiujiastudy/jiujiastudy` 就是地址）。光说「救驾」或「jiujia」不行：模型不认识新仓库，可能装到别人的同名仓库，而这个技能会拿到你的 Canvas token。请只从上面这个地址安装。

安装和第一次运行时 AI 会请你点「允许」。用 Claude Code 自动模式（Auto）被拦下「刚下载的代码」的话，把输入框旁的权限模式换成每次询问，再说「继续」。救驾不改任何权限设置。

**装好不用重启**，直接跟 AI 说「最近要交什么」。

### 手动安装

`SKILL.md` 要直接在 `skills/jiujiastudy/` 下面，不要多套一层。

Claude Code（macOS / Linux / Git Bash）：

```bash
git clone https://github.com/jiujiastudy/jiujiastudy ~/.claude/skills/jiujiastudy
```

Claude Code（Windows PowerShell）：

```powershell
git clone https://github.com/jiujiastudy/jiujiastudy "$HOME\.claude\skills\jiujiastudy"
```

Codex 及其它读 Agent Skills 的工具：把上面的 `.claude` 换成 `.agents`。

没有 git：下载 [main.zip](https://github.com/jiujiastudy/jiujiastudy/archive/refs/heads/main.zip)，解压出来的文件夹叫 `jiujiastudy-main`，改名成 `jiujiastudy` 再放到上面的位置。Windows「全部解压」会多套一层，确认 `SKILL.md` 直接在 `jiujiastudy` 文件夹里，多套了一层就把里面那层拿出来。

更新：在 `skills/jiujiastudy` 里执行

```bash
git pull
```

## 第一次使用

跟 AI 说「最近要交什么」，它会：

1. 用你电脑上已有的 Python。
2. 从浏览器记录里认出你学校的 Canvas 网址（**不会带 token 去试**），让你确认是哪个学校。
3. 你去 Canvas → Account → Settings → Approved Integrations → New Access Token 生成一个 token，直接发给 AI，它替你存好。
4. 采集，生成桌面文件夹、本周清单和 Deadline 雷达。

token 只要给一次：它存在你电脑上的 `~/.config/jiujiastudy/token`（Windows 是 `C:\Users\你的用户名\.config\jiujiastudy\token`，只有你本人的账户能读），以后每次直接用，只发给你确认过的那一个学校地址。要换就再发一个新的给 AI。token 也会留在你和 AI 的聊天记录里；介意的话，用完去 Canvas 撤销、重新生成。

平时这样说：「现在什么情况」「最近要交什么」「这周学什么」「做完了」「没状态」，或者直接说你想做的事：导读课件、做复习包、写东西、发帖、交作业。

## 配置

都是跟 AI 说一句它就改；想自己改，命令在下面。

| 想改什么 | 默认 | 命令 |
|---|---|---|
| 课件原件自动下载到你的文件夹 | 开 | `config set materials.auto_download false` |
| **把课件文字交给 AI 读**（导读、逐页精讲、复习包要用） | **关** | 按课打开：`config course <课程代码> --materials-ai on` |
| 显示时间跟着哪个时区 | 跟着电脑 | `config set user_tz Asia/Shanghai`，`auto` 恢复跟着电脑 |
| 这周是学期第几周 | 按模块名推断 | 跟 AI 说「这周是第 N 周」 |
| 资料放在哪个文件夹 | 桌面的「救驾」 | `config set root <路径>`，再让 AI 跑一次体检 |

作业、截止时间、模块、公告、站内信（收件箱）总是读，这是它的本职；只读，不改 Canvas 上的任何东西。

课件文字默认不交给 AI，是因为不少学校（悉尼大学也在内）把「把课程材料放进生成式 AI」列为误用。要不要打开，由你按你那门课的规定决定。

## 隐私与数据

- 给人看的：桌面「救驾」文件夹——本周清单、Deadline 雷达，每门课一个文件夹（课件放 Canvas 原件，产出放 AI 做的东西）。
- 给程序用的：同一个文件夹里的隐藏目录 `.coach`（配置、进度、采集到的原始数据）。
- token：`~/.config/jiujiastudy/token`，只有你本人的账户能读。以前存在环境变量或钥匙串里的也认。
- 本地 Skill 的档案保存在自己的电脑上；可选的 Telegram 账号服务由管理员自行托管，规则日报会发送到 Telegram。你让 AI 读的内容会经过你使用的那家 AI 服务。
- 发帖、发站内信、交作业：先给你看预览，你说「发」，再弹一个系统确认窗口，**你本人点确定**才真的发送。窗口弹不出来就不发，把链接给你自己交。

## Telegram 私聊账号服务

Linux/POSIX、Python 3.10+，仅用标准库；每个 Canvas 账号使用独立档案、专属 Bot 和管理员预设的 Telegram 数字用户 ID。服务使用出站 HTTPS long polling，无需开放入站端口。先确认学校允许自动访问和第三方托管。

1. 以独立受限系统用户运行；建立仅该用户可访问的档案目录（权限 `0700`），不要复用正在运行本地 Skill 的档案。
2. 在该目录的 `config.json` 设置 `schema_version: 3`、明确的 `canvas_host`（完整 HTTPS origin，如 `https://canvas.example.edu`）、课程所在地 `course_tz`、显示用 `user_tz`（IANA 时区），以及 `courses: []` 和 `service: {"telegram_user_id": 123456789}`。域名与数字 ID 均由管理员替换成实际绑定；首次刷新按既有规则自动发现课程。不要填入任何 token。
3. 在仓库和档案之外，用受限编辑器创建秘密 JSON 文件，权限必须为 `0600`，不能是符号链接。字段为 `canvas_origin`（与配置的规范化 origin 完全一致）、`canvas_token`、`telegram_bot_token`。秘密值只写入该文件，不放命令参数、示例、聊天或日志。更换 Canvas 站点必须同步重新绑定秘密；更换 Bot 或授权身份后重启服务。令牌轮换也应重启。
   如需可选 AI，管理员在 `service` 中设置 `ai_daily_limit`（非负整数，默认 0），并在同一受限秘密文件中同时设置 `ai_origin`（OpenAI-compatible HTTPS origin）、`ai_api_key`、`ai_model`；缺任一项均不能调用模型。凭据和模型名称不要放在普通配置或聊天中。额度日时区在首次建立服务档案时固定为当时 `service.daily_timezone`（默认 UTC），学习者之后调整扫描时区不会重置额度。供应商可能对失败请求收费，失败仍占用本次额度。
4. 授权学习者先在 Telegram 打开专属 Bot 私聊。Bot 不得配置 webhook；已有 webhook 时启动会拒绝，不会自动删除。
5. 用实际绝对路径启动，前台运行可观察安全错误提示：

   ```bash
   python3 -B tools/cc_telegram.py --home /srv/canvas-account/data --secrets /srv/canvas-account-private/service.json
   ```

支持 `/canvas help`、`/canvas status`、`/canvas report`（已有快照）、`/canvas refresh`（立即采集）、`/canvas on` / `/canvas off`（账号服务开关）、`/canvas courses`（监控课程按钮）、`/canvas tasks`（任务按钮）及 `/canvas schedule HH:MM Area/City`（每日基准时间和 IANA 显示时区）；`/canvas tasks stopped` 查看已停止项。也可直接使用 `/help`、`/on`、`/off` 等命令。help 和 status 都列出设置入口；status 显示服务开关、当地实际计划时刻及最近采集完整性，不访问 Canvas。默认基准时间为 09:00 UTC，初次设置后延迟在 0–15 分钟内抽取。重叠刷新复用进行中的采集，结束后的新请求立即允许，仍遵守 Canvas 服务端限流。未授权私聊和群聊静默忽略。
`/canvas ai [问题]` 在服务开启且用户打开 AI 后进行即时完整采集，完整成功才发送最小任务信息到配置的模型 API；例如 `/canvas ai 未来七天有哪些待交作业？`。`/canvas settings` 或 help/status 的「AI 设置」按钮可分别开关 AI、每日自动摘要、公告正文授权。三项默认关闭；公告正文只有单独授权后才采集和发送。status 显示额度日、余额和配置可用性，不访问 Canvas/模型；服务关闭时设置仍可修改。模型段落不替代规则日报、Canvas 截止日期和提交证据，公告日期仅附来源并标待确认。部分采集失败、模型错误/超时、额度不足时仍提供带失败范围的规则日报。自动摘要仅使用当次完整计划扫描，发送失败重用已生成内容，不再采集或调用模型。模型无工具、联网或多轮历史；发送给配置供应商的资料及供应商留存取决于其政策。

启动时从同一命令目录注册绑定用户私聊的原生命令菜单，包括 `/help`、`/status`、`/report`、`/refresh`、`/ai`、`/settings`、`/on`、`/off`、`/courses`、`/tasks`、`/schedule`。菜单不改变命令鉴权，不向其他私聊或群聊注册。

`service-report.json` 原子保存当前及上一成功快照、逐课数据时间、最近采集完整性、待通知变化、每个计划日的随机时刻及逐端投递状态。部分失败保留成功课的更新与失败课的旧数据时间；全部失败不推进任务快照，但形成明确标注失败的定时日报。日报包含未来七天待交、逾期未交、日期不明任务、新增改期、最近已提交及新课程，保留来源链接和逐课采集时间。没有待交任务且采集完整时仍给出确认。

每门课首次成功采集作为任务基线。定时日报形成后固定正文，Telegram 投递故障只重投正文，不重扫 Canvas；最多重试四次，超时回执可能造成重复，不承诺严格只送一次。手动查看确认不消费未来定时日报的变化记录。过期计划不补发多日历史；保留执行事实与未送达变化，已完成的旧日报正文可清理。课程首次采集、设置变化和手动刷新不使已扫描的计划日再次采集。
`/canvas off` 先保存关闭状态和失效代际，再确认；Bot 继续响应 help、status、标明数据时间的旧 report、任务停止/恢复及扫描设置，但不再采集或投递日报。已发出的请求和消息不能撤回；慢请求返回后不继续分页、提交快照或发送旧结果。`/canvas on` 从关闭状态立即采集；若当天未形成有效计划日报，则这次采集形成并投递当日日报；当天已形成则仅向发起私聊回复本次刷新，另外恢复原日报未成功端的投递，不重发成功端。重复 on/off 不触发采集。关闭期间不追发多日历史。

提交状态以 Canvas 证据为准：已提交未评分、待审阅和豁免不催交；仅有分数不代表已提交，线下及无法确认的状态标记需确认。个人延期使用 Canvas 为请求用户返回的 `due_at`；仅改期不使已提交任务恢复催交，明确的 `redo_request` 才表示要求重新提交。学习完成记录和本地备注不替代提交状态。正文按 Telegram 长度分段，课程文字以转义的预格式文本呈现，来源 URL 保留供打开或复制。

`/canvas tasks` 每页显示最多八项，按钮可翻页或查看已停止项。先点任务的「停止」/「恢复」，再点确认；「取消」不修改提醒。停止时可不填原因，也可选择「线下完成并停止」；已停止列表显示原因。提醒决定只影响催交，不写 Canvas、不授予学校豁免；重启仍保留。成功刷新检测到适用截止时间变化或 Canvas 明确 `redo_request` 时解除停止并通知；已提交/豁免始终优先过滤，单纯评分或批语不会解除停止。按钮仅在绑定的授权私聊有效，旧按钮、重复点击及任务版本变化后须重新打开列表。
`/canvas courses` 保留自动发现的课程和主动停止的决定：停课后不再拉取其作业，也不会因下一次发现自动加回；重新加入在服务开启时立即采集，关闭时仅保存选择。课程明确失去访问或从成功的在读列表消失时旧任务移出催交区、保留历史并提示确认；作业接口的临时 5xx 仅标记数据过期，不当作退课。

服务默认只访问身份验证、课程清单及作业/本人提交记录；仅在独立授权公告后采集其正文，不读站内信、不下载课件、不写 Canvas。AI 默认关闭并须管理员配置；HTTPS 分页和模型请求只访问绑定 origin，拒绝所有 HTTP 重定向。真实供应商兼容性需在有明确付费授权时用配置供应商验证；本地受控 API 仅证明过滤、额度和错误回退。真实学校特殊提交语义及更多渠道的综合验收见任务 09。

离线验证：`python3 -B -m unittest discover -s tests -p test_account_service.py -v`。安全场景使用临时证书的 HTTPS MockCanvas（需要 OpenSSL、POSIX），Telegram API 边界模拟不等于真实平台验收；部署前须用真实测试 Bot 验证授权/未授权身份、群聊拒绝、消息分段和重启。

## 写作业这件事

这个工具会帮你写草稿、讲稿、提纲、给老师的消息，也会按你的要求上传文件。**交什么、交不交，由你决定**：请自己遵守你所在学校和那门课对 AI 使用的规定，需要声明的地方自己声明。工具不会替你判断，也不会替你点提交。它不做任何「降低 AI 痕迹」之类的事。

## 卸载

1. 删掉技能文件夹（`~/.claude/skills/jiujiastudy` 或 `~/.agents/skills/jiujiastudy`）。
2. 删掉桌面的「救驾」文件夹和 `~/.config/jiujiastudy`（Windows：`C:\Users\你的用户名\.config\jiujiastudy`）。
3. 去 Canvas → Account → Settings → Approved Integrations 撤销那个 token。

## 反馈

问题和建议发到 [Issues](https://github.com/jiujiastudy/jiujiastudy/issues)，或在小红书、抖音私信 @悉尼苏丹（控制canvas版）。

## 依赖

只用 Python 标准库就能跑。少数功能会用到可选的第三方包（都不打包在仓库里，用到了才从 PyPI 装），清单和各自的许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 许可

MIT，见 [LICENSE](LICENSE)，不提供任何担保。与 Instructure 无关，未获其背书；Canvas 是 Instructure, Inc. 的商标。使用前请自行确认它符合你所在学校的规定。

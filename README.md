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

## 网站与 Telegram 账号服务

Linux/POSIX、Python 3.10+；先安装 `python3 -m pip install -r requirements.txt`。每个 Canvas 账号使用独立档案和稳定账号标识，网站使用用户名密码，Telegram Bot 与数字用户 ID 可选但必须同时配置。仅 Bot 模式使用出站 HTTPS long polling；网站必须置于 HTTPS 反向代理之后。先确认学校允许自动访问和第三方托管。

1. 以独立受限系统用户运行；建立仅该用户可访问的档案目录（权限 `0700`），不要复用正在运行本地 Skill 的档案。
2. 在该目录的 `config.json` 设置 `schema_version: 3`、明确的 `canvas_host`（完整 HTTPS origin，如 `https://canvas.example.edu`）、课程所在地 `course_tz`、显示用 `user_tz`（IANA 时区），以及 `courses: []` 和 `service: {"telegram_user_id": 123456789}`。域名与数字 ID 均由管理员替换成实际绑定；首次刷新按既有规则自动发现课程。不要填入任何 token。
3. 在仓库和档案之外，用受限编辑器创建秘密 JSON 文件，权限必须为 `0600`，不能是符号链接。字段为 `canvas_origin`（与配置的规范化 origin 完全一致）、`canvas_token`、`telegram_bot_token`。秘密值只写入该文件，不放命令参数、示例、聊天或日志。更换 Canvas 站点必须同步重新绑定秘密；更换 Bot 或授权身份后重启服务。令牌轮换也应重启。
   如需可选 AI，管理员在 `service` 中设置 `ai_daily_limit`（非负整数，默认 0），并在同一受限秘密文件中同时设置 `ai_origin`（OpenAI-compatible HTTPS origin）、`ai_api_key`、`ai_model`；缺任一项均不能调用模型。凭据和模型名称不要放在普通配置或聊天中。额度日时区在首次建立服务档案时固定为当时 `service.daily_timezone`（默认 UTC），学习者之后调整扫描时区不会重置额度。供应商可能对失败请求收费，失败仍占用本次额度。
4. 授权学习者先在 Telegram 打开专属 Bot 私聊。Bot 不得配置 webhook；已有 webhook 时启动会拒绝，不会自动删除。
5. 用实际绝对路径启动，前台运行可观察安全错误提示：

   ```bash
   python3 -B tools/cc_server.py --home /srv/canvas-account/data --secrets /srv/canvas-account-private/service.json
   ```

容器部署使用 `Dockerfile` 和 `compose.yaml`：同一镜像对应独立账号进程、数据目录、秘密文件和 Docker 网络。宿主数据目录归属 UID/GID `10001:10001`、权限 `0700`；秘密文件同属该用户、权限 `0600`。先创建各账号配置与秘密文件，不让 Docker 自动创建缺失路径。设置 `ACCOUNT_A_DATA`、`ACCOUNT_A_SECRET`、`ACCOUNT_B_DATA`、`ACCOUNT_B_SECRET` 为四条宿主绝对路径，运行 `docker compose config --quiet`，再 `docker compose build && docker compose up -d`。仅部署 A 时使用 `docker compose up -d account_a`。默认网站端口仅绑定宿主 `127.0.0.1:18081` / `18082`，可用 `ACCOUNT_A_WEB_PORT` / `ACCOUNT_B_WEB_PORT` 覆盖；统一网站的 HTTPS 反向代理转发到下述网关，再由网关选择账号后台，保留原始 Host。容器以 UID 10001、只读根文件系统、全能力移除、禁止提权运行，仅挂载自身数据和只读秘密，不挂载 Docker socket。构建上下文仅放行 Python 源码、网站 JS 和依赖清单。启动成功不等于真实 Canvas、浏览器及 Telegram 验收通过。

### 服务器账号管理向导

在运行 Docker 与 HTTPS 反向代理的 Linux 服务器上，管理员可重复执行 `sudo tools/account-wizard.sh`，选择新增账号、更新 Canvas Token、重设网页密码或查看状态。甲骨文部署机已安装快捷命令 `sudo jiujiastudy-admin`，程序位于 `/opt/jiujiastudy-admin`，无需进入仓库目录。脚本要求交互式终端；Token 与密码隐藏输入，经标准输入传给管理程序，不进入命令参数、环境变量或日志。仅标准部署的 UID/GID 10001、单账号独立容器可由向导管理；已有账号不被覆盖。若机器上多个运行中的应用镜像版本不同，新增时显式传入 `--image <已构建镜像名>`。

所有学习者使用同一个 HTTPS 网站，以各自用户名和密码登录。管理员先部署统一网关，并在 `/srv/jiujiastudy/gateway/registry.json` 维护网站 origin 与账号目录；每项包含唯一 `username`、`account_id`、宿主回环 `port`。目录不保存密码或 Canvas Token。网关只读挂载目录，使用独立受限签名密钥绑定浏览器会话与后台账号；各后台继续校验自己的密码、会话和 CSRF。后台不对公网暴露，各账号数据、凭据、额度和扫描保持独立。

当前统一入口为 `https://study.qiyi71w.com`。生产网关容器 `jiujiastudy-gateway` 使用 host 网络，但只监听 `127.0.0.1:18079`；OpenResty 将该网站转发到此端口。原账号后台仍监听 `127.0.0.1:18081`。网关启动命令为 `python -B /app/tools/cc_gateway.py --registry /registry/registry.json --key /run/secrets/gateway.key --listen 127.0.0.1 --port 18079`；只读挂载整个注册表目录到 `/registry`，确保原子更新立即可见。注册表由 root 持有、权限 `0644`；密钥至少32个随机字节，宿主路径 `/srv/jiujiastudy-private/gateway/gateway.key`，UID/GID `10001:10001`、权限 `0600`，单独只读挂载。网关不挂载学习档案、Canvas 秘密或 Docker socket。

新增向导只收集账号名称、Canvas 网址、Token、网页用户名、密码和时区，可选复用已有账号的 AI 供应商配置。无需逐用户填写域名或证书邮箱，也不再为每人申请证书。管理员只维护统一网站的 DNS、HTTPS 和反向代理。向导只读验证 Canvas 身份，拒绝重复用户名或身份；后台初始化并通过检查后加入统一目录，确认网站登录到正确账号后才启用扫描。每日 AI 上限 20 次，AI 主开关与公告授权默认关闭；未配置供应商时不可调用模型，复用供应商会共享供应商账单，不共享学习数据或授权。

更新 Token 时核对新 Token 属于原 Canvas 身份；密码重设撤销原账号旧会话。两项操作仅作用于统一目录登记的账号，状态菜单不显示凭据。开户失败撤回本次目录记录及账号资源，不改其他账号。切换统一入口后原网页会话需要重新登录，用户名、密码和学习记录保留；网站不提供公开注册。

镜像构建先升级到 `pip>=26.2`，再安装服务依赖，以包含已知安装与解包漏洞的修复。

网站配置：在 `config.json` 的 `service.web_origin` 填实际 HTTPS origin（例如 `https://study.example.com`，不含路径）。首次启动自动生成并持久保存 `service.account_id`；管理员读取该值后，以同一档案用户运行 `python3 tools/cc_web_auth.py --home /srv/canvas-account/data --account-id <实际标识> --username <用户名>`，在隐藏提示中输入至少十二字符的密码两次。不要把密码放入聊天或命令参数。容器中可用 `docker compose exec account_a python /app/tools/cc_web_auth.py --home /data --account-id <实际标识> --username <用户名>`。重新配置或修改密码会撤销全部会话。`web-auth.json` 权限为 `0600`，保存 Argon2id 哈希、散列会话索引与限流记录；会话最长十二小时。未配置凭据时网站不能登录，不存在默认密码。仅网站模式应同时省略 `service.telegram_user_id` 和秘密中的 `telegram_bot_token`。

连续失败登录会触发串行验证与五秒退避，不会将正确凭据锁定五分钟；验证忙时新登录请求快速拒绝，等待不占用会话存储锁，已有会话仍可读取或退出。修改密码使用独立的会话限流。公网反向代理仍应配置来源限速，不要信任客户端自行提供的转发地址。退出只有在服务器确认撤销或会话已失效后才显示成功；网络失败会隐藏内容并保存待退出标记，刷新时先重试撤销，不自动恢复账号页面。本地存储不可用时会明确提示，不能保证刷新后保留待退出意图。

网站提供今日、作业、公告、设置四页。完成任务会同步停止提醒，但仍单独展示 Canvas 是否已提交；可恢复待办或独立停止/恢复提醒。课程、AI、公告采集、通知和扫描时区设置与 Telegram 共用服务器状态，过期按钮或版本冲突要求刷新。公告采集独立于 AI：网站展示最近六十天记录，Canvas 已读或本地已读的公告默认不会发送给模型，本地已读不写回 Canvas，正文变化后需重新阅读。每次默认模型输入最多五十条未读公告。关闭 Telegram 自动通知仍继续计划扫描、网站日报和已授权 AI；手动 Bot 命令仍回复，重新开启不补发关闭期间日报。

今日页优先显示“今天先做这一件”和紧凑的“本周重点”；规则日报按类别折叠，保留计数、Canvas 来源及完整原文。AI 额度显示每日已用次数、上限和按用户显示时区换算的重置时间，修改显示时区不会重置额度。

今日重点区域在桌面和手机均采用上下布局：“今天先做这一件”在上，“本周重点”列表在下，统一左边线；今日任务正文限制行宽，以大标题和主按钮突出。辅助安排入口始终单列纵向排列，展开收起保持相同列宽与顺序，撞期提醒统一行距并限制正文行宽。

已保存的公告解读不会因公告变为已读而删除：当前有效未读解读正常展示，已读、旧版和不在当前快照中的解读收进折叠存档。默认分析不重新发送已读公告；用户对单条公告的明确授权仍独立处理。待确认公告行动可在核对当前原文后直接选择“已处理完毕”，也可加入计划或搁置；已完成行动可重新打开。完成只记录本站行动状态，不改变公告已读状态或 Canvas 数据；公告版本变化后必须重新核对，不在当前快照中的行动不能直接完成。

公告内容版本使用账号级持久递增序号，采集关闭或课程移除后重新出现也不会复用旧版本；旧页面的单条授权必须重新确认。停止或重新加入监控课程会使在途 AI 授权失效：未发送请求停止，已发送请求的结果不再保存；已发往供应商的数据无法撤回。

重启用 `docker compose restart account_a`，随后在该账号 `/canvas status` 核对原计划时间、快照和设置；投递失败仅重试未送达日报，不重新采集。轮换 Canvas token、Bot token 或模型密钥时，在对应账号的仓库外秘密文件中原子替换并保持 `0600`、UID/GID 10001，再重启该账号；更换 Canvas origin 时同步修改对应档案的 `canvas_host` 并重新取得该站点专用 token。更换绑定用户或 Bot 时先暂停旧接收身份的待投递私人内容，再更新配置/秘密并重启，逐一核对新的授权私聊；不要把旧身份的待投递问答转发给新身份。容器 `mem_limit: 256m`、`cpus: 1.0` 和 `pids_limit: 128` 是可调整的初始上限，不是经过容量验收的保证；部署时分别测量空闲和采集时 `docker compose stats --no-stream account_a account_b`，按真实负载调整。Telegram 和模型供应商自身的消息/请求留存不受本地清理控制。

支持 `/canvas help`、`/canvas status`、`/canvas report`（已有快照）、`/canvas refresh`（立即采集）、`/canvas on` / `/canvas off`（账号服务开关）、`/canvas courses`（监控课程按钮）、`/canvas tasks`（任务按钮）及 `/canvas schedule HH:MM Area/City`（每日基准时间和 IANA 显示时区）；`/canvas tasks stopped` 查看已停止项。也可直接使用 `/help`、`/on`、`/off` 等命令。help 和 status 都列出设置入口；status 显示服务开关、基准时间、显示时区、实际下次扫描、最近采集完整性以及最近日报的 Telegram 投递状态，不访问 Canvas。默认基准时间为 09:00 UTC，初次设置后延迟在 0–15 分钟内抽取。重叠刷新复用进行中的采集，结束后的新请求立即允许，仍遵守 Canvas 服务端限流。未授权私聊和群聊静默忽略。
`/canvas ai [问题]` 在服务开启且用户打开 AI 后即时完整采集，再发送最小任务信息到配置模型。`/canvas settings` 可分别设置 AI 主开关、每日摘要、公告解读授权、公告采集及 Telegram 自动通知；前三项默认关闭。授权公告分析会同时开启公告采集，关闭采集会撤销分析授权。采集公告供本人查看不代表允许发送给模型。模型段落不替代规则日报、Canvas 截止日期和提交证据，公告日期标为待确认。部分采集失败、模型错误/超时、额度不足时仍提供带失败范围的规则日报。自动摘要仅使用当次完整计划扫描，发送失败重用结果，不再次采集或调用模型。模型无工具、联网或多轮历史；供应商留存取决于其政策。
AI 返回经过校验的结构化中文结果：学习概况、逐条公告分析和下一步建议。课程名、作业名及专有名词保留原文；公告 ID 必须与输入一一对应，标题和来源链接由后端绑定，不接受模型生成的 URL。每条公告紧跟自己的来源，缺少来源时明确说明。

Bot 的截止时间、采集时间、改期通知、今日扫描基准及下次扫描统一按 `user_tz` 展示，并标注时区；原始快照仍保存带偏移的时间。`/canvas schedule HH:MM Area/City` 同时更新计划时区与 `user_tz`。仅调整显示不会改变已有扫描时刻或管理员额度日：额度页将实际额度日的起止边界换算为用户时区显示。AI 设置正文表示当前状态，按钮以「开启」或「关闭」明确表示点击动作。

启动时从同一命令目录注册绑定用户私聊的原生命令菜单，包括 `/help`、`/status`、`/report`、`/refresh`、`/ai`、`/settings`、`/on`、`/off`、`/courses`、`/tasks`、`/schedule`。菜单不改变命令鉴权，不向其他私聊或群聊注册。

`service-report.json` 原子保存当前及上一成功快照、逐课数据时间、最近采集完整性、待通知变化、每个计划日的随机时刻及 Telegram 投递状态。部分失败保留成功课的更新与失败课的旧数据时间；全部失败不推进任务快照，但形成明确标注失败的定时日报。日报包含未来七天待交、逾期未交、日期不明任务、新增改期、最近已提交及新课程，保留来源链接和逐课采集时间。没有待交任务且采集完整时仍给出确认。

Bot 任务列表和网站待办不展示 Canvas 已提交或豁免任务；网站「全部」可查看其原始状态。提交状态未知、待交和明确要求重新提交的任务仍可管理。日报截止时间后显示真实剩余或逾期时长；日期不明不推算倒计时。`/canvas report` 使用现有快照并按查看时刻重算，已形成的定时日报重试沿用原正文及计算时间。最近已提交仍作为一次性变化通知保留。

每门课首次成功采集作为任务基线。定时日报形成后固定规则正文；Telegram 每段成功回执立即持久记录，投递故障只重试尚未确认的分段，不重扫 Canvas 或调用模型；最多重试四次，超时回执可能造成重复，不承诺严格只送一次。用户撤销 AI 摘要授权导致待投递文本变化时重新分段，仅发送当前允许的正文。手动查看确认不消费未来定时日报的变化记录。过期计划不补发多日历史；保留执行事实与未送达变化，已完成的旧日报正文可清理。课程首次采集、设置变化和手动刷新不使已扫描的计划日再次采集。
`/canvas off` 先保存关闭状态和失效代际，再确认；Bot 继续响应 help、status、标明数据时间的旧 report、任务停止/恢复及扫描设置，但不再采集或投递日报。已发出的请求和消息不能撤回；慢请求返回后不继续分页、提交快照或发送旧结果。`/canvas on` 从关闭状态立即采集；若当天未形成有效计划日报，则这次采集形成并投递当日日报；当天已形成则仅向发起私聊回复本次刷新，另外恢复原日报未成功的 Telegram 投递，不重复已成功的投递。重复 on/off 不触发采集。关闭期间不追发多日历史。

提交状态以 Canvas 证据为准：已提交未评分、待审阅和豁免不催交；仅有分数不代表已提交，线下及无法确认的状态标记需确认。个人延期使用 Canvas 为请求用户返回的 `due_at`；仅改期不使已提交任务恢复催交，明确的 `redo_request` 才表示要求重新提交。学习完成记录和本地备注不替代提交状态。正文按 Telegram 长度分段，以纯文本呈现，不解析课程文字中的 HTML 或 Markdown；清理提及及控制字符，关闭链接预览，保留来源 URL。

`/canvas tasks` 每页显示最多八项，按钮可翻页或查看已停止项。先点任务的「停止」/「恢复」，再点确认；「取消」不修改提醒。停止时可不填原因，也可选择「线下完成并停止」；已停止列表显示原因。提醒决定只影响催交，不写 Canvas、不授予学校豁免；重启仍保留。成功刷新检测到适用截止时间变化或 Canvas 明确 `redo_request` 时解除停止并通知；已提交/豁免始终优先过滤，单纯评分或批语不会解除停止。按钮仅在绑定的授权私聊有效，旧按钮、重复点击及任务版本变化后须重新打开列表。
`/canvas courses` 保留自动发现的课程和主动停止的决定：停课后不再拉取其作业，也不会因下一次发现自动加回；重新加入在服务开启时立即采集，关闭时仅保存选择。课程明确失去访问或从成功的在读列表消失时旧任务移出催交区、保留历史并提示确认；作业接口的临时 5xx 仅标记数据过期，不当作退课。

服务默认只访问身份验证、课程清单及作业/本人提交记录；开启公告采集后读取公告，不读站内信、不下载课件、不写 Canvas。AI 默认关闭并须管理员配置；HTTPS 分页和模型请求只访问绑定 origin，拒绝 HTTP 重定向。真实供应商兼容性需在有明确付费授权时验证；本地受控 API 仅证明过滤、额度和错误回退。真实学校特殊提交语义及 Telegram 多账号容器综合验收见任务 09。

托管服务限制 Canvas 单页响应为 4 MiB、一次分页查询累计为 16 MiB，并对分页及重试共用 120 秒处理期限；模型响应上限为 1 MiB，流式与 JSON 响应均检查 120 秒期限及授权撤销。读取按块检查，单次阻塞读取还受不超过 30 秒的 socket 超时约束。超限或超时按采集／分析失败处理，不保存未经完整校验的结果。

离线验证：`python3 -B -m unittest discover -s tests -p test_account_service.py -v`。安全场景使用临时证书的 HTTPS MockCanvas（需要 OpenSSL、POSIX），Telegram API 边界模拟不等于真实平台验收；部署前须用真实测试 Bot 验证授权/未授权身份、群聊拒绝、消息分段和重启。

完整回归先安装 `requirements.txt`，再运行 `python3 -B -m unittest discover -s tests -v`；CI 在依赖安装完成后执行离线测试。账号管理和网关权限场景要求 POSIX；网站及周计划的 HTTPS 集成场景还要求 OpenSSL，缺少这些前提的平台会明确跳过相应场景。本地 Skill 的跨平台测试继续执行。

## 写作业这件事

这个工具会帮你写草稿、讲稿、提纲、给老师的消息，也会按你的要求上传文件。**交什么、交不交，由你决定**：请自己遵守你所在学校和那门课对 AI 使用的规定，需要声明的地方自己声明。工具不会替你判断，也不会替你点提交。它不做任何「降低 AI 痕迹」之类的事。

## 卸载

1. 删掉技能文件夹（`~/.claude/skills/jiujiastudy` 或 `~/.agents/skills/jiujiastudy`）。
2. 删掉桌面的「救驾」文件夹和 `~/.config/jiujiastudy`（Windows：`C:\Users\你的用户名\.config\jiujiastudy`）。
3. 去 Canvas → Account → Settings → Approved Integrations 撤销那个 token。

## 反馈

问题和建议发到 [Issues](https://github.com/jiujiastudy/jiujiastudy/issues)，或在小红书、抖音私信 @悉尼苏丹（控制canvas版）。

## 依赖

只用 Python 标准库就能运行本地 Skill；账号网站依赖 `requirements.txt`。其他可选功能按需安装第三方包，清单及许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 许可

MIT，见 [LICENSE](LICENSE)，不提供任何担保。与 Instructure 无关，未获其背书；Canvas 是 Instructure, Inc. 的商标。使用前请自行确认它符合你所在学校的规定。

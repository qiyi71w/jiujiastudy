# 救驾

给用第二语言上课的留学生的 Canvas 学习手帐：盯 deadline、排本周该学什么、按完成情况给下一步。项目提供可部署在自己服务器上的网站版本，以及装在自己电脑上的 Agent Skill。

[![tests](https://github.com/jiujiastudy/jiujiastudy/actions/workflows/ci.yml/badge.svg)](https://github.com/jiujiastudy/jiujiastudy/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](#要求)

[服务器版](#服务器版) · [本地 Skill](#本地-skill) · [自行部署](#自行部署) · [隐私与数据](#隐私与数据) · [测试](#测试)

## 服务器版

在自己的服务器上部署网站，通过浏览器查看任务、周计划和公告，也可绑定 Telegram 接收提醒。部署方法见[自行部署](#自行部署)。部署完成后，使用部署者提供的网址和管理员创建的用户名、密码登录；每个账号有独立的 Canvas 凭据、学习记录、扫描计划和 AI 额度。

- **今日**：看今天先做的一件事、本周重点和七日安排。固定日期、第一步调整和完成记录会保留。
- **作业**：查看截止时间、Canvas 提交状态，记录自己做完了什么，或停止某项提醒。
- **公告**：查看最近六十天的公告、标记已读；AI 提出的行动项由你核对后加入计划。
- **设置**：选择监控课程、扫描时间和时区，管理 AI 与 Telegram 通知授权。

> [!IMPORTANT]
> 服务器版的“已完成”是学习记录，不是 Canvas 提交记录。网站只读 Canvas；交作业仍要到学校网站操作。

AI 需要管理员配置供应商，再由你开启。公告解读另行授权；默认不把已读公告送给模型。规则日报和周计划不依赖 AI，额度用完后仍可查看。

如果账号绑定了专属 Telegram Bot，可在本人私聊中使用 `/canvas help` 查看命令，`/canvas report` 读取已有快照，`/canvas refresh` 主动采集。网站和 Bot 共用设置；关闭 Telegram 自动通知不会停掉网站的每日扫描。

## 本地 Skill

适合想把课件和学习资料放在自己电脑上的人。本地档案与服务器版分开，不会自动同步。

### 它做什么

- **Deadline 雷达**：每门课下一件要交什么、最急的一件、几件撞在一起的、Canvas 上没写日期的，一页看完。
- **本周清单**：按模块和 deadline 排出每门课这周要看的、要做的，每天一件必做。
- **状态和建议**：按打勾情况和你说的话判断正常、落后、过载，给一句具体建议。
- **课件在后台下**：Canvas 原件自动下到每门课的文件夹，从不让你等。
- **发到 Canvas 之前先过你的手**：发帖、发站内信、交作业都先给预览，再弹系统窗口由你本人点确定。

### 要求

- Python 3.10 或更新。没有的话 AI 会先问你要不要装。
- 一个能读 Agent Skills、能跑命令的 AI 助手：Claude Code、Codex 都行。
- Windows、macOS、Linux 都行。

### 安装

跟你的 AI 说一句（两种说法都行）：

> 帮我安装 救驾：https://github.com/jiujiastudy/jiujiastudy

> 帮我安装 GitHub 上的 jiujiastudy/jiujiastudy

两句都带了仓库地址（`jiujiastudy/jiujiastudy` 就是地址）。光说「救驾」或「jiujia」不行：模型不认识新仓库，可能装到别人的同名仓库，而这个技能会拿到你的 Canvas token。请只从上面这个地址安装。

安装和第一次运行时 AI 会请你点「允许」。用 Claude Code 自动模式（Auto）被拦下「刚下载的代码」的话，把输入框旁的权限模式换成每次询问，再说「继续」。救驾不改任何权限设置。

**装好不用重启**，直接跟 AI 说「最近要交什么」。

#### 手动安装

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

### 第一次使用

跟 AI 说「最近要交什么」，它会：

1. 用你电脑上已有的 Python。
2. 从浏览器记录里认出你学校的 Canvas 网址（**不会带 token 去试**），让你确认是哪个学校。
3. 你去 Canvas → Account → Settings → Approved Integrations → New Access Token 生成一个 token，直接发给 AI，它替你存好。
4. 采集，生成桌面文件夹、本周清单和 Deadline 雷达。

token 只要给一次：它存在你电脑上的 `~/.config/jiujiastudy/token`（Windows 是 `C:\Users\你的用户名\.config\jiujiastudy\token`，只有你本人的账户能读），以后每次直接用，只发给你确认过的那一个学校地址。要换就再发一个新的给 AI。token 也会留在你和 AI 的聊天记录里；介意的话，用完去 Canvas 撤销、重新生成。

平时这样说：「现在什么情况」「最近要交什么」「这周学什么」「做完了」「没状态」，或者直接说你想做的事：导读课件、做复习包、写东西、发帖、交作业。

### 配置

都是跟 AI 说一句它就改；想自己改，命令在下面。

| 想改什么 | 默认 | 命令 |
|---|---|---|
| 课件原件自动下载到你的文件夹 | 开 | `config set materials.auto_download false` |
| **把课件文字交给 AI 读**（导读、逐页精讲、复习包要用） | **关** | 按课打开：`config course <课程代码> --materials-ai on` |
| 显示时间跟着哪个时区 | 跟着电脑 | `config set user_tz Asia/Shanghai`，`auto` 恢复跟着电脑 |
| 这周是学期第几周 | 按模块名推断 | 跟 AI 说「这周是第 N 周」 |
| 资料放在哪个文件夹 | 桌面的「救驾」 | `config set root <路径>`，再让 AI 跑一次体检 |

本地采集读取作业、截止时间、模块、公告和站内信，不修改 Canvas。发帖、发信和交作业走单独的预览与确认流程。

课件文字默认不交给 AI。打开前，请先核对这门课对课程材料和生成式 AI 的规定。

## 隐私与数据

- 给人看的：桌面「救驾」文件夹——本周清单、Deadline 雷达，每门课一个文件夹（课件放 Canvas 原件，产出放 AI 做的东西）。
- 给程序用的：同一个文件夹里的隐藏目录 `.coach`（配置、进度、采集到的原始数据）。
- token：`~/.config/jiujiastudy/token`，只有你本人的账户能读。以前存在环境变量或钥匙串里的也认。
- 服务器版的档案和凭据保存在部署者的服务器上，管理员可以接触这些数据。绑定 Telegram 后，开启通知才会自动发送日报。
- 交给 AI 的内容会经过所配置的供应商。同一部署中的账号复用供应商配置时共用账单，学习数据和授权仍分开。
- 本地 Skill 发帖、发站内信、交作业：先给你看预览，你说「发」，再弹一个系统确认窗口，**你本人点确定**才真的发送。窗口弹不出来就不发，把链接给你自己交。

## 自行部署

账号服务使用 Linux/POSIX、Python 3.10+ 和 `requirements.txt` 中的依赖。Docker 配置见 [Dockerfile](Dockerfile) 和 [compose.yaml](compose.yaml)：每个账号运行独立容器，只挂载自己的档案和秘密文件，后台端口仅绑定宿主回环地址。

统一网站的请求先经过 HTTPS 反向代理和账号网关，再进入对应后台。首次部署要准备账号配置、受限秘密文件、网页登录凭据、网关注册表和签名密钥；Compose 本身不会代办这些步骤。具体字段、权限、启动命令和维护方法见 [部署与运维参考](SERVER_PLAN.md#部署与运维参考)。

运行账号管理向导，首次输入自己的 HTTPS 网站地址并确认，程序会创建注册表和空账号目录：

```bash
sudo tools/account-wizard.sh --registry /srv/jiujiastudy/gateway/registry.json --image <已构建的本地应用镜像>
```

首次配置完成后，按[部署与运维参考](SERVER_PLAN.md#服务器账号管理向导)配置 DNS、HTTPS 反向代理和网关，让网关读取同一份注册表；再运行上述命令开户。已有配置时，向导先显示网站地址并请求确认，再提供新增账号、更新 Canvas Token、重设网页密码和状态查询。初始化不会覆盖已有文件或账号目录。

`--registry` 可指定其他绝对路径，省略时使用上面的默认路径。已有唯一运行中的账号镜像时可省略 `--image`。网站地址只在首次配置时填写。

> [!WARNING]
> Canvas Token 和模型密钥只放在受限秘密文件中，或通过向导的隐藏输入传入。不要提交到 Git，也不要放进命令参数或日志。托管前先确认学校允许自动访问和第三方存储。

## 测试

在仓库根目录安装服务依赖，然后运行离线回归：

```bash
python3 -m pip install -r requirements.txt
python3 -B -m unittest discover -s tests -v
```

测试使用临时档案、模拟 Canvas 和受控模型端点。权限场景要求 POSIX，网站与周计划的 HTTPS 场景还要求 OpenSSL；缺少前提的平台会跳过这些场景。本地 Skill 的跨平台测试继续执行。测试布局见 [tests/README.md](tests/README.md)。

功能约定和部署验收记录见 [SERVER_PLAN.md](SERVER_PLAN.md)，领域用语见 [CONTEXT.md](CONTEXT.md)。离线测试不代替真实 Telegram 投递和模型供应商验收。

## 写作业这件事

本地 Skill 可以帮你写草稿、讲稿、提纲和给老师的消息，也可以按确认流程上传文件。**交什么、交不交，由你决定**：请遵守每门课对 AI 使用的规定，需要声明的地方自己声明。

## 卸载本地 Skill

1. 删掉技能文件夹（`~/.claude/skills/jiujiastudy` 或 `~/.agents/skills/jiujiastudy`）。
2. 删掉桌面的「救驾」文件夹和 `~/.config/jiujiastudy`（Windows：`C:\Users\你的用户名\.config\jiujiastudy`）。
3. 去 Canvas → Account → Settings → Approved Integrations 撤销那个 token。

## 反馈

问题和建议发到 [Issues](https://github.com/jiujiastudy/jiujiastudy/issues)，或在小红书、抖音私信 @悉尼苏丹（控制canvas版）。

## 依赖

只用 Python 标准库就能运行本地 Skill；账号网站依赖 `requirements.txt`。其他可选功能按需安装第三方包，清单及许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。


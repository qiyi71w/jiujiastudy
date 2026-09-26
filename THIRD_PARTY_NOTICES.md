# 第三方组件

救驾源码按 MIT 发布（见 `LICENSE`），不内嵌第三方库源码。本地 Skill 的基础 deadline 与本周清单仅依赖标准库；网站服务依赖 `requirements.txt`，构建容器时从 PyPI 安装，各组件遵循各自许可。

| 包 | 什么时候才需要 | 上游许可 |
|---|---|---|
| `pypdf` | 某门课打开了「课件文字给 AI 读」，且要读 PDF | BSD-3-Clause |
| `python-pptx` | 同上，要读 .pptx | MIT |
| `python-docx` | 同上，要读 .docx | MIT |
| `tzlocal` | 认电脑时区（认不出就退回按课程时区显示） | MIT |
| `tzdata` | 这个 Python 自带的时区库不全时 | Apache-2.0 |
| `Flask` | 网站路由、请求与响应 | BSD-3-Clause |
| `waitress` | 网站服务运行时 | ZPL 2.1 |
| `argon2-cffi` | 网站密码哈希 | MIT |

不装这些包，本地 Skill 的 deadline 和本周清单仍照常工作；账号网站需要上述三项网站依赖及其传递依赖。

前三个（取字用的）`doctor` **不会**自动装：课件文字提取默认关着，
你给某门课打开（`config course <课程代码> --materials-ai on`）之后要读什么格式，再装什么。

## 为什么不用 PyMuPDF

PyMuPDF 取字更好，但它是 AGPL：一个按 MIT 发布的项目不该主动把用户引去装 AGPL 的库。
所以 PDF 取字用纯 Python 的 pypdf。

许可信息以各包在 PyPI 上的声明为准；上面这张表只是方便查阅。

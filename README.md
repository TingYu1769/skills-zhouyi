<h1 align="center">周译 · Zhouyi</h1>

<p align="center">
  英文论文全文译中文的 agent skill：<br>
  有 arXiv LaTeX 源码时编出保留原版式的中文 PDF，没有源码时译成带原图的中文 Markdown。
</p>

<p align="center">
  <b>简体中文</b> &nbsp;·&nbsp; <a href="README.en.md">English</a>
</p>

<p align="center">
  <a href="LICENSE"><img alt="许可：MIT" src="https://img.shields.io/badge/license-MIT-blue"></a>
  <a href="https://agentskills.io"><img alt="Agent Skills 格式" src="https://img.shields.io/badge/format-Agent%20Skills-6f42c1"></a>
  <img alt="适用于 Claude Code、Codex 与其他 agent" src="https://img.shields.io/badge/works%20with-Claude%20Code%20%C2%B7%20Codex%20%C2%B7%20more-2ea44f">
</p>

<p align="center"><code>npx skills add TingYu1769/skills-zhouyi</code></p>

周译（`zhouyi`）是用于论文全文英译中的 [Agent Skill](https://agentskills.io)。“周”取周全之意：作者信息、正文、脚注和附录一并译出，公式、引用和编号与原文对应，参考文献保留英文。它面向能读写文件、执行命令和查看图片的 agent，例如 Claude Code 和 Codex。

> 用周译翻译 `papers/example.pdf`，输出到 `reading/`，简称 Example。

## 两种模式

| | 源码模式 | PDF 模式 |
| --- | --- | --- |
| 适用 | 能取得与 PDF 同版本的 arXiv LaTeX 源码 | 只有 PDF，或源码取不到、版本对不上、在本机编译不过 |
| 做法 | 按节切块翻译源码，公式、命令和引用换成占位符保护，用 XeLaTeX 重新编译 | 逐节对照 PDF 翻译，图表从原页裁剪并逐张查看 |
| 交付 | 保留原版式的中文 PDF，以及可再编译的中文 tex 工程 | 中文 Markdown、原 PDF 和 `assets/` 图片目录，可打包成 ZIP |

源码模式直接使用原论文的图片文件，表格和公式由 LaTeX 原样排版；表格单元和提示词框、代码框里的内容保持原文，图注和表注照常翻译。论文是 arXiv 预印本时，agent 按 PDF 首页的版本号下载同版本源码，取不到就改用 PDF 模式。

## 首次运行：确认环境

周译启动后先检查环境：确认文件读写、命令执行和图像查看能力，检查 Python 3.10+、PyMuPDF 与 Pillow，再用输入 PDF 渲染一页并实际打开查看，同时验证输出目录可写。检查脚本启动时只需 Python 标准库：

```bash
python3 skills/zhouyi/scripts/preflight.py
```

需要补齐依赖时，推荐用 [uv](https://docs.astral.sh/uv/) 准备隔离环境，并带上实际的输入与输出路径复查：

```bash
uv run --no-project --isolated --with 'pymupdf>=1.26,<2' --with 'pillow>=11,<13' \
  python skills/zhouyi/scripts/preflight.py papers/example.pdf --output-root reading/
```

源码模式另需 TeX Live（`xelatex`、`latexmk` 和 `ctex` 宏包）以及 Noto CJK SC 字体，楷体 AR PL UKai 可选。加 `--tex` 一并检查；TeX 不在 `PATH` 上时，用 `--texbin` 指定 TeX Live 的 `bin` 目录：

```bash
python3 skills/zhouyi/scripts/preflight.py --tex
```

缺少能力或依赖时，agent 会报告具体缺项和处理方式。Python 虚拟环境、Windows 路径、离线环境和 TeX 的准备方法见[环境准备说明](skills/zhouyi/references/environment.md)。

## 开场与默认

环境检查之后，周译先用几句话说明这一篇怎么做：环境与可用模式，走哪种模式及原因，可以改哪些选项，正文规模和预计耗时。同一输出目录第一次使用时说全，之后只报一行。

没说明的事按默认值办：能取得同版本源码就走源码模式，翻译全文含附录，有进度就续作，不用子代理，提示词框和表格单元保持原文，交付后清理中间产物。只在几种情况下停下来问：没给输出目录，源码模式走不通，需要安装依赖或联网，耗时超出估算的 1.5 倍，续作时发现别的会话刚改过同一篇。用户的选择交给宿主的记忆机制保存，下次沿用。详见[开场与默认](skills/zhouyi/references/onboarding.md)。

## 在不同宿主中使用

周译只需要文件读写、命令执行和看图三种能力。Claude Code、Codex、Cursor、Gemini CLI 在记忆、联网、看图和子代理上的差异，以及推荐的模型分工，见[在不同宿主中使用](skills/zhouyi/references/hosts.md)。花费请用宿主自己的命令查看，例如 Claude Code 的 `/usage`、Codex 的 `/status`。

## 安装

用 [skills CLI](https://github.com/vercel-labs/skills) 安装：

```bash
npx skills add TingYu1769/skills-zhouyi
```

也可以把 `skills/zhouyi/` 整个目录复制到所用工具的技能目录：

| 工具 | 个人技能目录 |
| --- | --- |
| Claude Code | `~/.claude/skills/zhouyi/` |
| Codex | `~/.codex/skills/zhouyi/` |

保留目录中的 `SKILL.md`、`references/`、`scripts/`、`assets/` 和 `agents/`。也可以直接让 agent 读取 [SKILL.md](skills/zhouyi/SKILL.md) 开始工作。

## 开始使用

提供论文 PDF 和输出目录即可；论文简称、出版信息和是否生成 ZIP 可一并指定。

```text
用 $zhouyi 翻译 papers/example.pdf，输出到 reading/，简称 Example。
```

```text
继续 reading/ 中 Example 的翻译，从保存的进度接着完成全文。
```

```text
用 $zhouyi 翻译 arXiv 1706.03762，输出到 reading/。
```

只给 arXiv 编号或链接时，周译自动下载 PDF 和同版本源码。

目录默认采用 `YYYYMM.Venue.ShortName` 命名，依次为首次公开年月、发表会议或期刊、论文简称，预印本使用 `arXiv`。命名信息优先从本地资料读取，必要时核对官方来源；输出根目录已有自己的命名约定时沿用该约定。

## 交付示例

PDF 模式：

```text
reading/
├── 202601.arXiv.Example/
│   ├── 202601.arXiv.Example.md
│   ├── 202601.arXiv.Example.pdf
│   ├── 202601.arXiv.Example.zh.pdf    # 有 pandoc 和 XeLaTeX 时生成
│   └── assets/
│       ├── fig_01.png
│       ├── table_01.png
│       └── algorithm_01.png
└── 202601.arXiv.Example.zip       # 按需生成
```

源码模式：

```text
reading/
└── 202601.arXiv.Example/
    ├── 202601.arXiv.Example.pdf       # 原 PDF
    ├── 202601.arXiv.Example.zh.pdf    # 中文 PDF
    └── zh-tex/                        # 可再编译的中文 tex 工程
```

PDF 保留输入文件的原始字节，Markdown 使用相对图片路径，整个目录可以移动、复制和分享。翻译进度、术语和核对记录保存在输出根目录的 `.zhouyi/` 中，供中断后继续使用。

## 翻译与核对

- **完整翻译。** 覆盖作者信息、摘要、正文、脚注、致谢与附录，参考文献保留英文。
- **保留学术结构。** 标题和章节中英对照，重要术语首次出现附英文；公式、引用、编号、变量和论证顺序与原文对应。
- **原页截图（PDF 模式）。** 图表直接从输入 PDF 渲染、裁剪。Agent 打开每张截图，对照原页检查图例、边缘标签、表格末行与脚注；跨页对象按顺序保存各部分。
- **可复制的算法与提示词（PDF 模式）。** 原图之外附中文文本，保留变量、占位符、协议标签及可执行代码。
- **合并检查与回退（源码模式）。** 合并前逐块检查占位符和花括号；某块译文导致编译失败时，这一块退回英文重编，该节重新标为待处理，修好后再合并。
- **只在两道关卡停下来判断。** 开译前看英文底稿与原 PDF 的比对结果，交付前看一张可疑页拼图；逐块核对提示、宏包标签的中文化、状态查看和清理由工具完成，省去逐页看图和自写核对脚本。
- **连续处理与恢复。** 按章节保存进度，继续任务时核对源文件和术语，从未完成的部分接着处理。

## 开发与验证

[技能入口](skills/zhouyi/SKILL.md) · [工具说明](skills/zhouyi/references/tools.md) · [源码模式](skills/zhouyi/references/latex-source.md) · [验收场景](evals/scenarios.md) · [验收记录](evals/results.md) · [更新记录](CHANGELOG.md)

运行功能测试：

```bash
uv run --with 'pymupdf>=1.26,<2' --with 'pillow>=11,<13' python -m unittest discover -s evals -p 'test_*.py' -v
```

其中 6 项会实际调用 XeLaTeX 编译：`latexmk` 在 `PATH` 上，或环境变量 `ZHOUYI_TEXBIN` 指向 TeX Live 的 `bin` 目录时运行，否则跳过。

提交问题时，请说明论文的版式或模板、处理位置、预期结果与实际结果；可复现的最小样例有助于定位问题。

## 许可证

[MIT](LICENSE)。输入论文及其截图的版权归原权利人。

# 更新记录

版本号按[语义化版本](https://semver.org/lang/zh-CN/)：补丁版本改措辞和说明，次版本增加功能或调整工作流程、但不改变已有交付物和工作记录的结构，主版本改变交付格式或工作记录的结构。版本号同时记在 `skills/zhouyi/SKILL.md` 的 `metadata.version` 里。

## 未发布

## 0.2.0（2026-10-28）

源码模式的改动：

- 切块识别论文自己定义的 verbatim 类环境（`\lstnewenvironment`、`\newtcblisting`、`\DefineVerbatimEnvironment`、`\newminted` 等）并整段保留，verbatim 环境到第一个字面的 `\end` 为止，`\verb`、`\lstinline` 等的参数也整段保留。以前提示词框里的 `$(nproc)` 会被当成公式开头，吞掉大段正文。
- `$` 到下一个空行前都没闭合时，切块报错并给出行号。新增 `tex-split --verbatim-env`，切块输出新增 `verbatim_envs`、`kept_prose` 和 `warnings`。
- 切块不在花括号组里切开，组内开始的节并入前一块；注释里的 `\begin`/`\end` 不再参与配对。以前 `\hypertarget{x}{\section{..}..}` 会切出两个不配平的块，一句注释掉的 `% \begin{tabular}` 会让切块报错。
- 新切的块，合并检查要求占位符外的花括号与原文块一样配平，花括号按反斜杠奇偶计数；已切块的工作目录沿用原规则。
- `tex-split` 记下 `W/src/` 各文件的哈希，切块后源码被改动时 `tex-merge` 报错。以前会按旧位置合并，悄悄吞掉或重复文字，编译照常通过。
- 逗号列表里的字体包（如 `\usepackage{iclr2027_conference,times}`）也会被替换，同一行的其他宏包照常加载。以前英文底稿会退回 Latin Modern。
- 报错行与源码原文一字不差且不含中文时，`tex-build` 停下交给 agent，不再退回它所在的块；新增 `tex-build --retry-reverted`，误判修好后重新合并退回的块。以前 override 在 `\begin{document}` 处出错，会把首块误退回英文。
- 主文件在子目录（如 `texFiles/arxiv.tex`）时，`tex-build` 能找到 PDF 和日志。
- 从 `W/overrides/` 删掉的文件，下次合并时恢复成源码原样；`tex-deliver` 把 override 里源码没有的文件也放进 `zh-tex/`。
- `tex-prepare` 新增 `--main`，源码包有多个顶层文档时指定主文件；找不到主文件时不留下解包目录。

## 0.1.0（2026-10-22）

首次公开发布。

- 两种输入模式：论文有与 PDF 同版本的 arXiv LaTeX 源码时，翻译源码并用 XeLaTeX 编出保留原版式的中文 PDF 和可再编译的中文 tex 工程；没有源码时，从本地 PDF 译成中文 Markdown，图表从原页截取。
- PDF 模式：全文英译中，覆盖作者信息、摘要、正文、脚注、致谢和附录，参考文献保留英文；图表按原页裁剪并逐张视觉复核，支持旋转页和跨页对象；算法和提示词框同时给出截图与可复制的中文文本；提供检查、渲染、裁剪、拼图、校验和 ZIP 打包工具。
- 源码模式：按节切块，超长的节在 `\subsection` 处再切；公式、命令和引用换成占位符保护，合并前检查占位符与括号；译文导致编译失败的块自动退回英文并重新标为待处理；手工修正放在 `W/overrides/`，每次合并后自动覆盖；中文用 Noto CJK，强调用楷体；替换 fontspec 下不可用的 Type 1 字体，包括类文件和样式文件里的写法；给 fontspec 传 `no-math`，保留模板自带的数学字体；交付前检查编译结果与复核记录。
- 环境检查：`preflight.py` 只用标准库启动，检查 Python、PyMuPDF 和 Pillow，加 `--tex` 另查 XeLaTeX、latexmk、ctex 与中文字体；首次运行用输入 PDF 实际渲染一页并打开预览。
- 工作记录：保存源文件身份、章节进度、术语、图片复核和问题记录，中断后可以续作。
- 控制上下文：一篇论文一个新会话，已写入的译文不整段回读，单个上下文累计看图不超过 30 张；默认由当前 agent 完成，用户要求时才用子代理。
- 中英文 README；`evals/` 下有功能测试（没有 TeX 时跳过 6 项编译测试）、行为场景和验收记录。

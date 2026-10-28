# LaTeX 源码模式

输入论文有与 PDF 同版本的 LaTeX 源码时（多数 arXiv 论文都有），直接翻译源码，用 XeLaTeX 编出中文 PDF。图保留原始矢量文件，表格和公式由 LaTeX 原样排版，不需要截图和逐张看图。交付物是中文 PDF 和可再编译的中文 tex 工程。

## 取得源码

arXiv 源码地址是 `https://arxiv.org/e-print/<编号>v<版本>`，返回 `.tar.gz`。版本以输入 PDF 首页的戳记为准（例如 `arXiv:1706.03762v7`），不要取最新版本，二者的正文可能不同。输出根目录另有原文登记工具时，用它下载并登记。

源码包只有 PDF、没有 `.tex`，或者版本对不上时，改用 PDF 模式。

## 环境

运行 `scripts/preflight.py --tex`（可加 `--texbin <TeX Live 的 bin 目录>`），确认有 `latexmk`、`xelatex`、`ctex.sty` 和 Noto Serif CJK SC。楷体 AR PL UKai 是可选项，用于中文强调；没有时退成倾斜的宋体。

以下命令中 `W` 是论文工作目录下的 `tex/`，例如 `reading/.zhouyi/202601.arXiv.Example/tex`。

## 流程

1. `zhouyi_tex.py tex-prepare <源码包> --work-dir W`：解包到 `W/src/`（原样保留）和 `W/build/`，从 `00README.json` 或 `\documentclass` 找主文件，按 `\input`/`\include` 列出全部 tex 文件。源码包里有不止一个顶层文档（例如另附 `rebuttal.tex`）时，用 `--main <文件名>` 指定主文件。
2. `zhouyi_tex.py tex-build --work-dir W`：先不翻译，直接编译一次。脚本在 `\documentclass` 之后插入 `zh-setup.tex`，并在 `\documentclass` 同一行给 fontspec 传 `no-math`，模板为 pdfLaTeX 准备的数学字体（如 libertinust1math）因此不会被换成文本字体。脚本还注释掉主文件的 `fontenc`、`inputenc`，以及主文件和源码里其他 .tex、.cls、.sty 中的 Type 1 字体行：`times`、`helvet`、`courier`、`mathptmx`、`tgtermes`、`tgheros`、`tgcursor`、`inconsolata`、`zlmtt`，以及把 `\rmdefault`、`\sfdefault`、`\ttdefault` 设成 ptm、phv、pcr 的写法，`\usepackage` 和 `\RequirePackage` 都算。这些字体包写在逗号列表里时（如 ICLR 模板的 `\usepackage{iclr2027_conference,times}`），只把字体包从列表中去掉，行尾记一句 `% zhouyi: dropped times`，同一行的其他宏包照常加载。被注释的字体族换成对应的 OpenType 字体（TeX Gyre、Inconsolata、Latin Modern Mono）；模板自己用 fontspec 能加载的西文字体（如 XCharter）保留原样。编译不过时先看日志：缺宏包（`File ... not found`）就补装（TeX Live 用 `tlmgr install`），其他问题修环境或源码，记在 `progress.json` 的 `metadata`。编译通过不等于字形正确，建议把英文底稿和原 PDF 逐页比对文字，确认公式符号、作者标记没有错成别的字形，再开始翻译。
3. `zhouyi_tex.py tex-split --work-dir W`：每个 `\section` 切成一块，正文前的标题部分和摘要各成一块，块不跨文件。正文超过 `--max-chars`（默认 14000 字符，约 3–4K token）的节在 `\subsection` 处再切，同一节内相邻小节合并到上限为止，块不跨节。切点不落在花括号组里：`\hypertarget{x}{\section{..} ..}` 这类组内的节并入前一块，输出的 `warnings` 会注明；文件里正文的花括号本身不配平时，退回在每节处切开并警告。块越大，来回写入的轮数越多，花费也越高。公式、命令、引用、标签、注释、表格体换成 `⟦Pn⟧` 占位符。verbatim 类环境整段保留，到第一个字面的 `\end{名}` 为止，里面的 `%` 不算注释：内置的 `verbatim`、`lstlisting`、`minted`、`tcblisting` 等，加上论文自己用 `\lstnewenvironment`、`\newtcblisting`、`\DefineVerbatimEnvironment`、`\newminted` 定义的，或开头代码里打开 Verbatim/lstlisting 的环境（自动识别，漏掉的用 `--verbatim-env 名1,名2` 声明）；`\verb`、`\lstinline`、`\mintinline`、`\path`、`\nolinkurl` 的参数也整段保留。一个 `$` 到下一个空行前都没闭合时，切块报错并给出行号，多半是遇到了没识别的 verbatim 环境。输出的 `verbatim_envs` 列出识别到的环境，`kept_prose` 列出整段保留、英文较多的 verbatim 块和表格（按块给出环境名和字母数），是否用 `W/overrides/` 补译由 agent 判断。输出在 `W/chunks/`：`<块>.src.tex` 是待译文本，`index.json` 列出各块。同时在 `W/progress.json` 里为每块登记一节。切块按 `W/src/` 当时的字符位置记录每块的起止，切块之后 `W/src/` 不能再改：`tex-merge` 发现文件变了会报错，修正一律放进 `W/overrides/`。
4. 翻译：默认由当前 agent 按 `index.json` 的顺序逐块翻译，读一块 `<块>.src.tex`、写一块 `<块>.zh.tex`，规则见下面的“块说明”和 `translation.md`；已写完的块不再整块回读，术语表随时更新。用户要求用子代理时，才把块交给不继承当前历史的子代理，只给它块说明、`translation.md`、术语表和 `.src.tex` 路径；附录按文件拆分的论文会切出很多小块，相邻小块合并成一批交给同一个子代理，每批正文约 12000 字符（按 `prose_chars` 累加），因为每开一个子代理都要重付一次系统提示和规范的开销。
5. `zhouyi_tex.py tex-merge --work-dir W`：逐块检查每个占位符恰好出现一次、没有新增占位符、占位符外的花括号与原文块一样配平（原文块本身就有没闭合的组时，译文留下同样多的未闭合组）、没有新增未转义的 `% & # _ $`，通过的块还原到 `W/build/`，再把 `W/overrides/` 覆盖上去；上次覆盖过、这次已从 `W/overrides/` 删掉的文件恢复成源码原样。不通过的块列在 `rejected` 里，修好后重跑。
6. `zhouyi_tex.py tex-build --work-dir W`：合并后编译。某块的译文导致编译失败时，脚本把这一块退回英文重编，该节改回 `pending`，并在 `issues` 里开一条记录；失败的译稿留在原处，修好后再编译会自动重新合并。报错落在译文之外、落在与源码原文一字不差且不含中文的行上（例如 overrides 或导言区的钩子在 `\begin{document}`、`\maketitle` 处出错），或者没有文件和行号时，脚本停下，交给 agent 处理。之前的退回若事后查明是误判，修好真正的原因后用 `tex-build --retry-reverted` 把退回的块重新合并，再把对应 issue 标为 resolved、各节重新核对。
7. 版面核对：用 `zhouyi.py render` 渲染全部页面，`zhouyi.py sheet --per 4` 拼图查看。逐张确认：没有缺字方框，标题、图表和表注位置正常，表格没有溢出栏宽，参考文献已生成，强调用的楷体正常显示。`build-summary.json` 里的 `missing_characters` 必须为 0，`undefined_references`、`overfull_over_20pt`（横向溢出）和 `overfull_vbox_over_20pt`（文本框、表格越过页底）逐条看过。
8. 核对只做一遍：每块写完后随即逐段对照原文核对（这时原文还在上下文里，标准同 [验收与恢复](verification.md)），发现问题当场改，之后不再整块重读原文和译文。全部合并编译后，只看 `tex-merge` 的结果、编译摘要和版面拼图，再抽查几段；没有问题时将各节设为 `verified`，疑点记入 `issues`，版面核对和抽查范围写进 `document_review`。用子代理时，逐段核对由子代理在本批完成并回报，主 agent 每批抽查一两段。不要在最后把全文再完整重读一遍：每轮请求都会重发上下文，整篇重读会让花费成倍增加，逐块写完即核对已经覆盖了这一步。
9. `zhouyi_tex.py tex-deliver <论文目录> --work-dir W` 复制 `<论文目录名>.zh.pdf` 和 `zh-tex/`，`zh-tex/` 含源码的全部文件、`W/overrides/` 中的文件（包括源码里没有的，如本地字体包替身）和 `zh-setup.tex`；`zhouyi_tex.py tex-check <论文目录> --work-dir W` 返回 `ready: true` 后交付。

## 块说明（翻译每一块时遵守；用子代理时原样交给它）

> 把 `<块>.src.tex` 译成中文，写到同目录的 `<块>.zh.tex`。只翻译自然语言，规则见 `translation.md` 的“内容覆盖”“术语与原始标记”。
>
> - `⟦Pn⟧` 是被保护的 LaTeX 片段，每个都原样保留一次，不改写、不删除、不新增；可以随中文语序在句内移动，但不要移到别的句子或段落里。
> - 保留所有 `{` `}`：它们是命令的参数边界，例如 `⟦P3⟧{Example}` 里的花括号。
> - 不新增 LaTeX 命令或宏。中文里出现百分号、`&`、`#`、`_`、`$` 时写成 `\%` `\&` `\#` `\_` `\$`。
> - 章节标题写成“中文（English）”，论文标题同样处理。表注、图注、脚注、算法里的文字都要翻译。
> - 数字、单位、模型和数据集名称照抄；不加原文没有的解释。
> - 段落之间的空行保持不变。
> - 写完后逐段对照原文自查一遍：段落、数字、限定语、否定、引用位置都要对上，发现问题直接改。
> - 完成后回报：核对范围，新出现的术语及译名，拿不准的地方及原因。

## 已知限制

- 表格单元保持英文（与 PDF 模式截图保留原表一致），表注、图注照常翻译。
- verbatim 类环境（提示词框、代码框）的内容保持原文：listing 里没有转义，中文还要另配 listing 的换行和字体，需要时用 `W/overrides/` 处理。
- 作者姓名和单位保持原文。
- 有几类可见文字不会进入待译块：环境可选参数里的文字（例如框标题 `title=...`）、被整体保护的宏参数里的图注（例如自定义宏里的 `\captionof`）、公式环境里的 `\intertext` 说明、模板类文件里写死的标签（例如直接写出的 `Abstract`，`zh-setup.tex` 改 `\abstractname` 管不到它）、作者字段里的说明（Equal contribution、Corresponding author、Project leader）、导言区宏里的文字（例如 `\papertitle`、算法的 Input/Output 标签、algorithmic 的控制词），以及 `\lstinputlisting` 等引入的外部文件（例如附录里的提示词全文）。开始翻译前先在 `W/src` 的主文件导言区、作者字段、类文件和外部文件里查一遍，随块翻译时一并写进 `W/overrides/`（类文件写死的标签可在主文件的 override 里用 `\patchcmd` 改），省得版面核对后再返工。
- 字体包的缩放选项（例如 `\RequirePackage[scaled=1.1]{zlmtt}`）不随字体替换保留，等宽字可能比原版略小；需要一致时在 override 里给对应的 `\setmonofont` 加 `Scale`。
- 手工修正放在 `W/overrides/`，按与 `W/build/` 相同的相对路径存放修改后的整个文件；每次合并之后，脚本都会把它们覆盖到 `W/build/` 上。直接改 `W/build/` 里的 tex 文件，下一次合并就会被覆盖。改动记进 `progress.json` 的 `metadata`。
- 被整体保护的环境（`tabular`、`verbatim`、公式环境等）里的脚注和说明文字不会被翻译，必要时在 `issues` 里记为 `noted`。
- 合并检查只看占位符和括号，不检查占位符顺序和段落数量；这两项靠逐块对照原文发现。
- 编译日志里的未定义引用不阻止交付，只作警告列出。

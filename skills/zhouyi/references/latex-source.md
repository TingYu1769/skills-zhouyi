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
2. `zhouyi_tex.py tex-build --work-dir W`：先不翻译，直接编译一次。脚本在 `\documentclass` 之后插入 `zh-setup.tex`，在同一行给 fontspec 传 `no-math`，模板为 pdfLaTeX 准备的数学字体（如 libertinust1math）因此不会被换成文本字体。脚本还注释掉主文件的 `fontenc`、`inputenc`，以及主文件和源码里其他 .tex、.cls、.sty 中 fontspec 下不能用的 Type 1 字体行：`times`、`helvet`、`courier`、`mathptmx`、`tgtermes`、`tgheros`、`tgcursor`、`inconsolata`、`zlmtt`，以及把 `\rmdefault`、`\sfdefault`、`\ttdefault` 设成 ptm、phv、pcr 的写法，`\usepackage` 和 `\RequirePackage` 都算；这些字体包写在逗号列表里时（如 `\usepackage{iclr2027_conference,times}`），只把它从列表中去掉。注释或去掉的字体族换成对应的 OpenType 字体（TeX Gyre、Inconsolata、Latin Modern Mono），模板自己用 fontspec 能加载的西文字体（如 XCharter）保留原样。`zh-setup.tex` 还把宏包自己印出的标签换成中文：算法浮动体名和 algorithmic/algpseudocode 的关键词、`\proofname`、目录和附录名、cleveref 的引用名；导言区 `\newtheorem` 里常见的显示名（Theorem、Lemma、Proposition 等）在编译副本里换成中文。模板类文件里直接写死的英文（如某些模板的 Abstract）不在此列。每一处改动都标 `% zhouyi:` 或列在 `build-summary.json` 的 `patches` 里。编译不过时先看日志：缺宏包（`File ... not found`）就补装（TeX Live 用 `tlmgr install`），其他问题修环境，或按[手工修正](#手工修正)处理，记进 `progress.json` 的 `metadata`。编译通过不等于字形正确：运行 `zhouyi.py compare <原 PDF> W/build/<主文件>.pdf`，看四项输出：页数、两边的正文主字体（例如原版是 Times 一类而底稿是 LMRoman，说明字体退回了默认值）、文字覆盖率低于阈值的页、只在底稿里出现的字符（常是错字形）。只打开它标出的页核对，没有标出的就可以开始翻译，不必逐页看图。
3. `zhouyi_tex.py tex-split --work-dir W`：把正文切成待译块，输出在 `W/chunks/`：`<块>.src.tex` 是待译文本，`index.json` 列出各块，`W/progress.json` 里为每块登记一节。
   - **切法。** 每个 `\section` 一块，正文前的标题部分和摘要各成一块。正文超过 `--max-chars`（默认 14000 字符，约 3–4K token）的节在 `\subsection` 处再切，同一节内相邻小节合并到上限为止。块不跨文件、不跨节，也不在花括号组里切开：`\hypertarget{x}{\section{..} ..}` 这类组内开始的节并入前一块。块越大，来回写入的轮数越多，花费也越高。
   - **保护。** 公式、命令、引用、标签、注释、表格体换成 `⟦Pn⟧` 占位符。verbatim 类环境整段保留，到第一个字面的 `\end{名}` 为止，里面的 `%` 不算注释。这类环境包括内置的 `verbatim`、`lstlisting`、`minted`、`tcblisting` 等，以及论文自己用 `\lstnewenvironment`、`\newtcblisting`、`\DefineVerbatimEnvironment`、`\newminted` 定义、或开头代码里打开 Verbatim/lstlisting 的环境；后者自动识别，漏掉的用 `--verbatim-env 名1,名2` 声明。`\verb`、`\lstinline`、`\mintinline`、`\path`、`\nolinkurl` 的参数也整段保留。
   - **报错。** 一个 `$` 到下一个空行前都没闭合时，切块停下并给出行号，多半是遇到了没识别的 verbatim 环境。
   - **要看的输出。** `verbatim_envs` 列出识别到的环境；`kept_prose` 列出整段保留、英文较多的 verbatim 块和表格，给出所在块、环境名和字母数，是否补译由 agent 判断；`warnings` 说明哪些节因花括号组并进了前一块，或者哪个文件的花括号本身不配平、退回了逐节切开。
   - 切块按 `W/src/` 当时的字符位置记录每块的起止，此后 `W/src/` 不能再改，见[手工修正](#手工修正)。
4. 翻译：默认由当前 agent 按 `index.json` 的顺序逐块翻译，读一块 `<块>.src.tex`、写一块 `<块>.zh.tex`，规则见下面的“块说明”和 `translation.md`；已写完的块不再整块回读，术语表随时更新。用户要求用子代理时，才把块交给不继承当前历史的子代理，只给它块说明、`translation.md`、术语表和 `.src.tex` 路径；附录按文件拆分的论文会切出很多小块，相邻小块合并成一批交给同一个子代理，每批正文约 12000 字符（按 `prose_chars` 累加），因为每开一个子代理都要重付一次系统提示和规范的开销。
5. `zhouyi_tex.py tex-merge --work-dir W`：逐块检查每个占位符恰好出现一次、没有新增占位符、没有新增未转义的 `% & # _ $`，以及占位符外的花括号与原文块一样配平（原文块本身留有未闭合的组时，译文留下同样多）。通过的块还原到 `W/build/`，再覆盖上 `W/overrides/`。不通过的块列在 `rejected` 里，修好后重跑。通过的块另有 `hints`：原文有、译文缺的数字，段落数不一致，译文里连续 8 个以上英文单词（全角括号里的英文原题不算）。提示只供核对，不拒收；照提示看一眼这一块即可，不必再自写核对脚本。
6. `zhouyi_tex.py tex-build --work-dir W`：合并后编译。某块的译文导致编译失败时，脚本把这一块退回英文重编，该节改回 `pending`，并在 `issues` 里开一条记录；失败的译稿留在原处，修好后再编译会自动重新合并。以下情况脚本不退回，停下交给 agent：报错落在译文之外；报错行与源码原文一字不差且不含中文（例如 override 或导言区的钩子在 `\begin{document}`、`\maketitle` 处出错）；日志里没有文件和行号。之前的退回事后查明是误判时，修好真正的原因，用 `tex-build --retry-reverted` 重新合并退回的块，再把对应 issue 标为 resolved、各节重新核对。
7. 版面核对：运行 `zhouyi.py review-sheet <中文 PDF> --output-dir W/tmp/review --log W/build/<主文件>.log`，它把首页和日志里有超宽溢出、缺字的页拼成一张图，只看这一张：没有缺字方框，标题、图表和表注位置正常，表格没有溢出栏宽，强调用的楷体正常显示。漏译已由 `tex-merge` 的提示在逐块阶段处理；只有图上看出版式问题，或 `build-summary.json` 有异常时，才用 `render` 和 `sheet` 看更多页。`build-summary.json` 里的 `missing_characters` 必须为 0，`undefined_references`、`overfull_over_20pt`（横向溢出）和 `overfull_vbox_over_20pt`（文本框、表格越过页底）逐条看过。
8. 核对只做一遍：每块写完后随即逐段对照原文核对（这时原文还在上下文里，标准同 [验收与恢复](verification.md)），发现问题当场改，之后不再整块重读原文和译文。全部合并编译后，只看 `tex-merge` 的结果、编译摘要和版面拼图，再抽查几段；没有问题时将各节设为 `verified`，疑点记入 `issues`，版面核对和抽查范围写进 `document_review`。用子代理时，逐段核对由子代理在本批完成并回报，主 agent 每批抽查一两段。不要在最后把全文再完整重读一遍：每轮请求都会重发上下文，整篇重读会让花费成倍增加，逐块写完即核对已经覆盖了这一步。
9. `zhouyi_tex.py tex-deliver <论文目录> --work-dir W` 复制 `<论文目录名>.zh.pdf` 和 `zh-tex/`（源码的全部文件、`W/overrides/` 里的文件和 `zh-setup.tex`）；`zhouyi_tex.py tex-check <论文目录> --work-dir W` 返回 `ready: true` 后交付。

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

## 手工修正

块里改不到的地方用 `W/overrides/` 修：按与 `W/build/` 相同的相对路径，放修改后的整个文件。每次合并之后，脚本把这些文件覆盖到 `W/build/` 上。从 `W/overrides/` 删掉的文件，下次合并时恢复成源码原样。交付时它们一并进入 `zh-tex/`，包括源码里没有的文件（如本地字体包替身）。改动记进 `progress.json` 的 `metadata`。

- 不改 `W/src/`：切块之后它被改动，`tex-merge` 会报错停下，因为块的起止位置已经对不上。也不直接改 `W/build/` 里的 tex 文件，下一次合并会把它覆盖掉。
- 尽量只覆盖需要改的小文件：类文件、宏包、导言区单独 `\input` 的宏文件和表格文件。不得不覆盖含有译块的 tex 文件时，用脚本从合并结果生成，并把脚本留在工作目录；之后改了译块，先删掉这份 override、重新合并，再运行脚本，否则旧副本会盖住新译文。
- 开始翻译前，先在主文件导言区、作者字段、类文件和外部文件里查一遍不会进入待译块的可见文字，随块翻译时一并写进 override，省得版面核对后再返工：环境可选参数里的文字（例如框标题 `title=...`）、被整体保护的宏参数里的图注（例如自定义宏里的 `\captionof`）、公式环境里的 `\intertext` 说明、模板类文件里写死的标签（例如直接写出的 `Abstract`，`zh-setup.tex` 改 `\abstractname` 管不到它，可在主文件的 override 里用 `\patchcmd` 改）、作者字段里的说明（Equal contribution、Corresponding author、Project leader）、导言区宏里的文字（例如 `\papertitle`、算法的 Input/Output 标签、algorithmic 的控制词），以及 `\lstinputlisting` 等引入的外部文件（例如附录里的提示词全文）。

## 已知限制

- 表格单元保持英文（与 PDF 模式截图保留原表一致），表注、图注照常翻译。
- verbatim 类环境（提示词框、代码框）的内容保持原文：listing 里没有转义，中文还要另配 listing 的换行和字体。需要翻译时按[手工修正](#手工修正)处理，`kept_prose` 列出了候选。
- 被整体保护的环境（`tabular`、公式环境等）里的脚注和说明文字不会被翻译，必要时在 `issues` 里记为 `noted`。
- 作者姓名和单位保持原文。
- 字体包的缩放选项（例如 `\RequirePackage[scaled=1.1]{zlmtt}`）不随字体替换保留，等宽字可能比原版略小；需要一致时在 override 里给对应的 `\setmonofont` 加 `Scale`。
- 合并检查只看占位符和括号，不检查占位符顺序和段落数量；这两项靠逐块对照原文发现。
- 编译日志里的未定义引用不阻止交付，只作警告列出。

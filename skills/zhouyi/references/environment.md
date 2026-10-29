# 首次运行与环境检查

每次开始新任务或更换运行环境时，先完成本页检查，再进入命名和全文处理。已有进度的任务也使用当前实际可用的解释器和工具。

同一输出根目录下，已有任务的 `progress.json` 记录过通过的环境检查（`environment` 字段写明解释器且预览已查看），所记解释器仍可用时，只用它重跑 `scripts/preflight.py`；通过即可开始，不必重读本页，也不必再次查看预览。检查失败或换了机器时，按本页完整处理。

## 1. 确认宿主能力和 Python

检查当前 agent 是否具有文件读写、命令执行和图像查看工具。通过实际工具调用确认能力；宿主名称本身不能证明这些工具已经启用。

查找 Python 3.10+ 的解释器和可用的 `uv`。例如执行 `python3 --version`；Windows 可使用 `py -3 --version`。以下命令中的 `python3` 换成查到的解释器。

从技能目录运行：

```bash
python3 scripts/preflight.py
```

`preflight.py` 启动时只使用标准库，检查 Python 版本，以及 PyMuPDF、Pillow 的版本和实际导入结果。输出包含解释器路径、逐项检查结果和可用的 `uv` 路径。返回码 0 表示所执行的程序检查通过，1 表示存在缺项。

## 2. 准备隔离环境

已有兼容环境时直接使用。需要补齐依赖时，在任务的隔离环境中准备，并保留后续调用方式。

有 `uv` 时：

```bash
uv run --no-project --isolated --with 'pymupdf>=1.26,<2' --with 'pillow>=11,<13' \
  python scripts/preflight.py
```

后续使用 `uv run scripts/zhouyi.py ...`；主脚本的依赖声明与检查要求相同。首次准备环境可能需要联网下载 Python 或依赖包。

使用 Python 虚拟环境时，将环境放到用户输出目录的工作区。例如输出根目录为 `reading/`：

```bash
python3 -m venv reading/.zhouyi/venv
reading/.zhouyi/venv/bin/python -m pip install 'pymupdf>=1.26,<2' 'pillow>=11,<13'
reading/.zhouyi/venv/bin/python scripts/preflight.py
```

Windows 虚拟环境的解释器路径为 `reading/.zhouyi/venv/Scripts/python.exe`。后续 `preflight.py` 和 `zhouyi.py` 使用同一个环境的解释器。已有离线环境可直接使用；下载受限时报告具体缺失的包及安装失败原因。

## 3. 用输入 PDF 验证实际路径

取得用户的 PDF 路径和输出根目录后，用选定的环境执行：

```bash
uv run --no-project --isolated --with 'pymupdf>=1.26,<2' --with 'pillow>=11,<13' \
  python scripts/preflight.py papers/example.pdf --output-root reading/
```

脚本读取原 PDF，检查页数和解锁状态，渲染第一页并用 Pillow 解码；同时在输出目录执行实际写入、读回和清理操作。原页预览保存在 `reading/.zhouyi/preflight/`，具体路径由 `preview` 字段返回。PDF 保持原样。

接着由 agent **打开返回的预览图片**，确认文字和版式可见。纯扫描页结合视觉阅读或可用 OCR 判断能否读取；后续页面按同样标准处理。

`programmatic_checks_ok` 只表示脚本检查通过；图像查看能力由 agent 的实际打开操作确认。成功导入依赖和生成 PNG 尚未完成这一项检查。

## 4. 源码模式的 TeX 环境

源码模式另需 TeX Live 的 `xelatex`、`latexmk` 和 `kpsewhich`，`ctex` 宏包，以及 Noto Serif CJK SC、Noto Sans CJK SC 和 Noto Sans Mono CJK SC 字体。楷体 AR PL UKai CN 可选，用于中文强调，没有时退成倾斜的宋体。TeX 不在 `PATH` 上时，用 `--texbin` 指向 TeX Live 的 `bin` 目录：

```bash
python3 scripts/preflight.py --tex --texbin /path/to/texlive/bin/x86_64-linux
```

编译时缺宏包（日志里的 `File ... not found`）用 `tlmgr install <包名>` 补装。模板按字体名加载 OpenType 字体时（例如 `fontawesome` 宏包要找 FontAwesome），字体文件要在 fontconfig 的搜索路径里；TeX Live 自带的字体可以链接到用户字体目录，再运行 `fc-cache`。

## 5. 进入翻译

检查通过后，在任务工作记录中保存解释器或调用方式、依赖版本及预览查看结果，按 [开场与默认](onboarding.md) 给出开场说明，然后进入技能的输入命名与全文清单步骤。

缺少命令执行、文件访问或图像查看能力时，明确指出缺项及其影响，说明需要启用的能力或补充的运行环境，待具备后继续。依赖安装与联网遵循当前宿主权限；按需使用的 OCR、Markdown 渲染器另行检查。

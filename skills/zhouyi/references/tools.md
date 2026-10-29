# 工具与工作记录

以下命令从本技能目录执行；其他位置调用时，将 `scripts/zhouyi.py` 换成实际路径。`uv` 根据脚本内声明准备隔离环境。也可用已安装 PyMuPDF 与 Pillow 的虚拟环境运行 `python scripts/zhouyi.py`。

先按 [环境检查](environment.md) 验证运行环境、输入 PDF、输出目录与实际图像查看能力，以下命令使用检查通过的解释器或 `uv` 调用方式。

## 准备

先确认输出目录和论文编号，再建立交付与工作目录，复制原 PDF：

```bash
mkdir -p reading/202601.arXiv.Example/assets
mkdir -p reading/.zhouyi/202601.arXiv.Example
cp papers/example.pdf reading/202601.arXiv.Example/202601.arXiv.Example.pdf
uv run scripts/zhouyi.py inspect papers/example.pdf \
  --work-dir reading/.zhouyi/202601.arXiv.Example
```

`inspect` 生成三个文件：

- `source.json`：文件名、SHA-256、页数、PDF 元数据、逐页文字和文字块。
- `source.txt`：按 PDF 文字块顺序排列的全文，每页以 `===== PAGE n =====` 开头，没有文字层的页写一行提示。阅读和翻译时读它，不必从 `source.json` 里拼。
- `progress.json`：源文件身份、命名依据、章节清单、术语、图片来源、复核和问题记录。

再次运行 `inspect` 会先核对源文件哈希，保留已有 `progress.json`。源版本变更时，使用新工作目录。

`source.json` 的 `text` 字段按坐标排序，双栏页的左右两栏会逐行交错，只适合检索；`source.txt` 保留文字块顺序，通常与阅读顺序一致，个别 PDF 的块顺序仍需对照预览确认。

`source.json` 中的页宽高是旋转后的可见页面尺寸；`text_blocks_unrotated` 是 PyMuPDF 的未旋转文字坐标，只辅助阅读顺序判断。裁剪以渲染预览的归一化坐标为准。

## 预览和裁剪

```bash
uv run scripts/zhouyi.py render papers/example.pdf --pages 1,3-5 \
  --output-dir reading/.zhouyi/202601.arXiv.Example/previews

uv run scripts/zhouyi.py crop papers/example.pdf --page 3 \
  --box 0.08 0.22 0.92 0.61 --dpi 300 --object-id table_01 \
  --output reading/202601.arXiv.Example/assets/table_01.png \
  --work-dir reading/.zhouyi/202601.arXiv.Example
```

`render` 支持 `--pages all`，默认 120 DPI。`crop` 默认 300 DPI，输出直接放在论文的 `assets/` 中。整个可见页面的区域是 `0 0 1 1`。

预览和截图用 `sheet` 拼成检查图，每格标出文件名：

```bash
uv run scripts/zhouyi.py sheet reading/.zhouyi/202601.arXiv.Example/previews/page_*.png \
  --output-dir reading/.zhouyi/202601.arXiv.Example/sheets --per 4 --cols 2

uv run scripts/zhouyi.py sheet reading/202601.arXiv.Example/assets/*.png \
  --output-dir reading/.zhouyi/202601.arXiv.Example/sheets --prefix assets --per 4 --cols 1
```

`--per` 是每张拼图的图片数，`--cols` 是列数，`--max-side` 限制拼图的长边（默认 1600 像素）。拼图默认存为 JPEG，每张几百 KB；看过的图片会留在上下文里随每次请求重发，PNG 拼图每张约 2MB，累积几张就可能超出服务端的请求体上限。宽表格和长条图宜用 `--cols 1` 纵向排列。拼图只用于查看，放在工作目录，不进入交付目录。

裁剪记录由工具写入，包含来源哈希、页码、归一化区域、实际像素区域、DPI、尺寸及图片哈希。多段截图使用相同的 `--object-id` 和不同文件名。

两个只看结果、不必逐页看图的命令：

```bash
uv run scripts/zhouyi.py compare papers/example.pdf W/build/main.pdf
uv run scripts/zhouyi.py review-sheet reading/202601.arXiv.Example/202601.arXiv.Example.zh.pdf \
  --output-dir W/tmp/review --log W/build/main.log
```

`compare` 用于开译前：逐页给出原版文字在底稿相邻两页里的覆盖率，列出低于 `--threshold`（默认 0.85）的页，并报出两边的页数、正文主字体和只在底稿里出现的字符。`review-sheet` 用于交付前：把首页和日志里有超宽溢出、缺字的页拼成一张图，最多 `--max-pages` 页（默认 8）。

## 填写进度

`progress.json` 是 agent 可编辑的工作记录。保留工具生成的 `schema_version`、`source` 和裁剪来源字段，按实际工作更新下面内容。

`metadata` 记录命名依据，例如首次公开时间和官方页面 URL；联网确认发表会议时，把所查来源与输入 PDF 的版本区分记录。

`sections` 从原文建立，随后更新状态。一个章节可以覆盖多页，同一页也可以属于多个章节：

```json
{
  "id": "2.1",
  "title": "Training Procedure",
  "source_pages": [3, 4],
  "status": "translating",
  "units": [
    {"id": "p1", "kind": "paragraph", "status": "translated"},
    {"id": "eq1", "kind": "equation", "status": "pending"},
    {"id": "table1", "kind": "table", "status": "pending"}
  ],
  "note": "第 3 页末段延续到第 4 页左栏。"
}
```

状态使用 `pending`、`translating`、`translated`、`verified`。`units` 可以省略，章节记录本身就够用；需要细分的长节或难节再登记内容单元，登记了的单元都要核对后设为 `verified`。逐段对照原文后才将章节设为 `verified`。参考文献也登记为章节；全部来源页都要有对应清单。

`glossary` 维护统一译名，例如：

```json
{"attention head": "注意力头", "positional encoding": "位置编码"}
```

`assets` 中每张图已有工具写入的记录。实际打开图片并对照原页后，填写其复核字段：

```json
{"status": "verified", "note": "已查看输出图片与原页；表头、末行、底线和两条脚注完整，下一页无续表。"}
```

这段内容写入该图的 `visual_review`。重裁会自动将对应复核状态恢复为 `pending`。

`issues` 中每条问题包含 `location`、`detail`、`status`。待处理用 `open`，解决后用 `resolved`。原 PDF 自身模糊、已按约定保留图注等可明确交付的限制用 `noted`，最终回复说明位置。待辨认正文仍用 `open`。

最终全文核对后，填写 `document_review.status` 为 `verified`，`note` 说明实际核对范围，并把 `check` 返回的 `markdown_sha256` 写入 `document_review.markdown_sha256`。之后修改 Markdown，需要重新核对受影响内容并更新该哈希。

## 校验与打包

```bash
uv run scripts/zhouyi.py check reading/202601.arXiv.Example \
  --source papers/example.pdf

uv run scripts/zhouyi.py check reading/202601.arXiv.Example \
  --source papers/example.pdf --require-reviewed

uv run scripts/zhouyi.py pack reading/202601.arXiv.Example \
  --source papers/example.pdf
```

工具默认寻找输出根目录的 `.zhouyi/<论文编号>/`，也可用 `--work-dir` 指定。目录名不符合 `YYYYMM.Venue.ShortName` 时只给警告，便于沿用输出根目录已有的命名约定。约定要求留在论文目录中的附属文件，用 `--allow-extra` 列出，可重复，`check` 与 `pack` 都接受，例如 `--allow-extra meta.json`。`check` 返回：

- `files_ok`：原 PDF、Markdown、图片文件及相对路径是否通过检查。
- `ready`：文件检查通过，且所记录的章节、内容单元、图片和全文复核均完成。
- `errors`：文件或来源一致性问题。
- `pending_reviews`：尚待核对或因修改而失效的记录。
- `warnings`：已说明的限制或未使用的旧裁剪记录。

普通 `check` 在文件检查通过时返回 0；加 `--require-reviewed` 后，还要求复核记录完整。结构检查只针对本技能输出格式，语义忠实度通过原文对照确认。

`pack` 执行相同的完整交付检查，默认在论文目录旁生成 ZIP。更新已有 ZIP 时加 `--replace`。压缩包包含顶层论文目录；`.zhouyi/` 工作记录留在输出根目录。

校验命令的返回码为：0 通过，1 检查未通过，2 输入或运行错误。工具输出 JSON，便于 agent 定位问题。

<h1 align="center">Zhouyi · 周译</h1>

<p align="center">
  An agent skill for translating complete English research papers into Chinese:<br>
  a Chinese PDF in the original layout when the arXiv LaTeX source is available, Chinese Markdown with the original figures otherwise.
</p>

<p align="center">
  <a href="README.md">简体中文</a> &nbsp;·&nbsp; <b>English</b>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue"></a>
  <a href="https://agentskills.io"><img alt="Agent Skills" src="https://img.shields.io/badge/format-Agent%20Skills-6f42c1"></a>
  <img alt="Works with Claude Code, Codex and other agents" src="https://img.shields.io/badge/works%20with-Claude%20Code%20%C2%B7%20Codex%20%C2%B7%20more-2ea44f">
</p>

<p align="center"><code>npx skills add TingYu1769/skills-zhouyi</code></p>

Zhouyi (`zhouyi`) is an [Agent Skill](https://agentskills.io) for translating complete English research papers into Chinese. Author information, body text, footnotes, and appendices are all translated; equations, citations, and numbering follow the source; bibliography entries stay in English. It is designed for agents that can read and write files, run commands, and view images, such as Claude Code and Codex.

> Use Zhouyi to translate `papers/example.pdf` into Chinese. Save it under `reading/` and use Example as the short name.

## Two modes

| | Source mode | PDF mode |
| --- | --- | --- |
| When | The arXiv LaTeX source matching the PDF version is available | Only a PDF exists, or the source is missing, mismatched, or does not compile locally |
| How | Translates the source chunk by chunk, with equations, commands, and citations protected by placeholders, then rebuilds with XeLaTeX | Translates section by section from the PDF and crops figures and tables from the original pages, checking every image |
| Output | A Chinese PDF in the original layout, plus a recompilable Chinese TeX project | Chinese Markdown, the original PDF, and an `assets/` image folder, optionally zipped |

Source mode reuses the paper's own image files and lets LaTeX typeset tables and equations as before; table cells stay in English while captions are translated. For arXiv preprints the agent downloads the source matching the version stamped on the PDF's first page and falls back to PDF mode when it cannot.

## First run: check the environment

Zhouyi starts by checking file access, command execution, image viewing, Python 3.10+, PyMuPDF, and Pillow. The agent then renders a page from your PDF, opens it for inspection, and verifies that the output directory is writable. The checker starts with the Python standard library alone:

```bash
python3 skills/zhouyi/scripts/preflight.py
```

Use an existing compatible environment, or prepare an isolated one with [uv](https://docs.astral.sh/uv/) and check your actual input and output paths:

```bash
uv run --no-project --isolated --with 'pymupdf>=1.26,<2' --with 'pillow>=11,<13' \
  python skills/zhouyi/scripts/preflight.py papers/example.pdf --output-root reading/
```

Source mode also needs TeX Live (`xelatex`, `latexmk`, and the `ctex` package) and the Noto CJK SC fonts; AR PL UKai is optional. Add `--tex` to check them, and `--texbin` to point at the TeX Live `bin` directory when TeX is not on `PATH`:

```bash
python3 skills/zhouyi/scripts/preflight.py --tex
```

Missing capabilities or dependencies are reported with a way to resolve them. See [environment setup](skills/zhouyi/references/environment.md) for virtual environments, Windows paths, offline use, and TeX.

## Installation

Install with the [skills CLI](https://github.com/vercel-labs/skills):

```bash
npx skills add TingYu1769/skills-zhouyi
```

Alternatively, copy the entire `skills/zhouyi/` directory to your agent's skill directory:

| Agent | Personal skill directory |
| --- | --- |
| Claude Code | `~/.claude/skills/zhouyi/` |
| Codex | `~/.codex/skills/zhouyi/` |

Keep `SKILL.md`, `references/`, `scripts/`, `assets/`, and `agents/` together. You can also ask your agent to read [SKILL.md](skills/zhouyi/SKILL.md) directly.

## Usage

Provide a PDF and an output directory. You may also specify a short name, publication details, and whether to create a ZIP.

```text
Use $zhouyi to translate papers/example.pdf into Chinese.
Save it under reading/ and use Example as the short name.
```

```text
Resume the Example translation under reading/ from its saved progress
and complete the whole paper.
```

Folders use `YYYYMM.Venue.ShortName` by default: the first public month, the confirmed conference or journal, and the paper's short name. Preprints use `arXiv`. The agent checks local metadata first and consults official sources when needed; an output root with its own naming convention keeps that convention.

## Output

PDF mode:

```text
reading/
├── 202601.arXiv.Example/
│   ├── 202601.arXiv.Example.md
│   ├── 202601.arXiv.Example.pdf
│   └── assets/
│       ├── fig_01.png
│       ├── table_01.png
│       └── algorithm_01.png
└── 202601.arXiv.Example.zip       # optional
```

Source mode:

```text
reading/
└── 202601.arXiv.Example/
    ├── 202601.arXiv.Example.pdf       # original PDF
    ├── 202601.arXiv.Example.zh.pdf    # Chinese PDF
    └── zh-tex/                        # recompilable Chinese TeX project
```

The PDF is copied byte for byte, and relative image paths keep the folder portable. Progress, terminology, and review records live in `.zhouyi/` under the output root so the agent can resume interrupted work.

## Translation and review

- **Complete text.** Author information, abstract, body, footnotes, acknowledgments, and appendices are translated. Bibliography entries remain in English.
- **Academic structure.** Titles and section headings are bilingual. Important terms include English on first use. Equations, citations, numbering, variables, and argument order follow the source.
- **Original-page captures (PDF mode).** Figures and tables are rendered and cropped directly from the supplied PDF. The agent opens every image and compares legends, edge labels, final rows, and footnotes against the source. Objects spanning pages are saved in order.
- **Copyable algorithms and prompts (PDF mode).** Original screenshots come with Chinese text, retaining variables, placeholders, protocol tags, and executable code.
- **Merge checks and fallback (source mode).** Each chunk's placeholders and braces are checked before merging. When a translated chunk breaks the build, that chunk falls back to English and its section returns to pending until it is fixed and merged again.
- **Resumable work.** Section progress and terminology are saved. The agent verifies the source before continuing unfinished sections.

## Development and evaluation

[Skill](skills/zhouyi/SKILL.md) · [Tools](skills/zhouyi/references/tools.md) · [Source mode](skills/zhouyi/references/latex-source.md) · [Scenarios](evals/scenarios.md) · [Results](evals/results.md) · [Changelog](CHANGELOG.md)

Run the functional tests:

```bash
uv run --with 'pymupdf>=1.26,<2' --with 'pillow>=11,<13' python -m unittest discover -s evals -p 'test_*.py' -v
```

Six of them compile with XeLaTeX. They run when `latexmk` is on `PATH` or the `ZHOUYI_TEXBIN` environment variable points at the TeX Live `bin` directory, and are skipped otherwise.

When reporting an issue, include the paper's layout or template, the location, the expected outcome, and the actual result. A minimal reproducible sample helps diagnose the problem.

## License

[MIT](LICENSE). Input papers and their screenshots retain their original copyright.

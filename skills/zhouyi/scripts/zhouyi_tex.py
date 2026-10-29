#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""arXiv LaTeX source mode for Zhouyi: unpack the source, cut it into translatable chunks,
restore the translations, add Chinese support and build with XeLaTeX."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile

SETUP = Path(__file__).resolve().parent.parent / "assets" / "zh-setup.tex"
MARK = "% zhouyi:"
# Encoding and Type 1 font lines that do not work with fontspec; the value names the families
# zh-setup.tex then replaces with OpenType fonts (rm, sf, tt: TeX Gyre; ttinconsolata, ttlmmono).
# Font lines are also looked for in the source's other .tex, .cls and .sty files, where templates
# often load them with \RequirePackage or set \rmdefault directly.
PKG = r"\\(?:usepackage|RequirePackage)(\[[^\]]*\])?"
FONT_PATCHES = [
    (r"\\pdfoutput\s*=\s*1", ""),
    (r"\\usepackage(\[[^\]]*\])?\{fontenc\}", ""),
    (r"\\usepackage(\[[^\]]*\])?\{inputenc\}", ""),
    (PKG + r"\{(times|mathptmx)\}", "rm sf tt"),
    (PKG + r"\{(helvet|tgheros)\}", "sf"),
    (PKG + r"\{(courier|tgcursor)\}", "tt"),
    (PKG + r"\{tgtermes\}", "rm"),
    (PKG + r"\{inconsolata\}", "ttinconsolata"),
    (PKG + r"\{zlmtt\}", "ttlmmono"),
    (r"\\renewcommand\*?\s*\{?\\rmdefault\}?\s*\{ptm\}", "rm"),
    (r"\\renewcommand\*?\s*\{?\\sfdefault\}?\s*\{phv\}", "sf"),
    (r"\\renewcommand\*?\s*\{?\\ttdefault\}?\s*\{pcr\}", "tt"),
]
# The same packages named inside a comma list, such as \usepackage{iclr2027_conference,times}:
# commenting the line would also drop the template, so only these names leave the list.
LISTED_FONTS = {"times": "rm sf tt", "mathptmx": "rm sf tt", "helvet": "sf", "tgheros": "sf",
                "courier": "tt", "tgcursor": "tt", "tgtermes": "rm", "inconsolata": "ttinconsolata",
                "zlmtt": "ttlmmono", "fontenc": "", "inputenc": ""}
DROPPED = re.compile(rf"{re.escape(MARK)} dropped ([\w,.-]+)")
NO_MATH = "\\PassOptionsToPackage{no-math}{fontspec}"
# Display names given to \newtheorem in the preamble, which no package setting can reach; the
# build copy gets the Chinese name, as with the font lines. Unknown names stay as they are.
THEOREM_NAMES = {"Theorem": "定理", "Lemma": "引理", "Proposition": "命题", "Corollary": "推论",
                 "Definition": "定义", "Assumption": "假设", "Remark": "注", "Example": "例", "Claim": "断言",
                 "Conjecture": "猜想", "Hypothesis": "假说", "Fact": "事实", "Property": "性质",
                 "Condition": "条件", "Observation": "观察", "Problem": "问题", "Note": "注"}
THEOREM_LINE = re.compile(r"(\\newtheorem\*?\s*\{[^{}]+\}\s*(?:\[[^\]]*\]\s*)?\{)(" + "|".join(THEOREM_NAMES) + r")(\})")


class TexError(ValueError):
    """An actionable input or workflow error."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="surrogateescape")


def find_main(root: Path, main: str | None = None) -> str:
    if main:
        if not (root / main).is_file():
            raise TexError(f"{main} is not in the source package")
        return main
    readme = root / "00README.json"
    if readme.is_file():
        for item in json.loads(readme.read_text(encoding="utf-8")).get("sources", []):
            if item.get("usage") == "toplevel" and (root / item.get("filename", "")).is_file():
                return item["filename"]
    candidates = [p.name for p in root.glob("*.tex") if re.search(r"^\s*\\documentclass", read_text(p), re.M)]
    if len(candidates) != 1:
        raise TexError(f"Cannot pick the main file among {candidates or 'no .tex files'}; "
                       "check 00README.json or pass --main")
    return candidates[0]


def tex_files(root: Path, main: str) -> list[str]:
    """Main file plus files reached through \\input/\\include, in reading order."""
    seen: list[str] = []

    def visit(name: str) -> None:
        path = root / name
        if not path.is_file() and not name.endswith(".tex"):
            path = root / f"{name}.tex"  # \\input{tables/x_1.7B} names tables/x_1.7B.tex
        rel = path.relative_to(root).as_posix()
        if rel in seen or not path.is_file():
            return
        seen.append(rel)
        body = re.sub(r"(?<!\\)%.*", "", read_text(path))
        for match in re.finditer(r"\\(?:input|include)\s*\{([^}]+)\}", body):
            visit(match[1].strip())

    visit(main)
    return seen


def init_progress(work_dir: Path, record: dict) -> None:
    """Same review fields as PDF mode, in the tex work directory so both modes can exist for one paper."""
    if not (work_dir / "progress.json").exists():
        write_json(work_dir / "progress.json", {
            "schema_version": 1, "mode": "tex",
            "source": {"filename": record["source_file"], "sha256": record["source_sha256"]},
            "metadata": {}, "sections": [], "glossary": {}, "assets": {}, "issues": [],
            "document_review": {"status": "pending", "pdf_sha256": "", "note": ""},
        })


def prepare(tarball: Path, work_dir: Path, main: str | None = None) -> dict:
    """Unpack into W/src (kept as delivered) and W/build (working copy); record the main file,
    named by `main` when the package holds several top-level documents."""
    tarball, work_dir = Path(tarball), Path(work_dir)
    digest = sha256(tarball)
    record_path = work_dir / "tex.json"
    if record_path.is_file():
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("source_sha256") != digest:
            raise TexError("Source package differs from the saved record; use a new work directory")
        if main and record["main"] != main:
            raise TexError(f"This work directory was prepared with {record['main']}; use a new one")
        init_progress(work_dir, record)
        return record
    src, build = work_dir / "src", work_dir / "build"
    if src.exists() or build.exists():
        raise TexError(f"{src} or {build} exists without tex.json; remove them first")
    with tarfile.open(tarball, "r:*") as archive:
        for member in archive.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or member.issym() or member.islnk():
                raise TexError(f"Unsafe path in source package: {member.name}")
        archive.extractall(src, filter="data")
    shutil.copytree(src, build)
    try:
        main = find_main(src, main)
    except TexError:
        shutil.rmtree(src)  # leave nothing behind, so the retry with --main can unpack again
        shutil.rmtree(build)
        raise
    record = {"source_sha256": digest, "source_file": tarball.name, "main": main,
              "tex_files": tex_files(src, main)}
    write_json(record_path, record)
    init_progress(work_dir, record)
    return record


# ---- Scanner: split LaTeX into translatable text and kept spans -------------------------------

# Environments TeX reads verbatim. They are kept whole and end at the first literal \end{name}: inside
# them % is not a comment and \begin does not nest. split() adds the ones a paper defines itself.
VERBATIM_ENVS = frozenset({"verbatim", "verbatim*", "Verbatim", "Verbatim*", "BVerbatim", "LVerbatim",
                           "VerbatimOut", "lstlisting", "minted", "comment", "tcblisting"})
# Environments kept whole: math, verbatim-like, table bodies (cells stay English), bibliographies.
WHOLE_ENVS = {
    "equation", "equation*", "align", "align*", "alignat", "alignat*", "gather", "gather*",
    "multline", "multline*", "eqnarray", "eqnarray*", "displaymath", "math", "flalign", "flalign*",
    "tabular", "tabular*", "tabularx", "tabulary", "longtable", "array", "tikzpicture",
    "thebibliography",
} | VERBATIM_ENVS
TABLE_ENVS = {"tabular", "tabular*", "tabularx", "tabulary", "longtable"}
# Commands whose argument TeX reads verbatim, between two equal delimiters or in braces.
INLINE_VERBATIM = {"verb", "Verb", "lstinline", "mintinline", "path", "nolinkurl"}
# Commands kept together with every argument group that follows them.
KEEP_WITH_ARGS = {
    "cite", "citep", "citet", "citealp", "citealt", "citeauthor", "citeyear", "citeyearpar", "nocite",
    "ref", "eqref", "autoref", "cref", "Cref", "pageref", "nameref", "label", "url", "includegraphics",
    "input", "include", "bibliography", "bibliographystyle", "newcommand", "renewcommand",
    "providecommand", "newenvironment", "renewenvironment", "definecolor", "setlength", "addtolength",
    "setcounter", "addtocounter", "vspace", "hspace", "vskip", "hskip", "color", "pagestyle",
    "thispagestyle", "hypersetup", "captionsetup", "usepackage", "RequirePackage", "documentclass",
    "newcolumntype", "graphicspath", "DeclareMathOperator", "newtheorem", "theoremstyle", "bibitem",
    "fontsize", "linespread", "rule", "arraystretch", "def",
}
# Commands whose first N groups are not prose; later groups are translated.
KEEP_FIRST_ARGS = {"href": 1, "textcolor": 1, "colorbox": 1, "fcolorbox": 2, "resizebox": 2,
                   "scalebox": 1, "rotatebox": 1, "raisebox": 1, "parbox": 1, "multicolumn": 2,
                   "multirow": 2, "makecell": 0}
# Environments whose \begin takes non-prose groups after optional arguments.
ENV_ARGS = {"minipage": 1, "wrapfigure": 2, "subfigure": 1, "tcolorbox": 0, "algorithmic": 0}
# Commands that may only appear in the preamble region's translatable arguments.
FRONT_TEXT = ("title", "thanks")
PLACEHOLDER = re.compile(r"⟦P(\d+)⟧")
CJK = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")


def skip_group(text: str, i: int, open_c: str = "{", close_c: str = "}") -> int:
    """Index after the group opening at text[i]; honours escapes and comments."""
    depth, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "%":
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise TexError(f"Unbalanced {open_c}{close_c} starting near: {text[max(0, i - 40):i][:80]!r}")


def skip_args(text: str, i: int, groups: int | None) -> int:
    """Skip optional [..] groups and up to `groups` {..} groups (all if None), allowing spaces between."""
    taken = 0
    while True:
        j = i
        # Spaces and a single line break may separate a command from its arguments; a blank line may not.
        while j < len(text) and (text[j] in " \t" or (text[j] == "\n" and text[j + 1:j + 2] != "\n")):
            j += 1
        if j < len(text) and text[j] == "[" and (groups is None or taken < max(groups, 1)):
            i = skip_group(text, j, "[", "]")
        elif j < len(text) and text[j] == "{" and (groups is None or taken < groups):
            i = skip_group(text, j)
            taken += 1
        else:
            return i


def verbatim_end(text: str, i: int, env: str) -> int:
    """Index after the first literal \\end{env} at or after i, as TeX itself ends a verbatim body."""
    m = re.compile(r"\\end\s*\{" + re.escape(env) + r"\}").search(text, i)
    if not m:
        raise TexError(f"line {text.count(chr(10), 0, i) + 1}: no \\end{{{env}}}")
    return m.end()


def inline_verbatim_end(text: str, i: int, base: str) -> int:
    """Index after the argument of \\verb|..|, \\lstinline[..]{..}, \\mintinline{lang}|..| and the like;
    i is just after the command name. A malformed use keeps only the command name."""
    if base == "mintinline":
        i = skip_args(text, i, 1)  # options and the language
    elif base in ("lstinline", "Verb"):
        i = skip_args(text, i, 0)  # options only
    if i >= len(text) or text[i].isspace() or text[i].isalpha():
        return i
    if text[i] == "{" and base != "verb":
        return skip_group(text, i)
    j = text.find(text[i], i + 1)
    line_end = text.find("\n", i + 1)
    return i if j < 0 or 0 <= line_end < j else j + 1  # a verbatim argument cannot span lines


def find_env_end(text: str, i: int, env: str) -> int:
    """Index after the \\end{env} matching a \\begin{env} that ends before i."""
    pattern = re.compile(r"\\(begin|end)\s*\{" + re.escape(env) + r"\}")
    depth = 1
    for m in pattern.finditer(text, i):
        if in_comment(text, m.start()):  # a commented-out % \begin{tabular}{...} inside the table
            continue
        depth += 1 if m[1] == "begin" else -1
        if depth == 0:
            return m.end()
    raise TexError(f"No \\end{{{env}}}")


def scan(text: str, start: int = 0, end: int | None = None,
         verbatim: frozenset[str] = VERBATIM_ENVS) -> list[tuple[str, int, int]]:
    """Segments ("text"|"keep", start, end) covering text[start:end]; `verbatim` names the
    environments kept whole up to their first literal \\end."""
    end = len(text) if end is None else end
    segs: list[tuple[str, int, int]] = []

    def add(kind: str, a: int, b: int) -> None:
        if a < b:
            segs.append((kind, a, b))

    i, mark = start, start
    while i < end:
        c = text[i]
        if c == "%":
            j = text.find("\n", i)
            j = end if j < 0 or j >= end else j + 1
            add("text", mark, i); add("keep", i, j); i = mark = j
        elif c == "$":
            delim = "$$" if text.startswith("$$", i) else "$"
            j = i + len(delim)
            while True:
                j = text.find(delim, j)
                if j < 0:
                    raise TexError(f"line {text.count(chr(10), 0, i) + 1}: unclosed math")
                if text[j - 1] != "\\":
                    break
                j += 1
            if re.search(r"\n[ \t]*\n", text[i:j]):
                # TeX forbids a paragraph break in math, so this $ is not math: it most likely sits in
                # a verbatim-like environment or command the splitter does not know.
                raise TexError(f"line {text.count(chr(10), 0, i) + 1}: {delim} is not closed before a blank "
                               "line; if it sits in a verbatim-like environment, name it with --verbatim-env")
            add("text", mark, i); add("keep", i, j + len(delim)); i = mark = j + len(delim)
        elif c == "\\":
            m = re.match(r"\\([A-Za-z@]+\*?)", text[i:i + 64])
            if not m:  # control symbol: \\ \( \[ \% \& ...
                if text.startswith("\\(", i) or text.startswith("\\[", i):
                    close = "\\)" if text[i + 1] == "(" else "\\]"
                    j = text.find(close, i + 2)
                    if j < 0:
                        raise TexError("Unclosed math")
                    j += 2
                elif text.startswith("\\\\", i):
                    j = skip_args(text, i + 2 + (text[i + 2:i + 3] == "*"), 0)
                else:
                    j = i + 2
                add("text", mark, i); add("keep", i, j); i = mark = j
                continue
            name, j = m[1], i + len(m[0])
            base = name.rstrip("*")
            if base in INLINE_VERBATIM:
                j = inline_verbatim_end(text, j, base)
            elif name in ("begin", "end"):
                em = re.match(r"\s*\{([^}]+)\}", text[j:])
                if not em:
                    raise TexError(f"Malformed \\{name} near {text[i:i + 40]!r}")
                env, j = em[1].strip(), j + em.end()
                if name == "begin" and env in verbatim:
                    j = verbatim_end(text, j, env)
                elif name == "begin" and env in WHOLE_ENVS:
                    j = find_env_end(text, j, env)
                elif name == "begin":
                    j = skip_args(text, j, ENV_ARGS.get(env, 0))
            elif base in KEEP_WITH_ARGS:
                if base == "def":
                    j = skip_group(text, text.index("{", j))
                else:
                    j = skip_args(text, j, None)
            elif base in KEEP_FIRST_ARGS:
                j = skip_args(text, j, KEEP_FIRST_ARGS[base])
            else:
                j = skip_args(text, j, 0)  # command name and optional args; {..} stays prose
            add("text", mark, i); add("keep", i, j); i = mark = j
        else:
            i += 1
    add("text", mark, end)
    return segs


def scan_front(text: str, start: int, end: int,
               verbatim: frozenset[str] = VERBATIM_ENVS) -> list[tuple[str, int, int]]:
    """Preamble: everything kept except the arguments of \\title and \\thanks."""
    segs, pos = [], start
    for m in re.finditer(r"\\(" + "|".join(FRONT_TEXT) + r")\s*(\[[^\]]*\])?\s*\{", text[start:end]):
        a = start + m.end()
        if a <= pos or text.rfind("%", text.rfind("\n", 0, a) + 1, a) >= 0:
            continue
        b = skip_group(text, a - 1) - 1
        segs.append(("keep", pos, a))
        segs.extend(scan(text, a, b, verbatim))
        pos = b
    segs.append(("keep", pos, end))
    return [s for s in segs if s[1] < s[2]]


def boundary(text: str, seg: tuple[str, int, int]) -> str | None:
    """"section" for \\section or \\appendix, "subsection" for \\subsection, else None."""
    kind, a, _ = seg
    m = re.match(r"\\(section|appendix|subsection)\*?(?![A-Za-z])", text[a:a + 16]) if kind == "keep" else None
    return None if m is None else ("subsection" if m[1] == "subsection" else "section")


def prose_chars(text: str, segs: list) -> int:
    return sum(y - x for k, x, y in segs if k == "text" and text[x:y].strip())


def split_file(rel: str, text: str, is_main: bool, max_chars: int = 14000,
               verbatim: frozenset[str] = VERBATIM_ENVS, warnings: list[str] | None = None) -> list[dict]:
    """Chunks for one file: front matter (main only), then one chunk per \\section; a section whose
    prose exceeds max_chars is cut at \\subsection and neighbouring subsections are regrouped up to
    max_chars. Chunks never cross a section or a file, and never cut inside a brace group: a section
    that opens inside one (\\hypertarget{x}{\\section{..}..}) stays with the chunk before it."""
    warnings = [] if warnings is None else warnings
    chunks = []
    if is_main:
        b = re.search(r"\\begin\s*\{document\}", text)
        e = re.search(r"\\end\s*\{document\}", text)
        if not b or not e:
            raise TexError(f"{rel}: no document environment")
        chunks.append(scan_front(text, 0, b.end(), verbatim))
        body = scan(text, b.end(), e.start(), verbatim)
        tail = [("keep", e.start(), len(text))] if e.start() < len(text) else []
    else:
        body, tail = scan(text, verbatim=verbatim), []
    def make_units(in_groups: bool) -> tuple[list[list], int, int]:
        """Units start at every section or subsection outside brace groups (anywhere, if in_groups);
        each remembers whether it opens a section. Only text segments hold bare braces: commands,
        their arguments, math, comments and verbatim are kept segments."""
        units: list[list] = [[False, []]]
        depth = held = 0
        for seg in body:
            kind = boundary(text, seg)
            if kind and depth and not in_groups:
                held += 1
            elif kind and prose_chars(text, units[-1][1]):
                units.append([kind == "section", []])
            elif kind == "section":
                units[-1][0] = True
            units[-1][1].append(seg)
            if seg[0] == "text":
                depth += text.count("{", seg[1], seg[2]) - text.count("}", seg[1], seg[2])
        return units, depth, held

    units, depth, held = make_units(False)
    if depth:
        warnings.append(f"{rel}: braces in the prose do not balance ({depth:+d}); cut at every section as before")
        units, _, _ = make_units(True)
    elif held:
        warnings.append(f"{rel}: {held} section start(s) inside a brace group stay in the chunk before them")
    current: list = []
    for opens_section, segs in units:
        full = prose_chars(text, current)
        if current and full and (opens_section or full + prose_chars(text, segs) > max_chars):
            chunks.append(current)
            current = []
        current += segs
    chunks.append(current + tail)
    return [{"file": rel, "start": c[0][1], "end": c[-1][2], "segments": c} for c in chunks if c]


def render_chunk(text: str, segments: list) -> tuple[str, dict]:
    """Chunk source with kept spans (merging adjacent ones and whitespace between them) as ⟦Pn⟧."""
    merged: list[list] = []
    for kind, a, b in segments:
        if merged and kind == "keep" and merged[-1][0] == "keep":
            merged[-1][2] = b
        elif (merged and kind == "text" and merged[-1][0] == "keep" and not text[a:b].strip()
              and "\n\n" not in text[a:b]):
            merged[-1][2] = b
        else:
            merged.append([kind, a, b])
    out, holders = [], {}
    for kind, a, b in merged:
        if kind == "keep":
            key = f"⟦P{len(holders) + 1}⟧"
            holders[key] = text[a:b]
            out.append(key)
        else:
            out.append(text[a:b])
    return "".join(out), holders


def in_comment(text: str, at: int) -> bool:
    """Whether position `at` lies after an unescaped % on its line."""
    line = text[text.rfind("\n", 0, at) + 1:at]
    return bool(re.search(r"(?<!\\)(?:\\\\)*%", line))


def chunk_title(text: str, chunk: dict, index: int, is_main: bool) -> str:
    # A commented-out heading (e.g. a dropped "AI use statement") must not name the chunk.
    for m in re.finditer(r"\\(section|subsection)\*?\s*\{", text[chunk["start"]:chunk["end"]]):
        if in_comment(text, chunk["start"] + m.start()):
            continue
        open_at = chunk["start"] + m.end() - 1
        return " ".join(text[open_at + 1:skip_group(text, open_at) - 1].split())
    if any(not in_comment(text, chunk["start"] + m.start())
           for m in re.finditer(r"\\begin\s*\{abstract\}", text[chunk["start"]:chunk["end"]])):
        return "Abstract"
    return "front matter" if index == 0 and is_main else f"{chunk['file']} part {index + 1}"


VERBATIM_BODY = re.compile(r"\\begin\s*\{(" + "|".join(map(re.escape, sorted(VERBATIM_ENVS, key=len, reverse=True)))
                           + r")\}.*?\\end\s*\{\1\}", re.S)
VERBATIM_DEFINERS = re.compile(r"\\(?:lstnewenvironment|newtcblisting|renewtcblisting|NewTCBListing|RenewTCBListing|"
                               r"DeclareTCBListing|ProvideTCBListing|DefineVerbatimEnvironment|"
                               r"RecustomVerbatimEnvironment)\s*(?:\[[^\]]*\]\s*)?\{([^{}]+)\}")
NEWMINTED = re.compile(r"\\newminted\s*(?:\[([^\]]*)\]\s*)?\{([^{}]+)\}")
ENV_DEFINERS = re.compile(r"\\(newenvironment|renewenvironment|NewDocumentEnvironment|RenewDocumentEnvironment|"
                          r"DeclareDocumentEnvironment|ProvideDocumentEnvironment)\s*\{([^{}]+)\}")
VERBATIM_OPENERS = re.compile(r"\\VerbatimEnvironment|\\begin\s*\{(?:Verbatim|BVerbatim|LVerbatim|verbatim|lstlisting|minted)\}")


def detect_verbatim_envs(src: Path) -> list[str]:
    """Environments the source defines as verbatim (listings, tcolorbox listings, fancyvrb, minted, or
    an environment whose begin code opens one), ignoring definitions in comments or in verbatim text."""
    found = set()
    for path in sorted(p for p in Path(src).rglob("*") if p.is_file() and p.suffix in (".tex", ".sty", ".cls")):
        text = VERBATIM_BODY.sub("", read_text(path))  # first: inside verbatim, % is not a comment
        text = re.sub(r"(?<!\\)((?:\\\\)*)%[^\n]*\n?", r"\1", text)  # comments, with their line break
        found.update(m[1].strip() for m in VERBATIM_DEFINERS.finditer(text))
        found.update((m[1] or f"{m[2]}code").strip() for m in NEWMINTED.finditer(text))
        for m in ENV_DEFINERS.finditer(text):
            k, spec = m.end(), "Document" in m[1]  # the xparse forms take an argument spec first
            try:
                while True:  # skip [n][default] or the argument spec, then read the begin code
                    k = re.compile(r"\s*").match(text, k).end()
                    if text.startswith("[", k):
                        k = skip_group(text, k, "[", "]")
                    elif spec and text.startswith("{", k):
                        k, spec = skip_group(text, k), False
                    else:
                        break
                if text.startswith("{", k) and VERBATIM_OPENERS.search(text[k:skip_group(text, k)]):
                    found.add(m[2].strip())
            except TexError:
                continue
    return sorted(found)


def split(work_dir: Path, max_chars: int = 14000, verbatim_envs: list[str] | tuple = ()) -> dict:
    work_dir = Path(work_dir)
    record = read_json(work_dir / "tex.json")
    src, out = work_dir / "src", work_dir / "chunks"
    out.mkdir(exist_ok=True)
    own = sorted(set(detect_verbatim_envs(src)) | {e.strip() for e in verbatim_envs if e.strip()})
    verbatim = VERBATIM_ENVS | set(own)
    report_envs = verbatim | TABLE_ENVS
    index, kept_prose, warnings = [], [], []
    for rel in record["tex_files"]:
        text = read_text(src / rel)
        is_main = rel == record["main"]
        try:
            file_chunks = split_file(rel, text, is_main, max_chars, verbatim, warnings)
        except TexError as exc:
            raise TexError(f"{rel}: {exc}") from None
        for k, chunk in enumerate(file_chunks):
            cid = f"c{len(index) + 1:02d}"
            body, holders = render_chunk(text, chunk["segments"])
            has_prose = bool(PLACEHOLDER.sub("", body).strip())
            for value in holders.values():  # long English kept whole, for the agent to judge
                em = re.match(r"\\begin\s*\{([^}]+)\}", value)
                letters = len(re.findall(r"[A-Za-z]", value))
                if em and em[1].strip() in report_envs and letters > 400:
                    kept_prose.append({"chunk": cid if has_prose else None, "file": rel,
                                       "env": em[1].strip(), "letters": letters})
            if not has_prose:
                continue  # nothing to translate; the span stays as in the source
            (out / f"{cid}.src.tex").write_text(body, encoding="utf-8", errors="surrogateescape")
            write_json(out / f"{cid}.map.json", {"file": rel, "start": chunk["start"], "end": chunk["end"],
                                                 "placeholders": holders})
            index.append({"id": cid, "file": rel, "title": chunk_title(text, chunk, k, is_main),
                          "start": chunk["start"], "end": chunk["end"],
                          "prose_chars": len(PLACEHOLDER.sub("", body))})
    # Chunk offsets point into W/src as it is now; merge refuses to run if a file changes later.
    hashes = {rel: sha256(src / rel) for rel in record["tex_files"]}
    write_json(out / "index.json", {"source_sha256": record["source_sha256"], "src_sha256": hashes,
                                    "brace_rule": "source", "verbatim_envs": own, "kept_prose": kept_prose,
                                    "warnings": warnings, "chunks": index})
    progress = read_json(work_dir / "progress.json")
    known = {s.get("id") for s in progress["sections"]}
    progress["sections"] += [{"id": c["id"], "title": c["title"], "chunk": c["id"], "files": [c["file"]],
                              "status": "pending"} for c in index if c["id"] not in known]
    write_json(work_dir / "progress.json", progress)
    return {"chunks": len(index), "prose_chars": sum(c["prose_chars"] for c in index), "dir": str(out),
            "verbatim_envs": own, "kept_prose": kept_prose, "warnings": warnings}


def unescaped(text: str, chars: str = "%&#_$") -> int:
    return sum(1 for m in re.finditer(r"(?<!\\)[" + re.escape(chars) + "]", text))


def brace_net(text: str) -> int:
    """Opening minus closing braces as TeX reads them: after \\\\ (a line break) a brace is bare,
    while \\{ and \\} print braces."""
    text = re.sub(r"\\\\", "", text)
    text = re.sub(r"\\[{}]", "", text)
    return text.count("{") - text.count("}")


def check_chunk(src: str, zh: str, holders: dict, match_source: bool = False) -> list[str]:
    """Placeholders each once, no new ones, no new unescaped specials, and braces outside placeholders
    balanced. With `match_source` (chunks split since this rule) the braces only have to leave as many
    groups open as the source chunk does, so a chunk whose source is unbalanced can be copied as is."""
    problems = []
    found = PLACEHOLDER.findall(zh)
    for key in holders:
        count = zh.count(key)
        if count != 1:
            problems.append(f"{key} appears {count} times")
    extra = {f"⟦P{n}⟧" for n in found} - set(holders)
    if extra:
        problems.append(f"unknown placeholders {sorted(extra)}")
    prose = PLACEHOLDER.sub("", zh)
    expected = brace_net(PLACEHOLDER.sub("", src)) if match_source else 0
    if brace_net(prose) != expected:
        problems.append("unbalanced braces outside placeholders" if not match_source else
                        f"braces outside placeholders leave {brace_net(prose)} groups open, the source {expected}")
    if unescaped(prose) > unescaped(PLACEHOLDER.sub("", src)):
        problems.append("new unescaped % & # _ or $ outside placeholders")
    return problems


NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
ENGLISH_RUN = re.compile(r"(?:[A-Za-z][A-Za-z'’-]*[ \t\n,;:]+){7,}[A-Za-z][A-Za-z'’-]*")


def chunk_hints(src: str, zh: str) -> list[str]:
    """What a careful reader would check first, for the agent to look at; never a reason to reject:
    numbers of the source missing from the translation, a different paragraph count, and runs of
    eight or more English words left outside placeholders and outside full-width brackets."""
    src_prose, zh_prose = PLACEHOLDER.sub(" ", src), PLACEHOLDER.sub(" ", zh)
    hints = []
    def numbers(text: str) -> collections.Counter:  # 1,000 = 1000 and 09/2025 = 2025 年 9 月
        return collections.Counter(n.replace(",", "").lstrip("0") or "0" for n in NUMBER.findall(text))
    have = numbers(zh_prose)
    missing = []
    for number, count in numbers(src_prose).items():
        missing += [number] * max(0, count - have[number])
    if missing:
        hints.append(f"numbers missing from the translation: {', '.join(missing[:10])}")
    paragraphs = [len([b for b in re.split(r"\n\s*\n", text) if b.strip()]) for text in (src, zh)]
    if paragraphs[0] != paragraphs[1]:
        hints.append(f"{paragraphs[1]} paragraphs, the source has {paragraphs[0]}")
    bare = re.sub(r"（[^（）]*）", " ", zh_prose)
    runs = [m[0] for m in ENGLISH_RUN.finditer(bare)]
    if runs:
        hints.append(f"{len(runs)} English run(s) of 8+ words left, e.g. {runs[0][:60]!r}")
    return hints


def restore(zh: str, holders: dict) -> str:
    """Put the kept spans back. A span ending in a control word gets a space before a following
    letter: XeTeX treats CJK characters as letters, so \\xspace我们 would read as one command name."""
    def put(m: re.Match) -> str:
        value = holders[m[0]]
        after = zh[m.end():m.end() + 1]
        if re.search(r"\\[A-Za-z@]+\*?$", value) and after and after.isalpha():
            return value + " "
        return value
    return PLACEHOLDER.sub(put, zh)


def apply_overrides(work_dir: Path) -> list[str]:
    """Copy W/overrides/** over W/build after each merge, for hand fixes that merging would overwrite
    (prose hidden in environment options, labels hard-coded in class files, float adjustments).
    A file whose override was removed since the last run goes back to the source version (or away,
    if the source has none); the .tex files that merging rewrites are already fresh."""
    src = work_dir / "overrides"
    copied = []
    for path in sorted(p for p in src.rglob("*") if p.is_file()) if src.is_dir() else []:
        rel = path.relative_to(src)
        (work_dir / "build" / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, work_dir / "build" / rel)
        copied.append(rel.as_posix())
    ledger = work_dir / "overrides-applied.json"
    merged_files = set(read_json(work_dir / "tex.json")["tex_files"])
    for rel in read_json(ledger)["files"] if ledger.is_file() else []:
        if rel in copied or rel in merged_files:
            continue
        original, target = work_dir / "src" / rel, work_dir / "build" / rel
        if original.is_file():
            shutil.copyfile(original, target)
        else:
            target.unlink(missing_ok=True)
    write_json(ledger, {"files": copied})
    return copied


def merge(work_dir: Path) -> dict:
    """Restore translated chunks into W/build; untranslated or reverted chunks keep the source text."""
    work_dir = Path(work_dir)
    record = read_json(work_dir / "tex.json")
    chunks_dir, src, build_dir = work_dir / "chunks", work_dir / "src", work_dir / "build"
    meta = read_json(chunks_dir / "index.json")
    for rel, digest in meta.get("src_sha256", {}).items():
        if sha256(src / rel) != digest:  # merging by stale offsets would silently drop or repeat text
            raise TexError(f"W/src/{rel} changed after tex-split, so the chunk offsets no longer match it; "
                           "restore it and put fixes in W/overrides/")
    index = meta["chunks"]
    reverted = read_json(chunks_dir / "reverted.json") if (chunks_dir / "reverted.json").exists() else {}
    used, problems, placed, hints = [], {}, {}, {}
    per_file: dict[str, list] = {}
    for c in index:
        zh_path = chunks_dir / f"{c['id']}.zh.tex"
        if not zh_path.is_file():
            continue
        zh = read_text(zh_path)
        if c["id"] in reverted and reverted[c["id"]] == hashlib.sha256(zh.encode("utf-8", "surrogateescape")).hexdigest():
            continue  # still the version that failed to compile
        holders = read_json(chunks_dir / f"{c['id']}.map.json")["placeholders"]
        issues = check_chunk(read_text(chunks_dir / f"{c['id']}.src.tex"), zh, holders,
                             meta.get("brace_rule") == "source")
        if issues:
            problems[c["id"]] = issues
            continue
        restored = restore(zh, holders)
        per_file.setdefault(c["file"], []).append((c["start"], c["end"], restored, c["id"]))
        used.append(c["id"])
        found = chunk_hints(read_text(chunks_dir / f"{c['id']}.src.tex"), zh)
        if found:
            hints[c["id"]] = found
    for rel in record["tex_files"]:
        text = read_text(src / rel)
        spans = sorted(per_file.get(rel, []))
        out, pos, lines = [], 0, []
        for a, b, restored, cid in spans:
            out.append(text[pos:a])
            first = "".join(out).count("\n") + 1
            out.append(restored)
            lines.append({"id": cid, "first_line": first, "last_line": first + restored.count("\n")})
            pos = b
        out.append(text[pos:])
        (build_dir / rel).write_text("".join(out), encoding="utf-8", errors="surrogateescape")
        placed[rel] = lines
    write_json(chunks_dir / "merged.json", placed)
    overrides = apply_overrides(work_dir)
    return {"merged": used, "rejected": problems, "reverted": sorted(set(reverted) - set(used)),
            "overrides": overrides, "hints": hints}


def locate(work_dir: Path, rel: str, line: int, main: str) -> str | None:
    placed = read_json(work_dir / "chunks" / "merged.json")
    offset = 1 if rel == main else 0  # patch_main inserts one line after \documentclass
    for item in placed.get(rel, []):
        if item["first_line"] + offset <= line <= item["last_line"] + offset:
            return item["id"]
    return None


def patch_fonts(text: str, where: str, changes: list[str], families: set[str]) -> str:
    """Comment out the font lines of FONT_PATCHES and collect the families zh-setup.tex must
    replace, counting lines an earlier build already commented out (merging rewrites only the
    files that hold chunks). Commenting keeps every line in place, so log line numbers still match."""
    for m in DROPPED.finditer(text):  # package lists an earlier build already split
        for name in m[1].split(","):
            families.update(LISTED_FONTS.get(name, "").split())

    def drop(m: re.Match) -> str:
        names = [n.strip() for n in m[3].split(",")]
        dropped = [n for n in names if n in LISTED_FONTS]
        if not dropped:
            return m[0]
        kept = [n for n in names if n not in LISTED_FONTS]
        changes.append(f"drop {','.join(dropped)} from {m[0].strip()}{where}")
        for name in dropped:
            families.update(LISTED_FONTS[name].split())
        note = f"{MARK} dropped {','.join(dropped)}"
        return f"{m[1]}{{{','.join(kept)}}}{m[4]} {note}" if kept else f"{note}: {m[0]}"
    text = re.sub(rf"^(?!{re.escape(MARK)})([ \t]*{PKG})\{{([^{{}}\n]*,[^{{}}\n]*)\}}([^\n]*)", drop, text, flags=re.M)
    for pattern, replaces in FONT_PATCHES:
        if re.search(rf"^{re.escape(MARK)} [ \t]*{pattern}", text, re.M):
            families.update(replaces.split())

        def comment(m: re.Match, replaces: str = replaces) -> str:
            changes.append(f"comment out {m[0].strip()}{where}")
            families.update(replaces.split())
            return f"{MARK} {m[0]}"
        text = re.sub(rf"^(?!{re.escape(MARK)})[ \t]*{pattern}[^\n]*", comment, text, flags=re.M)
    return text


def patch_theorem_names(text: str, where: str, changes: list[str]) -> str:
    def name(m: re.Match) -> str:
        changes.append(f"name {m[2]} {THEOREM_NAMES[m[2]]}{where}")
        return f"{m[1]}{THEOREM_NAMES[m[2]]}{m[3]}"
    return THEOREM_LINE.sub(lambda m: m[0] if in_comment(text, m.start()) else name(m), text)


def patch_main(build: Path, main: str) -> list[str]:
    """Comment out clashing font lines in the main file and in the source's other .tex, .cls and
    .sty files, keep fontspec away from the template's math fonts, then insert zh-setup.tex after
    \\documentclass, telling it which font families to replace. Idempotent."""
    shutil.copyfile(SETUP, build / "zh-setup.tex")
    changes, families = [], set()
    src = build.parent / "src"
    for other in sorted(build.rglob("*")):
        rel = other.relative_to(build).as_posix()
        if other.suffix in (".tex", ".cls", ".sty") and rel != main and (src / rel).is_file():
            text = read_text(other)
            patched = patch_theorem_names(patch_fonts(text, f" in {rel}", changes, families), f" in {rel}", changes)
            if patched != text:
                other.write_text(patched, encoding="utf-8", errors="surrogateescape")
    path = build / main
    text = patch_theorem_names(patch_fonts(read_text(path), "", changes, families), "", changes)
    line = rf"^[ \t]*(?:{re.escape(NO_MATH)})?\\documentclass(\[[^\]]*\])?\{{[^}}]+\}}[^\n]*\n"
    match = re.search(line, text, re.M)
    if not match:
        raise TexError(f"No \\documentclass line in {main}")
    if NO_MATH not in text:
        # Templates set up their math fonts for pdfLaTeX (libertinust1math, for example); without
        # no-math, fontspec swaps in text fonts and braces, \in or daggers print as wrong glyphs.
        # Same line as \documentclass, so locate() still sees exactly one inserted line.
        at = text.index("\\documentclass", match.start())
        text = text[:at] + NO_MATH + text[at:]
        changes.append("pass no-math to fontspec")
        match = re.search(line, text, re.M)
    if "\\input{zh-setup.tex}" not in text:
        flags = "".join(f"\\def\\Zhouyi{f.capitalize()}{{}}" for f in sorted(families))
        text = text[:match.end()] + flags + "\\input{zh-setup.tex}\n" + text[match.end():]  # one line
        changes.append("insert zh-setup.tex" + (f" replacing {', '.join(sorted(families))} fonts" if families else ""))
    path.write_text(text, encoding="utf-8", errors="surrogateescape")
    return changes


def summarize_log(log: str) -> dict:
    errors = re.findall(r"^(?:\./)?([^\s:]+\.(?:tex|sty|cls|bbl)):(\d+): (.+)$", log, re.M)
    plain = [line for line in log.splitlines() if line.startswith("! ")]
    overfull = [float(x) for x in re.findall(r"^Overfull \\hbox \(([\d.]+)pt too wide\)", log, re.M)]
    overhigh = [float(x) for x in re.findall(r"^Overfull \\vbox \(([\d.]+)pt too high\)", log, re.M)]
    return {
        "errors": [{"file": f, "line": int(n), "message": m} for f, n, m in errors] or [{"message": m} for m in plain],
        "missing_characters": len(re.findall(r"^Missing character: There is no", log, re.M)),
        "overfull_over_20pt": sum(1 for x in overfull if x > 20),
        "overfull_vbox_over_20pt": sum(1 for x in overhigh if x > 20),  # boxes running past the page foot
        "undefined_references": len(re.findall(r"LaTeX Warning: (?:Reference|Citation) .* undefined", log)),
    }


def tex_bin(name: str, texbin: Path | None) -> str:
    path = shutil.which(name, path=str(texbin) if texbin else None)
    if not path:
        raise TexError(f"{name} not found; install TeX Live with XeLaTeX or pass --texbin")
    return path


def compile_once(root: Path, main: str, texbin: Path | None) -> dict:
    env = dict(os.environ)
    if texbin:
        env["PATH"] = f"{texbin}{os.pathsep}{env.get('PATH', '')}"
    # -g: always rebuild; otherwise latexmk keeps reporting an old failure after the environment changes.
    cmd = [tex_bin("latexmk", texbin), "-g", "-xelatex", "-interaction=nonstopmode", "-file-line-error",
           "-halt-on-error", main]
    # latexmk runs in W/build, so the PDF, log and auxiliary files land there under the main file's
    # name even when the main file sits in a subdirectory such as texFiles/arxiv.tex.
    stem = Path(main).stem
    log_path = root / f"{stem}.log"
    # A failed run can leave a truncated .aux; BibTeX then stops before XeLaTeX runs and the old log
    # would be read as this run's result. Start each compile without the old log and auxiliary files.
    for suffix in (".log", ".aux", ".bbl", ".blg", ".fdb_latexmk", ".fls", ".xdv"):
        name = f"{stem}{suffix}"
        if suffix == ".bbl" and (root.parent / "src" / name).is_file():
            continue  # a .bbl shipped in the source package is the bibliography itself
        root.joinpath(name).unlink(missing_ok=True)
    # latexmk can echo non-UTF-8 bytes from the TeX log; decode leniently instead of crashing.
    run = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, errors="replace", timeout=1800)
    summary = summarize_log(log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else run.stdout)
    pdf = root / f"{stem}.pdf"
    summary.update(ok=run.returncode == 0 and pdf.is_file(), returncode=run.returncode,
                   pdf=str(pdf) if pdf.is_file() else "")
    return summary


def revert(work_dir: Path, cid: str, error: dict) -> None:
    """Fall back to the English source for one chunk; its section returns to pending with an open issue."""
    chunks = work_dir / "chunks"
    reverted = read_json(chunks / "reverted.json") if (chunks / "reverted.json").exists() else {}
    zh = (chunks / f"{cid}.zh.tex").read_bytes()
    reverted[cid] = hashlib.sha256(zh).hexdigest()  # the failed translation stays on disk for repair
    write_json(chunks / "reverted.json", reverted)
    progress = read_json(work_dir / "progress.json")
    for section in progress["sections"]:
        if section.get("chunk") == cid:
            section["status"] = "pending"
    progress["issues"].append({
        "location": cid, "status": "open",
        "detail": f"Compile error at {error['file']}:{error['line']}: {error['message']}; "
                  f"the chunk was reverted to English. Fix chunks/{cid}.zh.tex and rebuild.",
    })
    write_json(work_dir / "progress.json", progress)


def untouched_line(work_dir: Path, error: dict) -> bool:
    """Whether the failing line reads exactly as in the source and holds no Chinese: the translation
    did not write it, so the error comes from elsewhere (the preamble, W/overrides/, a hook run at
    \\begin{document} or \\maketitle) and reverting the chunk around it would not help."""
    built, source = work_dir / "build" / error["file"], work_dir / "src" / error["file"]
    if not built.is_file() or not source.is_file():
        return False
    lines = read_text(built).splitlines()
    line = lines[error["line"] - 1].strip() if 0 < error["line"] <= len(lines) else ""
    return bool(line) and not CJK.search(line) and line in {l.strip() for l in read_text(source).splitlines()}


def build(work_dir: Path, output: Path | None = None, texbin: Path | None = None, rounds: int = 5,
          retry_reverted: bool = False) -> dict:
    """Merge translated chunks, add Chinese support and compile; a chunk that breaks the build falls
    back to English, at most `rounds` times. Errors that cannot be traced to a chunk stop the loop.
    `retry_reverted` merges reverted chunks again, after a revert turned out to be a misattribution."""
    work_dir = Path(work_dir)
    record = read_json(work_dir / "tex.json")
    root, main = work_dir / "build", record["main"]
    has_chunks = (work_dir / "chunks" / "index.json").is_file()
    reverted_now, merged = [], None
    if retry_reverted:
        (work_dir / "chunks" / "reverted.json").unlink(missing_ok=True)
    for attempt in range(rounds + 1):
        if has_chunks:
            merged = merge(work_dir)
        else:
            apply_overrides(work_dir)
        changes = patch_main(root, main)
        summary = compile_once(root, main, texbin)
        if summary["ok"] or not has_chunks:
            break
        error = next((e for e in summary["errors"] if "file" in e), None)
        cid = locate(work_dir, error["file"], error["line"], main) if error else None
        if cid is None or cid not in merged["merged"]:
            # Only a translated chunk can be reverted; anything else needs a human look.
            summary["stopped"] = "error outside translated text or without file:line; fix it by hand"
            break
        if untouched_line(work_dir, error):
            summary["stopped"] = (f"error on a line the translation did not change ({error['file']}:{error['line']}, "
                                  f"inside chunk {cid}); it likely comes from the preamble, W/overrides/ or a hook; "
                                  "fix it by hand")
            break
        if attempt == rounds:
            summary["stopped"] = f"still failing after reverting {rounds} chunks"
            break
        revert(work_dir, cid, error)
        reverted_now.append({"chunk": cid, **error})
    summary.update(patches=changes, reverted_this_run=reverted_now, merge=merged)
    if summary["ok"] and output is not None:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(summary["pdf"], output)
        summary["output"] = str(output)
        summary["output_sha256"] = sha256(Path(output))
    write_json(work_dir / "build-summary.json", summary)
    return summary


def deliver(paper_dir: Path, work_dir: Path, pdf_name: str | None = None, tex_dir: str = "zh-tex") -> dict:
    """Copy the built PDF and a recompilable Chinese project (source files plus zh-setup.tex) into the paper directory."""
    paper_dir, work_dir = Path(paper_dir), Path(work_dir)
    summary = read_json(work_dir / "build-summary.json")
    if not summary.get("ok"):
        raise TexError("The last build failed; run tex-build first")
    pdf_out = paper_dir / (pdf_name or f"{paper_dir.name}.zh.pdf")
    shutil.copyfile(summary["pdf"], pdf_out)
    target = paper_dir / tex_dir
    if target.exists():
        if not (target / "zh-setup.tex").is_file():
            raise TexError(f"{target} exists and was not written by zhouyi; move it first")
        shutil.rmtree(target)
    src, build_dir, overrides = work_dir / "src", work_dir / "build", work_dir / "overrides"
    files = {p.relative_to(src) for p in src.rglob("*") if p.is_file()}
    if overrides.is_dir():  # also files the source lacks, such as a local stub for a font package
        files |= {p.relative_to(overrides) for p in overrides.rglob("*") if p.is_file()}
    files = sorted(files) + [Path("zh-setup.tex")]
    for rel in files:
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(build_dir / rel, target / rel)
    readme = target / "00README.json"
    if readme.is_file():  # arXiv metadata names the compiler; the Chinese project needs XeLaTeX
        meta = json.loads(readme.read_text(encoding="utf-8"))
        if isinstance(meta.get("process"), dict):
            meta["process"]["compiler"] = "xelatex"
            write_json(readme, meta)
    return {"pdf": str(pdf_out), "pdf_sha256": sha256(pdf_out), "tex_dir": str(target), "files": len(files)}


def check(paper_dir: Path, work_dir: Path, pdf_name: str | None = None, tex_dir: str = "zh-tex") -> dict:
    paper_dir, work_dir = Path(paper_dir), Path(work_dir)
    record = read_json(work_dir / "tex.json")
    errors, pending, warnings = [], [], []
    pdf = paper_dir / (pdf_name or f"{paper_dir.name}.zh.pdf")
    if not pdf.is_file() or not pdf.read_bytes()[:5] == b"%PDF-":
        errors.append(f"Missing or invalid PDF: {pdf.name}")
    if not (paper_dir / tex_dir / record["main"]).is_file():
        errors.append(f"Missing Chinese project: {tex_dir}/{record['main']}")
    summary = read_json(work_dir / "build-summary.json") if (work_dir / "build-summary.json").is_file() else {}
    if not summary.get("ok") or summary.get("errors"):
        errors.append("Last build failed or reported errors")
    if summary.get("missing_characters"):
        errors.append(f"{summary['missing_characters']} missing characters in the build log")
    for key in ("undefined_references", "overfull_over_20pt", "overfull_vbox_over_20pt"):
        if summary.get(key):
            warnings.append(f"{key}: {summary[key]}")
    index = read_json(work_dir / "chunks" / "index.json")["chunks"] if (work_dir / "chunks" / "index.json").is_file() else []
    merged = set((summary.get("merge") or {}).get("merged", []))
    pending += [f"Chunk not in the build (untranslated, rejected or reverted): {c['id']}" for c in index if c["id"] not in merged]
    progress = read_json(work_dir / "progress.json")
    pending += [f"Section not verified: {s.get('id')}" for s in progress["sections"] if s.get("status") != "verified"]
    pending += [f"Unresolved issue: {i.get('location')}" for i in progress["issues"] if i.get("status") not in {"resolved", "noted"}]
    review = progress["document_review"]
    if review.get("status") != "verified" or not str(review.get("note", "")).strip():
        pending.append("Layout and document review pending")
    return {"files_ok": not errors, "ready": not errors and not pending,
            "errors": errors, "pending_reviews": pending, "warnings": warnings}


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("tex-prepare", help="Unpack an arXiv source package into W/src and W/build")
    p.add_argument("tarball", type=Path)
    p.add_argument("--work-dir", required=True, type=Path)
    p.add_argument("--main", help="Main .tex file, relative to the package root, when the package "
                   "holds several top-level documents")
    p = sub.add_parser("tex-split", help="Cut the source into per-section chunks with ⟦Pn⟧ placeholders")
    p.add_argument("--work-dir", required=True, type=Path)
    p.add_argument("--max-chars", type=int, default=14000,
                   help="Prose characters per chunk before a section is cut at \\subsection (about 3-4K tokens)")
    p.add_argument("--verbatim-env", action="append", default=[],
                   help="Comma-separated environments to keep whole as verbatim, beyond those detected")
    p = sub.add_parser("tex-merge", help="Check translated chunks and restore them into W/build")
    p.add_argument("--work-dir", required=True, type=Path)
    p = sub.add_parser("tex-build", help="Merge chunks, add Chinese support and compile with XeLaTeX")
    p.add_argument("--work-dir", required=True, type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--texbin", type=Path, help="Directory holding latexmk and xelatex")
    p.add_argument("--rounds", type=int, default=5, help="Most chunks to revert to English in one run")
    p.add_argument("--retry-reverted", action="store_true",
                   help="Merge reverted chunks again, once the error behind a revert has been fixed elsewhere")
    for command in ("tex-deliver", "tex-check"):
        p = sub.add_parser(command, help="Copy the PDF and Chinese project into the paper directory"
                           if command == "tex-deliver" else "Check the delivery and review records")
        p.add_argument("paper_dir", type=Path)
        p.add_argument("--work-dir", required=True, type=Path)
        p.add_argument("--pdf-name", help="Default: <paper dir name>.zh.pdf")
        p.add_argument("--tex-dir", default="zh-tex")
    return ap


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "tex-prepare":
            result = prepare(args.tarball, args.work_dir, args.main)
        elif args.command == "tex-split":
            result = split(args.work_dir, args.max_chars,
                           [e for arg in args.verbatim_env for e in arg.split(",")])
        elif args.command == "tex-merge":
            result = merge(args.work_dir)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result["rejected"] else 0
        elif args.command == "tex-deliver":
            result = deliver(args.paper_dir, args.work_dir, args.pdf_name, args.tex_dir)
        elif args.command == "tex-check":
            result = check(args.paper_dir, args.work_dir, args.pdf_name, args.tex_dir)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["files_ok"] else 1
        else:
            result = build(args.work_dir, args.out, args.texbin, args.rounds, args.retry_reverted)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 1
    except (TexError, OSError, ValueError, tarfile.TarError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

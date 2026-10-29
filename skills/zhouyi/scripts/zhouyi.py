#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pymupdf>=1.26,<2", "pillow>=11,<13"]
# ///
"""PDF preparation, original-page crops, contact sheets, delivery checks and packaging for Zhouyi."""

from __future__ import annotations

import argparse
import collections
from datetime import datetime, timezone
import difflib
import hashlib
import io
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unicodedata
import urllib.error
from urllib.parse import unquote, urlsplit
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

import pymupdf
from PIL import Image, ImageDraw, ImageFont


class ZhouyiError(ValueError):
    """An actionable input or workflow error."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".zhouyi-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def write_json(path: Path, data: dict) -> None:
    write_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ZhouyiError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ZhouyiError(f"Expected a JSON object: {path}")
    return data


def open_pdf(path: Path):
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise ZhouyiError(f"Cannot open PDF {path}: {exc}") from exc
    if not doc.is_pdf or doc.needs_pass or doc.page_count == 0:
        doc.close()
        raise ZhouyiError("Input must be a readable, unlocked PDF with at least one page")
    return doc


def load_progress(work_dir: Path, source_hash: str | None = None) -> dict:
    state = read_json(Path(work_dir) / "progress.json")
    if state.get("schema_version") != 1:
        raise ZhouyiError("Unsupported progress schema; expected schema_version 1")
    source = state.get("source")
    if not isinstance(source, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(source.get("sha256", ""))):
        raise ZhouyiError("Progress record has no valid source SHA-256")
    if source_hash is not None and source.get("sha256") != source_hash:
        raise ZhouyiError("Source PDF differs from the saved progress; use the original PDF or a new work directory")
    for key, kind in [("sections", list), ("glossary", dict), ("assets", dict), ("issues", list), ("document_review", dict)]:
        if not isinstance(state.get(key), kind):
            raise ZhouyiError(f"Progress field {key} must be a {kind.__name__}")
    return state


def reading_text(pages: list[dict]) -> str:
    """Page text in PDF block order, so two-column pages are not interleaved line by line."""
    parts = []
    for page in pages:
        blocks = [b["text"].strip() for b in page["text_blocks_unrotated"] if b["text"].strip()]
        body = "\n\n".join(blocks) if blocks else "[No text layer: read this page from its preview]"
        parts.append(f"===== PAGE {page['page']} =====\n\n{body}\n")
    return "\n".join(parts)


def inspect_pdf(source: Path, work_dir: Path | None = None) -> dict:
    source = Path(source)
    digest = sha256(source)
    if work_dir is not None and (Path(work_dir) / "progress.json").exists():
        load_progress(Path(work_dir), digest)
    with open_pdf(source) as doc:
        pages = []
        for number, page in enumerate(doc, 1):
            text = page.get_text("text", sort=True)
            blocks = [
                {"bbox": list(block[:4]), "text": block[4]}
                for block in page.get_text("blocks") if block[6] == 0
            ]
            pages.append({
                "page": number, "width_pt": page.rect.width,
                "height_pt": page.rect.height, "rotation": page.rotation,
                "text_characters": len(text.strip()), "needs_visual_reading": not text.strip(),
                "text": text, "text_blocks_unrotated": blocks,
            })
        result = {"source": {"filename": source.name, "sha256": digest, "page_count": len(pages)},
                  "metadata": doc.metadata, "pages": pages}
    if work_dir is not None:
        work_dir = Path(work_dir)
        write_json(work_dir / "source.json", result)
        write_bytes(work_dir / "source.txt", reading_text(pages).encode())
        if not (work_dir / "progress.json").exists():
            write_json(work_dir / "progress.json", {
                "schema_version": 1, "source": result["source"],
                "metadata": {}, "sections": [], "glossary": {}, "assets": {}, "issues": [],
                "document_review": {"status": "pending", "markdown_sha256": "", "note": ""},
            })
    return result


def parse_pages(spec: str, count: int) -> list[int]:
    if spec == "all":
        return list(range(1, count + 1))
    pages = []
    try:
        for item in spec.split(","):
            parts = item.strip().split("-")
            if len(parts) == 1:
                values = [int(parts[0])]
            elif len(parts) == 2:
                start, end = map(int, parts)
                if not 1 <= start <= end <= count:
                    raise ValueError()
                values = range(start, end + 1)
            else:
                raise ValueError()
            for page in values:
                if not 1 <= page <= count:
                    raise ValueError()
                if page not in pages:
                    pages.append(page)
    except ValueError as exc:
        raise ZhouyiError(f"Pages must be within 1..{count}, e.g. 1,3-5 or all") from exc
    return pages


def page_image(doc, number: int, dpi: int) -> Image.Image:
    if not 1 <= number <= doc.page_count:
        raise ZhouyiError(f"Page {number} is outside 1..{doc.page_count}")
    if not 1 <= dpi <= 1200:
        raise ZhouyiError("DPI must be within 1..1200")
    pix = doc[number - 1].get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def png_bytes(image: Image.Image, dpi: int) -> bytes:
    stream = io.BytesIO()
    image.save(stream, format="PNG", dpi=(dpi, dpi))
    return stream.getvalue()


def render_pdf(source: Path, pages: str, output_dir: Path, dpi: int = 120) -> list[dict]:
    rendered = []
    with open_pdf(source) as doc:
        for number in parse_pages(pages, doc.page_count):
            image = page_image(doc, number, dpi)
            path = Path(output_dir) / f"page_{number:03d}.png"
            write_bytes(path, png_bytes(image, dpi))
            rendered.append({"page": number, "path": str(path), "width": image.width, "height": image.height, "dpi": dpi})
    return rendered


def make_sheets(images: list[Path], output_dir: Path, per: int = 4, cols: int = 2,
                max_side: int = 1600, prefix: str = "sheet", fmt: str = "jpg") -> list[dict]:
    """Tile previews or crops into labelled contact sheets, so one image view covers several files."""
    if not images:
        raise ZhouyiError("Give at least one image")
    if not 1 <= cols <= per <= 16 or not 200 <= max_side <= 8000:
        raise ZhouyiError("Use 1 <= cols <= per <= 16 and 200 <= max-side <= 8000")
    if fmt not in {"jpg", "png"}:
        raise ZhouyiError("Sheet format must be jpg or png")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", prefix):
        raise ZhouyiError("Prefix may contain letters, digits and _.+-")
    rows = math.ceil(per / cols)
    label_h, gap = 32, 8
    cell_w = (max_side - gap * (cols - 1)) // cols
    cell_h = (max_side - gap * (rows - 1)) // rows - label_h
    font = ImageFont.load_default(size=22)
    sheets = []
    for start in range(0, len(images), per):
        group = [Path(p) for p in images[start:start + per]]
        tiles = []
        for path in group:
            try:
                with Image.open(path) as source:
                    tile = source.convert("RGB")
            except OSError as exc:
                raise ZhouyiError(f"Cannot read image {path}: {exc}") from exc
            tile.thumbnail((cell_w - 2, cell_h - 2))
            tiles.append(tile)
        # Size columns and rows to their largest tile, so the sheet carries no empty margins.
        used_cols, used_rows = min(cols, len(tiles)), math.ceil(len(tiles) / cols)
        col_w = [max(t.width for t in tiles[c::cols]) + 2 for c in range(used_cols)]
        row_h = [max(t.height for t in tiles[r * cols:(r + 1) * cols]) + 2 + label_h for r in range(used_rows)]
        canvas = Image.new("RGB", (sum(col_w) + gap * (used_cols - 1), sum(row_h) + gap * (used_rows - 1)), "white")
        draw = ImageDraw.Draw(canvas)
        for index, (path, tile) in enumerate(zip(group, tiles)):
            x = sum(col_w[:index % cols]) + gap * (index % cols)
            y = sum(row_h[:index // cols]) + gap * (index // cols)
            draw.text((x + 4, y + 4), path.name, fill="black", font=font)
            canvas.paste(tile, (x + 1, y + label_h + 1))
            draw.rectangle((x, y + label_h, x + tile.width + 1, y + label_h + tile.height + 1), outline=(160, 160, 160))
        output = Path(output_dir) / f"{prefix}_{start // per + 1:02d}.{fmt}"
        if fmt == "png":
            write_bytes(output, png_bytes(canvas, 72))
        else:
            # JPEG keeps each sheet a few hundred KB; images stay in the context and every request resends them.
            stream = io.BytesIO()
            canvas.save(stream, format="JPEG", quality=85)
            write_bytes(output, stream.getvalue())
        sheets.append({"path": str(output), "width": canvas.width, "height": canvas.height,
                       "members": [str(p) for p in group]})
    return sheets


def page_texts(source: Path) -> list[str]:
    """Each page's text in a comparable form: NFKC (ligatures, full-width forms), no line-break
    hyphens, no whitespace."""
    with open_pdf(source) as doc:
        texts = [doc[i].get_text() for i in range(doc.page_count)]
    return ["".join(unicodedata.normalize("NFKC", re.sub(r"-\n", "", t)).split()) for t in texts]


def main_fonts(source: Path, top: int = 3) -> list[dict]:
    """The fonts that set the most characters: a build that fell back to a default font (for
    example Latin Modern instead of a Times clone) shows up here even when its text is identical."""
    counts: collections.Counter = collections.Counter()
    with open_pdf(source) as doc:
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        counts[span["font"]] += len(span["text"].strip())
    total = sum(counts.values()) or 1
    return [{"font": f, "share": round(n / total, 3)} for f, n in counts.most_common(top)]


def compare_pdfs(original: Path, built: Path, threshold: float = 0.85, window: int = 2) -> dict:
    """Page-by-page text check of an English build against the original PDF, before translating.
    For each original page, `coverage` is the share of its text found in the best pair of
    consecutive built pages within `window` pages, so floats that moved or text that flowed onto
    the next page do not count. Characters that appear only in the build, apart from Chinese
    labels, usually mean a wrong font or glyph."""
    a, b = page_texts(original), page_texts(built)
    pages = []
    for i, text in enumerate(a):
        best = (0.0, None)
        for j in range(max(0, i - window), min(len(b), i + window + 1)):
            joined = b[j] + (b[j + 1] if j + 1 < len(b) else "")
            if not text:
                best = max(best, (1.0, j)); continue
            m = difflib.SequenceMatcher(None, text, joined, autojunk=False)
            if m.quick_ratio() * (len(text) + len(joined)) / 2 / len(text) < best[0]:
                continue  # cannot beat the best pair so far
            best = max(best, (sum(block.size for block in m.get_matching_blocks()) / len(text), j))
        pages.append({"page": i + 1, "match": None if best[1] is None else best[1] + 1, "coverage": round(best[0], 3)})
    seen = set("".join(a))
    only_built = collections.Counter(c for c in "".join(b) if c not in seen and not re.match(r"[\u3000-\u9fff\uff00-\uffef]", c))
    return {"pages": {"original": len(a), "built": len(b)},
            "main_fonts": {"original": main_fonts(original), "built": main_fonts(built)},
            "flagged": [p for p in pages if p["coverage"] < threshold],
            "new_characters": dict(only_built.most_common(20)),
            "per_page": pages}


SHIPOUT = re.compile(r"(?<![\w.])\[(\d+)(?=[\s\]{<]|$)")


def log_problem_pages(log: str) -> dict[int, set[str]]:
    """Pages of overfull boxes past 20pt and missing glyphs, read from the TeX log by counting
    page shipouts ([1], [2] ...) seen before each message."""
    found: dict[int, set[str]] = collections.defaultdict(set)
    current = 1
    for line in log.splitlines():
        m = re.match(r"Overfull \\(h|v)box \(([\d.]+)pt too (wide|high)\)", line)
        if m and float(m[2]) > 20:
            found[current].add("overfull")
        if line.startswith("Missing character: There is no"):
            found[current].add("missing glyph")
        for shipped in SHIPOUT.findall(line):
            current = int(shipped) + 1
    return found


def review_sheet(pdf: Path, output_dir: Path, log: Path | None = None, max_pages: int = 8) -> dict:
    """One contact sheet of the pages worth a look before delivery: the first page and the pages
    with overfull boxes or missing glyphs in the TeX log. Untranslated text is caught earlier, by
    the per-chunk hints of tex-merge."""
    with open_pdf(pdf) as doc:
        count = doc.page_count
    reasons: dict[int, set[str]] = collections.defaultdict(set)
    reasons[1].add("first page")
    if log is not None:
        for page, why in log_problem_pages(Path(log).read_text(encoding="utf-8", errors="replace")).items():
            if 1 <= page <= count:
                reasons[page] |= why
    chosen = sorted(reasons, key=lambda p: (p != 1, -len(reasons[p]), p))[:max_pages]
    chosen.sort()
    output_dir = Path(output_dir)
    rendered = render_pdf(pdf, ",".join(map(str, chosen)), output_dir / "pages", 80)
    sheets = make_sheets([Path(r["path"]) for r in rendered], output_dir, per=len(chosen),
                         cols=min(3, len(chosen)), max_side=2000, prefix="review")
    return {"page_count": count, "pages": [{"page": p, "reasons": sorted(reasons[p])} for p in chosen],
            "not_shown": max(0, len(reasons) - len(chosen)), "sheets": sheets}


# ---- Work directories: status, cleaning, fetching ------------------------------------------------

BUILD_LEFTOVERS = {".aux", ".log", ".out", ".toc", ".xdv", ".fls", ".fdb_latexmk", ".blg", ".bcf", ".lof", ".lot",
                   ".synctex.gz", ".run.xml", ".nav", ".snm", ".vrb"}


def tree_size(path: Path) -> int:
    return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file()) if Path(path).exists() else 0


def last_change(path: Path) -> float:
    times = [p.stat().st_mtime for p in Path(path).rglob("*") if p.is_file()]
    return max(times, default=Path(path).stat().st_mtime)


def tool_notes(work: Path) -> list[dict]:
    """First comment line of each script in tools/: what it does and when to rerun it."""
    notes = []
    for path in sorted(p for p in (work / "tools").glob("*") if p.is_file()) if (work / "tools").is_dir() else []:
        first = next((l.strip().lstrip("#/-; ").strip() for l in path.read_text(errors="replace").splitlines()
                      if l.strip() and not l.startswith("#!")), "")
        notes.append({"path": path.name, "note": first[:160]})
    return notes


def paper_status(root: Path, pid: str) -> dict:
    """Mode, stage and progress of one paper from its own work records; nothing is recomputed."""
    work = root / ".zhouyi" / pid
    paper = root / pid
    entry = {"id": pid, "modes": [], "stage": "", "progress": "", "open_issues": 0}
    tex = work / "tex"
    if (tex / "tex.json").is_file():
        entry["modes"].append("tex")
        index = read_json(tex / "chunks" / "index.json") if (tex / "chunks" / "index.json").is_file() else None
        summary = read_json(tex / "build-summary.json") if (tex / "build-summary.json").is_file() else {}
        done = len(list((tex / "chunks").glob("*.zh.tex"))) if index else 0
        total = len(index["chunks"]) if index else 0
        entry["progress"] = f"{done}/{total} chunks" if index else ""
        if (paper / f"{pid}.zh.pdf").is_file():
            entry["stage"] = "delivered"
        elif summary.get("ok") and done and done == total:
            entry["stage"] = "built"
        elif done:
            entry["stage"] = "translating"
        elif index:
            entry["stage"] = "split"
        else:
            entry["stage"] = "prepared"
        if (tex / "progress.json").is_file():
            entry["open_issues"] += sum(1 for i in read_json(tex / "progress.json").get("issues", []) if i.get("status") == "open")
    if (work / "progress.json").is_file() and read_json(work / "progress.json").get("mode") != "tex":
        state = read_json(work / "progress.json")
        entry["modes"].append("pdf")
        sections = state.get("sections", [])
        done = sum(1 for s in sections if s.get("status") == "verified")
        entry["open_issues"] += sum(1 for i in state.get("issues", []) if i.get("status") == "open")
        if not entry["stage"]:
            entry["progress"] = f"{done}/{len(sections)} sections verified"
            entry["stage"] = ("delivered" if (paper / f"{pid}.md").is_file() and state.get("document_review", {}).get("status") == "verified"
                              else "translating" if sections else "inspected")
    categories: collections.Counter = collections.Counter()
    for record in (tex / "progress.json", work / "progress.json"):
        if record.is_file():
            categories.update(i["category"] for i in read_json(record).get("issues", []) if i.get("category"))
    entry["issues_by_category"] = dict(categories)
    changed = last_change(work)
    entry.update(last_activity=datetime.fromtimestamp(changed, timezone.utc).isoformat(timespec="seconds"),
                 minutes_ago=int((time.time() - changed) // 60), size_mb=round(tree_size(work) / 2**20, 1),
                 tools=tool_notes(tex if "tex" in entry["modes"] else work))
    return entry


def status(root: Path) -> dict:
    root = Path(root)
    base = root / ".zhouyi"
    if not base.is_dir():
        raise ZhouyiError(f"No .zhouyi/ under {root}")
    papers = [paper_status(root, d.name) for d in sorted(base.iterdir())
              if d.is_dir() and ((d / "progress.json").is_file() or (d / "tex" / "tex.json").is_file())]
    return {"root": str(root), "papers": papers}


def clean(work: Path, dry_run: bool = False) -> dict:
    """Remove what can be made again: every tmp/ under the paper's work directory and LaTeX
    leftovers in tex/build. Sources, chunks, overrides, tools and work records stay."""
    work = Path(work)
    if not work.is_dir():
        raise ZhouyiError(f"No such work directory: {work}")
    targets = [p for p in work.rglob("tmp") if p.is_dir()]
    build = work / "build" if (work / "build").is_dir() else work / "tex" / "build"
    if build.is_dir():
        src = build.parent / "src"
        for p in build.rglob("*"):
            rel = p.relative_to(build)
            if not p.is_file() or (src / rel).exists():
                continue  # files of the source package stay
            if p.suffix in BUILD_LEFTOVERS or "".join(p.suffixes[-2:]) in BUILD_LEFTOVERS or (p.suffix == ".pdf" and p.parent == build):
                targets.append(p)
    freed = sum(tree_size(p) if p.is_dir() else p.stat().st_size for p in targets)
    if not dry_run:
        for p in targets:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    return {"work_dir": str(work), "removed": [str(p.relative_to(work)) for p in targets][:50],
            "count": len(targets), "freed_mb": round(freed / 2**20, 1), "dry_run": dry_run}


ARXIV_API = "https://export.arxiv.org/api/query?id_list="
ARXIV_SITE = "https://arxiv.org"
ATOM = {"a": "http://www.w3.org/2005/Atom"}


def http_get(url: str, tries: int = 3) -> tuple[int, bytes]:
    """Status and body; 4xx answers are returned, network errors retried with a short backoff."""
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "zhouyi (paper translation skill)"})
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return exc.code, exc.read()
            last = exc
        except (urllib.error.URLError, OSError) as exc:
            last = exc
        time.sleep(2 * (attempt + 1))
    raise ZhouyiError(f"Cannot reach {url}: {last}; download the PDF yourself and pass the local file")


def fetch(ref: str, root: Path, version: int | None = None) -> dict:
    """Download an arXiv PDF and the source of the same version into ROOT/.zhouyi/downloads/."""
    m = re.search(r"(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v(\d+))?", ref)
    if not m:
        raise ZhouyiError(f"Not an arXiv identifier or link: {ref}")
    arxiv = m[1]
    code, body = http_get(ARXIV_API + arxiv)
    entry = ET.fromstring(body).find("a:entry", ATOM) if code == 200 else None
    if entry is None or entry.find("a:id", ATOM) is None:
        raise ZhouyiError(f"arXiv has no record for {arxiv}")
    latest = int(re.search(r"v(\d+)$", entry.find("a:id", ATOM).text.strip())[1])
    version = version or (int(m[2]) if m[2] else latest)
    out = Path(root) / ".zhouyi" / "downloads"
    out.mkdir(parents=True, exist_ok=True)
    name = f"{arxiv.replace('/', '_')}v{version}"
    code, pdf = http_get(f"{ARXIV_SITE}/pdf/{arxiv}v{version}")
    if code != 200 or not pdf.startswith(b"%PDF"):
        raise ZhouyiError(f"No PDF for {arxiv}v{version} (HTTP {code})")
    pdf_path = out / f"{name}.pdf"
    write_bytes(pdf_path, pdf)
    code, src = http_get(f"{ARXIV_SITE}/src/{arxiv}v{version}")
    source = {"type": "none", "path": None, "sha256": None}
    if code == 200 and src.startswith(b"\x1f\x8b"):
        try:
            with tarfile.open(fileobj=io.BytesIO(src), mode="r:gz"):
                kind, suffix = "tar.gz", ".src.tar.gz"
        except tarfile.TarError:
            kind, suffix = "gz", ".src.gz"  # a single gzipped .tex
        src_path = out / f"{name}{suffix}"
        write_bytes(src_path, src)
        source = {"type": kind, "path": str(src_path), "sha256": sha256(src_path)}
    elif code == 200 and src.startswith(b"%PDF"):
        source["type"] = "pdf-only"
    elif code == 403:
        source["type"] = "not public"
    published = entry.find("a:published", ATOM).text.strip()
    return {"arxiv": arxiv, "version": version, "latest": latest,
            "title": " ".join(entry.find("a:title", ATOM).text.split()),
            "authors": [a.find("a:name", ATOM).text for a in entry.findall("a:author", ATOM)],
            "first_published": published[:10], "yymm": published[2:4] + published[5:7],
            "pdf": {"path": str(pdf_path), "sha256": sha256(pdf_path)}, "source": source}


MD2PDF = Path(__file__).resolve().parent.parent / "assets" / "md2pdf"


def md2pdf(paper_dir: Path, work_dir: Path | None = None, texbin: Path | None = None,
           pandoc: str | None = None) -> dict:
    """Typeset <ID>.md and assets/ into <ID>.zh.pdf: pandoc (filter.lua, template.tex) writes the
    LaTeX, XeLaTeX compiles it twice; no model call. The build happens in the work directory's tmp/."""
    paper_dir = Path(paper_dir).resolve()
    md = paper_dir / f"{paper_dir.name}.md"
    if not md.is_file():
        raise ZhouyiError(f"Missing Markdown: {md}")
    pandoc = pandoc or shutil.which("pandoc")
    xelatex = shutil.which("xelatex", path=str(texbin) if texbin else None)
    if not pandoc or not xelatex:
        raise ZhouyiError("md2pdf needs pandoc (2.0 or later) and XeLaTeX; deliver the Markdown without the PDF and say so")
    work_dir = Path(work_dir) if work_dir else paper_dir.parent / ".zhouyi" / paper_dir.name
    build = work_dir / "tmp" / "md2pdf"
    build.mkdir(parents=True, exist_ok=True)
    tex = build / f"{paper_dir.name}.zh.tex"
    title = next((l[2:].strip() for l in md.read_text(encoding="utf-8").splitlines() if l.startswith("# ")), paper_dir.name)
    subprocess.run([pandoc, md.name, "-f", "markdown-smart-auto_identifiers-implicit_figures", "-t", "latex",
                    "--template", str(MD2PDF / "template.tex"), "--lua-filter", str(MD2PDF / "filter.lua"),
                    "--no-highlight", "--columns=20", "-V", f"resource-dir={paper_dir}",
                    "-V", f"pdf-title={title}", "-o", str(tex)], cwd=paper_dir, check=True, capture_output=True)
    env = dict(os.environ)
    if texbin:
        env["PATH"] = f"{texbin}{os.pathsep}{env.get('PATH', '')}"
    for _ in range(2):  # the second run settles page references and the header marks
        run = subprocess.run([xelatex, "-interaction=nonstopmode", "-halt-on-error", tex.name], cwd=build,
                             env=env, capture_output=True, text=True, errors="replace", timeout=1800)
        if run.returncode:
            log = tex.with_suffix(".log").read_text(encoding="utf-8", errors="replace").splitlines()
            first = next((i for i, l in enumerate(log) if l.startswith("!")), None)
            where = " / ".join(l.strip() for l in log[first:first + 4] if l.strip()) if first is not None else ""
            raise ZhouyiError(f"XeLaTeX failed: {where[:300]} (log: {tex.with_suffix('.log')}); usually a defect in "
                              "the Markdown such as math outside $...$; fix it, or deliver without the PDF and say so")
    log = tex.with_suffix(".log").read_text(encoding="utf-8", errors="replace")
    out = paper_dir / f"{paper_dir.name}.zh.pdf"
    shutil.copyfile(tex.with_suffix(".pdf"), out)
    with open_pdf(out) as doc:
        pages = doc.page_count
    return {"pdf": str(out), "pages": pages, "log": str(tex.with_suffix(".log")),
            "missing_characters": len(re.findall(r"^Missing character: There is no", log, re.M)),
            "overfull_over_20pt": sum(1 for x in re.findall(r"^Overfull \\hbox \(([\d.]+)pt too wide", log, re.M) if float(x) > 20)}


def crop_pdf(source: Path, page: int, box: list[float], output: Path,
             work_dir: Path, dpi: int = 300, object_id: str | None = None) -> dict:
    if len(box) != 4 or not all(math.isfinite(x) for x in box):
        raise ZhouyiError("Box must contain four finite normalized coordinates")
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ZhouyiError("Box must satisfy 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1")
    output, work_dir = Path(output), Path(work_dir)
    if output.suffix.lower() != ".png" or output.parent.name != "assets":
        raise ZhouyiError("Crop output must be a PNG directly inside the paper's assets/ directory")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*\.png", output.name):
        raise ZhouyiError("Use an asset name such as fig_01.png or table_09_p02.png")
    digest = sha256(source)
    state = load_progress(work_dir, digest)
    paper_id = output.parent.parent.name
    if state.get("paper_id", paper_id) != paper_id:
        raise ZhouyiError("This work directory is associated with a different paper directory")
    with open_pdf(source) as doc:
        image = page_image(doc, page, dpi)
        pixels = [math.floor(x0 * image.width), math.floor(y0 * image.height),
                  math.ceil(x1 * image.width), math.ceil(y1 * image.height)]
        cropped = image.crop(pixels)
        data = png_bytes(cropped, dpi)
    record = {
        "object_id": object_id or output.stem, "source_sha256": digest,
        "page": page, "box": list(box), "pixel_box": pixels, "dpi": dpi,
        "width": cropped.width, "height": cropped.height,
        "sha256": hashlib.sha256(data).hexdigest(),
        "visual_review": {"status": "pending", "note": ""},
    }
    write_bytes(output, data)
    state["paper_id"] = paper_id
    state["assets"][f"assets/{output.name}"] = record
    write_json(work_dir / "progress.json", state)
    return record


def markdown_body(text: str) -> tuple[str, list[str]]:
    """Keep prose, masking code fences and inline code before structural checks."""
    lines, errors, fence, display_math = [], [], None, False
    headings = []
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if marker:
            token, rest = marker.groups()
            if fence is None:
                fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1] and not rest.strip():
                fence = None
            continue
        if fence:
            continue
        line = re.sub(r"(`+)(?:(?!\1).)*?\1", "", line)
        if line.strip() == "$$":
            display_math = not display_math
            lines.append(line)
            continue
        if "$$" in line:
            errors.append(f"Line {number}: put display-math delimiters on separate lines")
        if not display_math and len(re.findall(r"(?<!\\)\$", line)) % 2:
            errors.append(f"Line {number}: unpaired inline math delimiter")
        heading = re.match(r"^(#{1,6})\s+", line)
        if heading:
            level = len(heading[1])
            headings.append(level)
            if level > 3:
                errors.append(f"Line {number}: use bold text for headings below subsection")
        lines.append(line)
    if fence:
        errors.append("Unclosed code fence")
    if display_math:
        errors.append("Unclosed display-math block")
    if not headings or headings[0] != 1 or headings.count(1) != 1:
        errors.append("Markdown must have one title at heading level 1")
    if not text.lstrip().startswith("# "):
        errors.append("Start the translation with its title")
    return "\n".join(lines), errors


def image_targets(body: str) -> tuple[list[str], list[str]]:
    targets, errors = [], []
    definitions = {m[1].strip().casefold(): m[2].strip() for m in re.finditer(r"^\[([^\]]+)\]:\s*(.+)$", body, re.M)}
    pattern = r"!\[([^\]]*)\](?:\(([^)\n]*)\)|\[([^\]]*)\])"
    matches = list(re.finditer(pattern, body))
    if len(matches) != len(re.findall(r"!\[", body)):
        errors.append("Unresolved image syntax; use ![图 1](assets/fig_01.png)")
    if re.search(r"<img\b", body, re.I):
        errors.append("Use Markdown image references for delivery images")
    for match in matches:
        value = match[2]
        if value is None:
            value = definitions.get((match[3] or match[1]).strip().casefold())
            if value is None:
                errors.append(f"Missing image reference definition: {match[3] or match[1]}")
                continue
        destination = re.fullmatch(r'''(?:<([^<>]+)>|(\S+?))(?:\s+["'].*["'])?''', value.strip())
        if destination is None:
            errors.append(f"Invalid image destination: {value}")
        else:
            targets.append(destination[1] or destination[2])
    return targets, errors


def local_image(root: Path, target: str) -> Path:
    url = urlsplit(target)
    decoded = unquote(url.path)
    if url.scheme or url.netloc or url.query or url.fragment or "\\" in decoded or PureWindowsPath(decoded).is_absolute():
        raise ZhouyiError(f"Image must use a local relative path: {target}")
    relative = Path(decoded)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] != "assets":
        raise ZhouyiError(f"Image must be inside assets/: {target}")
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ZhouyiError(f"Image resolves outside the paper directory: {target}")
    return path


def review_pending(state: dict | None, markdown_hash: str, count: int, used: set[str]) -> list[str]:
    if state is None:
        return ["No progress record; content and visual reviews are not recorded"]
    pending, covered = [], set()
    if not state["sections"]:
        pending.append("Create the source-based section and content inventory")
    ids = set()
    for section in state["sections"]:
        if not isinstance(section, dict):
            pending.append("Invalid section record")
            continue
        sid = section.get("id", "")
        if not sid or sid in ids:
            pending.append("Section IDs must be present and unique")
        ids.add(sid)
        if section.get("status") != "verified":
            pending.append(f"Section not verified: {sid}")
        pages = section.get("source_pages", [])
        if not isinstance(pages, list) or not pages or any(type(p) is not int or not 1 <= p <= count for p in pages):
            pending.append(f"Invalid source pages: {sid}")
        else:
            covered.update(pages)
        # Units are optional; a section-level record is enough, recorded units must all be verified.
        units = section.get("units", [])
        if not isinstance(units, list):
            pending.append(f"Invalid content units: {sid}")
        elif units:
            unit_ids = set()
            for unit in units:
                if not isinstance(unit, dict) or not unit.get("id") or unit.get("id") in unit_ids or unit.get("status") != "verified":
                    pending.append(f"Content unit incomplete or invalid: {sid}")
                if isinstance(unit, dict):
                    unit_ids.add(unit.get("id"))
    missing = sorted(set(range(1, count + 1)) - covered)
    if missing:
        pending.append(f"Source pages not accounted for: {missing}")
    for target in sorted(used):
        record = state["assets"].get(target, {})
        review = record.get("visual_review", {}) if isinstance(record, dict) else {}
        if not isinstance(review, dict) or review.get("status") != "verified" or not str(review.get("note", "")).strip():
            pending.append(f"Open and visually review: {target}")
    review = state["document_review"]
    if review.get("status") != "verified" or review.get("markdown_sha256") != markdown_hash or not str(review.get("note", "")).strip():
        pending.append("Final document review is pending or belongs to an earlier Markdown revision")
    for issue in state["issues"]:
        if not isinstance(issue, dict) or issue.get("status") not in {"resolved", "noted"}:
            pending.append(f"Unresolved issue: {issue}")
    return pending


def check_delivery(paper_dir: Path, source: Path | None = None, work_dir: Path | None = None,
                   allow_extra: list[str] | None = None) -> dict:
    root = Path(paper_dir).resolve()
    errors, warnings, used = [], [], set()
    if not root.is_dir():
        raise ZhouyiError(f"Paper directory does not exist: {root}")
    match = re.fullmatch(r"(\d{4})(\d{2})\.[A-Za-z0-9][A-Za-z0-9+-]*\.[A-Za-z0-9][A-Za-z0-9.+-]*", root.name)
    if not match or not 1 <= int(match[2]) <= 12:
        # An existing naming convention at the output root may take precedence.
        warnings.append("Directory name is not YYYYMM.Venue.ShortName")
    md, pdf = root / f"{root.name}.md", root / f"{root.name}.pdf"
    expected = {md.name, pdf.name, f"{root.name}.zh.pdf", "assets", *(allow_extra or [])}  # .zh.pdf: optional, from md2pdf
    for path in root.iterdir():
        if path.name not in expected:
            errors.append(f"Move working files outside the delivery directory: {path.name}")
    if not (root / "assets").is_dir():
        errors.append("Missing assets/ directory")
    for path in root.rglob("*"):
        if path.is_symlink():
            errors.append(f"Copy delivery files instead of using symlinks: {path.relative_to(root)}")
    markdown_hash, pdf_hash, count = "", "", 0
    if not md.is_file():
        errors.append(f"Missing Markdown: {md.name}")
    else:
        markdown_hash = sha256(md)
        body, md_errors = markdown_body(md.read_text(encoding="utf-8"))
        errors.extend(md_errors)
        targets, image_errors = image_targets(body)
        errors.extend(image_errors)
        for target in targets:
            try:
                path = local_image(root, target)
                used.add(path.relative_to(root).as_posix())
                with Image.open(path) as image:
                    image.verify()
                with Image.open(path) as image:
                    image.load()
                    if min(image.size) < 8:
                        errors.append(f"Image is too small to inspect: {target}")
            except (ValueError, OSError) as exc:
                errors.append(f"Image {target}: {exc}")
    if not pdf.is_file():
        errors.append(f"Missing PDF: {pdf.name}")
    else:
        pdf_hash = sha256(pdf)
        try:
            with open_pdf(pdf) as doc:
                count = doc.page_count
        except ZhouyiError as exc:
            errors.append(str(exc))
    if source is not None:
        try:
            if sha256(Path(source)) != pdf_hash:
                errors.append("Delivered PDF differs from the input PDF")
        except OSError as exc:
            errors.append(str(exc))
    state = None
    progress_dir = Path(work_dir) if work_dir is not None else root.parent / ".zhouyi" / root.name
    if (progress_dir / "progress.json").exists():
        try:
            state = load_progress(progress_dir, pdf_hash)
            if state["source"].get("page_count") != count:
                errors.append("Saved source page count differs from the PDF")
            if state.get("paper_id", root.name) != root.name:
                errors.append("Work directory belongs to a different paper directory")
        except ZhouyiError as exc:
            errors.append(str(exc))
    elif work_dir is not None:
        errors.append(f"Missing progress record: {progress_dir / 'progress.json'}")
    if source is None and state is None:
        errors.append("Supply --source or an existing work record to verify PDF identity")
    asset_files = [p for p in (root / "assets").rglob("*") if p.is_file()] if (root / "assets").is_dir() else []
    for path in asset_files:
        target = path.relative_to(root).as_posix()
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            errors.append(f"Unexpected asset file: {target}")
        elif target not in used:
            errors.append(f"Asset is not referenced by the Markdown: {target}")
    if state is not None:
        for target in used:
            record = state["assets"].get(target)
            path = root / target
            if not isinstance(record, dict):
                errors.append(f"Missing crop provenance: {target}")
            elif record.get("source_sha256") != pdf_hash:
                errors.append(f"Crop belongs to another source PDF: {target}")
            elif path.is_file() and sha256(path) != record.get("sha256"):
                errors.append(f"Image changed since crop registration: {target}")
        for target in state["assets"]:
            if target not in used:
                warnings.append(f"Unused crop record: {target}")
        warnings.extend(f"Documented limitation: {issue.get('detail', issue)}" for issue in state["issues"] if isinstance(issue, dict) and issue.get("status") == "noted")
    pending = review_pending(state, markdown_hash, count, used)
    return {"files_ok": not errors, "ready": not errors and not pending,
            "markdown_sha256": markdown_hash, "source_sha256": pdf_hash,
            "page_count": count, "referenced_images": len(used),
            "errors": errors, "pending_reviews": pending, "warnings": warnings}


def pack_delivery(paper_dir: Path, source: Path | None = None, work_dir: Path | None = None,
                  output: Path | None = None, replace: bool = False,
                  allow_extra: list[str] | None = None) -> dict:
    root = Path(paper_dir).resolve()
    result = check_delivery(root, source, work_dir, allow_extra)
    if not result["ready"]:
        raise ZhouyiError("Delivery is not ready for packaging:\n" + "\n".join(result["errors"] + result["pending_reviews"]))
    output = Path(output).resolve() if output is not None else root.parent / f"{root.name}.zip"
    if output.is_relative_to(root):
        raise ZhouyiError("Place the ZIP beside the paper directory")
    if output.exists() and not replace:
        raise ZhouyiError(f"ZIP exists: {output}; use --replace to refresh it")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".zhouyi-", suffix=".zip", dir=output.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    archive.write(path, f"{root.name}/{path.relative_to(root).as_posix()}")
            if not any((root / "assets").iterdir()):
                archive.writestr(f"{root.name}/assets/", b"")
        with zipfile.ZipFile(name) as archive:
            if archive.testzip() is not None:
                raise ZhouyiError("ZIP integrity check failed")
        os.replace(name, output)
    finally:
        Path(name).unlink(missing_ok=True)
    return {"path": str(output), "sha256": sha256(output), "warnings": result["warnings"]}


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("inspect", help="Extract PDF metadata and text; initialize or validate progress")
    p.add_argument("pdf", type=Path)
    p.add_argument("--work-dir", type=Path)
    p = sub.add_parser("render", help="Render visible PDF pages to PNG previews")
    p.add_argument("pdf", type=Path)
    p.add_argument("--pages", default="all")
    p.add_argument("--output-dir", required=True, type=Path)
    p.add_argument("--dpi", type=int, default=120)
    p = sub.add_parser("crop", help="Crop a normalized region of a rendered page and record provenance")
    p.add_argument("pdf", type=Path)
    p.add_argument("--page", required=True, type=int)
    p.add_argument("--box", required=True, type=float, nargs=4, metavar=("X0", "Y0", "X1", "Y1"))
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--work-dir", required=True, type=Path)
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--object-id")
    p = sub.add_parser("sheet", help="Tile previews or crops into labelled contact sheets")
    p.add_argument("images", type=Path, nargs="+")
    p.add_argument("--output-dir", required=True, type=Path)
    p.add_argument("--per", type=int, default=4)
    p.add_argument("--cols", type=int, help="Default: 2, or 1 with --per 1")
    p.add_argument("--max-side", type=int, default=1600)
    p.add_argument("--prefix", default="sheet")
    p.add_argument("--format", default="jpg", choices=["jpg", "png"])
    p = sub.add_parser("status", help="Mode, stage and progress of every paper under an output root")
    p.add_argument("root", type=Path, nargs="?", default=Path("."))
    p = sub.add_parser("clean", help="Remove tmp/ and LaTeX leftovers from a paper's work directory")
    p.add_argument("work_dir", type=Path)
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("fetch", help="Download an arXiv PDF and the source of the same version")
    p.add_argument("arxiv", help="Identifier or link, e.g. 1706.03762 or https://arxiv.org/abs/1706.03762v7")
    p.add_argument("--version", type=int)
    p.add_argument("--output-root", required=True, type=Path)
    p = sub.add_parser("md2pdf", help="Typeset the delivered Markdown into <ID>.zh.pdf with pandoc and XeLaTeX")
    p.add_argument("paper_dir", type=Path)
    p.add_argument("--work-dir", type=Path, help="Default: <root>/.zhouyi/<ID>")
    p.add_argument("--texbin", type=Path, help="Directory holding xelatex")
    p.add_argument("--pandoc")
    p = sub.add_parser("compare", help="Compare an English build with the original PDF page by page")
    p.add_argument("original", type=Path)
    p.add_argument("built", type=Path)
    p.add_argument("--threshold", type=float, default=0.85)
    p.add_argument("--window", type=int, default=2)
    p = sub.add_parser("review-sheet", help="One contact sheet of the pages worth a look before delivery")
    p.add_argument("pdf", type=Path)
    p.add_argument("--output-dir", required=True, type=Path)
    p.add_argument("--log", type=Path, help="TeX log, for pages with overfull boxes or missing glyphs")
    p.add_argument("--max-pages", type=int, default=8)
    for command in ["check", "pack"]:
        p = sub.add_parser(command, help="Check delivery files and review records" if command == "check" else "Validate and package a reviewed delivery")
        p.add_argument("paper_dir", type=Path)
        p.add_argument("--source", type=Path)
        p.add_argument("--work-dir", type=Path)
        p.add_argument("--allow-extra", action="append", default=[], metavar="NAME",
                       help="File allowed in the paper directory, e.g. meta.json; repeatable")
        if command == "check":
            p.add_argument("--require-reviewed", action="store_true")
        else:
            p.add_argument("--output", type=Path)
            p.add_argument("--replace", action="store_true")
    return ap


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "inspect":
            result = inspect_pdf(args.pdf, args.work_dir)
            if args.work_dir:
                result = {"source": result["source"], "work_dir": str(args.work_dir),
                          "pages_without_text": [p["page"] for p in result["pages"] if p["needs_visual_reading"]]}
        elif args.command == "render":
            result = render_pdf(args.pdf, args.pages, args.output_dir, args.dpi)
        elif args.command == "crop":
            result = crop_pdf(args.pdf, args.page, args.box, args.output, args.work_dir, args.dpi, args.object_id)
        elif args.command == "sheet":
            cols = args.cols if args.cols is not None else min(2, args.per)
            result = make_sheets(args.images, args.output_dir, args.per, cols, args.max_side, args.prefix, args.format)
        elif args.command == "status":
            result = status(args.root)
        elif args.command == "clean":
            result = clean(args.work_dir, args.dry_run)
        elif args.command == "fetch":
            result = fetch(args.arxiv, args.output_root, args.version)
        elif args.command == "md2pdf":
            result = md2pdf(args.paper_dir, args.work_dir, args.texbin, args.pandoc)
        elif args.command == "compare":
            result = compare_pdfs(args.original, args.built, args.threshold, args.window)
        elif args.command == "review-sheet":
            result = review_sheet(args.pdf, args.output_dir, args.log, args.max_pages)
        elif args.command == "check":
            result = check_delivery(args.paper_dir, args.source, args.work_dir, args.allow_extra)
        else:
            result = pack_delivery(args.paper_dir, args.source, args.work_dir, args.output, args.replace, args.allow_extra)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "check" and not result["ready" if args.require_reviewed else "files_ok"]:
            return 1
        return 0
    except (ZhouyiError, OSError, UnicodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

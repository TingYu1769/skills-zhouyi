#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pymupdf>=1.26,<2", "pillow>=11,<13"]
# ///
"""PDF preparation, original-page crops, contact sheets, delivery checks and packaging for Zhouyi."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import sys
import tempfile
from urllib.parse import unquote, urlsplit
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
    expected = {md.name, pdf.name, "assets", *(allow_extra or [])}
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

#!/usr/bin/env python3
"""Check Zhouyi's runtime before loading its PDF tools; uses stdlib at startup."""

import argparse
import importlib
import io
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile


DEPENDENCIES = (
    ("pymupdf", "pymupdf", (1, 26, 0), (2, 0, 0), "pymupdf>=1.26,<2"),
    ("Pillow", "PIL.Image", (11, 0, 0), (13, 0, 0), "pillow>=11,<13"),
)


def supported_version(value, lower, upper):
    match = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?(?:\.(?:post)\d+)?", value)
    if not match:
        return False
    release = tuple(int(part or 0) for part in match.groups())
    return lower <= release < upper


def check_tex(add, texbin=None):
    """Optional checks for the LaTeX source mode: XeLaTeX, latexmk, ctex and the Chinese fonts."""
    import subprocess
    search = str(texbin) if texbin else None
    for tool in ("latexmk", "xelatex", "kpsewhich"):
        path = shutil.which(tool, path=search)
        add(f"tex_{tool}", bool(path), path or "not found; install TeX Live or pass --texbin")
    kpse = shutil.which("kpsewhich", path=search)
    if kpse:
        found = subprocess.run([kpse, "ctex.sty"], capture_output=True, text=True).stdout.strip()
        add("tex_ctex", bool(found), found or "ctex.sty not found; install the TeX Live Chinese collection")
    fc_list = shutil.which("fc-list")
    families = subprocess.run([fc_list, ":lang=zh-cn", "family"], capture_output=True, text=True).stdout if fc_list else ""
    add("font_noto_serif_cjk_sc", "Noto Serif CJK SC" in families,
        "found" if "Noto Serif CJK SC" in families else "install Noto Serif/Sans CJK (fonts-noto-cjk)")
    # Kaiti for Chinese emphasis is optional; zh-setup.tex falls back to a slanted Song face.
    checks_note = "found" if "AR PL UKai CN" in families else "absent; Chinese emphasis will be slanted Song"
    add("font_kaiti_optional", True, checks_note)


def check_environment(pdf=None, output_root=None, tex=False, texbin=None):
    checks, modules = [], {}

    def add(name, ok, detail):
        checks.append({"name": name, "ok": ok, "detail": detail})

    python_ok = sys.version_info >= (3, 10)
    add("python", python_ok, {"executable": sys.executable,
                              "version": sys.version.split()[0], "required": ">=3.10"})
    if python_ok:
        metadata = importlib.import_module("importlib.metadata")
        for package, module, lower, upper, requirement in DEPENDENCIES:
            try:
                version = metadata.version(package)
                if not supported_version(version, lower, upper):
                    add(package, False, {"version": version, "required": requirement})
                    continue
                modules[package] = importlib.import_module(module)
                add(package, True, {"version": version, "required": requirement})
            except Exception as exc:
                add(package, False, {"required": requirement, "error": str(exc)})

    output_ok = False
    if output_root is not None:
        output_root = Path(output_root)
        try:
            output_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".zhouyi-check-", dir=output_root) as temp:
                probe = Path(temp) / "probe.txt"
                probe.write_bytes(b"zhouyi")
                if probe.read_bytes() != b"zhouyi":
                    raise OSError("Output read/write probe failed")
            output_ok = True
            add("output_root", True, str(output_root.resolve()))
        except OSError as exc:
            add("output_root", False, str(exc))

    preview = None
    if pdf is not None:
        pdf = Path(pdf)
        readable = False
        try:
            with pdf.open("rb") as stream:
                if not stream.read(1):
                    raise ValueError("Input file is empty")
            readable = True
            add("input_readable", True, str(pdf.resolve()))
        except (OSError, ValueError) as exc:
            add("input_readable", False, str(exc))

        if readable and len(modules) == len(DEPENDENCIES):
            try:
                with modules["pymupdf"].open(pdf) as doc:
                    if not doc.is_pdf or doc.needs_pass or not doc.page_count:
                        raise ValueError("Input must be a readable, unlocked PDF with at least one page")
                    pix = doc[0].get_pixmap(dpi=120, colorspace=modules["pymupdf"].csRGB, alpha=False)
                    data = pix.tobytes("png")
                    with modules["Pillow"].open(io.BytesIO(data)) as image:
                        image.load()
                        dimensions = list(image.size)
                    add("pdf_render", True, {"pages": doc.page_count, "preview_size": dimensions,
                                             "first_page_has_text": bool(doc[0].get_text().strip())})
                if output_ok:
                    preview_dir = output_root / ".zhouyi" / "preflight"
                    preview_dir.mkdir(parents=True, exist_ok=True)
                    # A distinct filename keeps simultaneous paper checks separate.
                    with tempfile.NamedTemporaryFile(prefix="page_001-", suffix=".png",
                                                     dir=preview_dir, delete=False) as stream:
                        stream.write(data)
                        preview = str(Path(stream.name).resolve())
                    add("preview_saved", True, preview)
            except Exception as exc:
                add("pdf_render_or_preview", False, str(exc))

    if tex:
        check_tex(add, texbin)

    checks_ok = all(item["ok"] for item in checks)
    if not checks_ok:
        next_steps = ["Resolve the failed checks using references/environment.md, then rerun this check."]
    elif preview is None:
        next_steps = ["First run in this output root: rerun with the input PDF and --output-root to create "
                      "a preview for agent inspection. Reusing a recorded environment needs no new preview."]
    else:
        next_steps = []

    return {
        "programmatic_checks_ok": checks_ok,
        "checks": checks,
        "uv": shutil.which("uv"),
        "preview": preview,
        "agent_checks": (["Open the preview with the host image-viewing tool and inspect the page."]
                         if preview is not None else []),
        "next_steps": next_steps,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", nargs="?", type=Path, help="Optional input PDF to read and render")
    parser.add_argument("--output-root", type=Path, help="Check output access; save a preview when a PDF is supplied")
    parser.add_argument("--tex", action="store_true", help="Also check XeLaTeX, ctex and Chinese fonts for the source mode")
    parser.add_argument("--texbin", type=Path, help="Directory holding latexmk and xelatex")
    args = parser.parse_args()
    result = check_environment(args.pdf, args.output_root, args.tex, args.texbin)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["programmatic_checks_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

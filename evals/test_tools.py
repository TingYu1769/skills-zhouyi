"""Behavioral tests for PDF identity, pixel crops, recovery and portable delivery."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

import pymupdf
from PIL import Image, ImageChops

from make_fixture import make_fixture

TEXBIN = Path(os.environ["ZHOUYI_TEXBIN"]) if os.environ.get("ZHOUYI_TEXBIN") else None
HAS_TEX = bool(shutil.which("xelatex", path=str(TEXBIN) if TEXBIN else None))

SCRIPT = Path(__file__).resolve().parents[1] / "skills/zhouyi/scripts/zhouyi.py"
spec = importlib.util.spec_from_file_location("zhouyi", SCRIPT)
z = importlib.util.module_from_spec(spec)
spec.loader.exec_module(z)


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = make_fixture(self.root / "original.pdf")
        self.pid = "202601.arXiv.Example"
        self.paper = self.root / self.pid
        (self.paper / "assets").mkdir(parents=True)
        shutil.copyfile(self.source, self.paper / f"{self.pid}.pdf")
        self.work = self.root / ".zhouyi" / self.pid
        z.inspect_pdf(self.source, self.work)

    def tearDown(self):
        self.temp.cleanup()

    def state(self):
        return z.read_json(self.work / "progress.json")

    def save(self, state):
        z.write_json(self.work / "progress.json", state)

    def markdown(self, extra=""):
        path = self.paper / f"{self.pid}.md"
        path.write_text("# 示例（Example）\n\n## 1. 方法（Method）\n\n完整测试内容。\n" + extra, encoding="utf-8")
        return path

    def crop(self, page=1, box=(0.08, 0.40, 0.93, 0.57), filename="fig_01.png", dpi=72):
        output = self.paper / "assets" / filename
        return z.crop_pdf(self.source, page, list(box), output, self.work, dpi, "figure1")

    def record_test_reviews(self):
        """Synthetic records exercise bookkeeping, not a claim of agent visual review."""
        state = self.state()
        state["sections"] = [{"id": "test", "source_pages": [1, 2, 3, 4], "status": "verified",
                              "units": [{"id": "unit1", "status": "verified"}]}]
        state["glossary"] = {"attention": "注意力"}
        for record in state["assets"].values():
            record["visual_review"] = {"status": "verified", "note": "Synthetic review record for state-transition tests."}
        state["document_review"] = {"status": "verified", "markdown_sha256": z.sha256(self.paper / f"{self.pid}.md"),
                                     "note": "Synthetic document review for tests."}
        self.save(state)

    def make_pdf(self, path, pages):
        doc = pymupdf.open()
        for text in pages:
            page = doc.new_page()
            page.insert_textbox(pymupdf.Rect(60, 60, 540, 780), text, fontname="china-s", fontsize=10)
        doc.save(path)
        return path

    def test_compare_flags_changed_pages_new_glyphs_and_page_count(self):
        words = ["alpha beta gamma delta epsilon zeta eta theta " * 12, "iota kappa lambda mu nu xi omicron pi " * 12,
                 "rho sigma tau upsilon phi chi psi omega " * 12]
        original = self.make_pdf(self.root / "orig.pdf", words)
        built = self.make_pdf(self.root / "built.pdf", [words[0], "completely different words on this page " * 12,
                                                         words[2].replace("omega", "omega ±"), "spill over"])
        result = z.compare_pdfs(original, built)
        self.assertEqual(result["pages"], {"original": 3, "built": 4})
        self.assertEqual([p["page"] for p in result["flagged"]], [2])
        self.assertIn("±", result["new_characters"])  # a glyph the original never had
        self.assertEqual(result["main_fonts"]["original"][0]["font"], result["main_fonts"]["built"][0]["font"])

    def test_review_sheet_shows_first_page_and_pages_named_in_the_log(self):
        pdf = self.make_pdf(self.root / "zh.pdf", ["第一页", "第二页", "第三页", "第四页"])
        log = self.root / "main.log"
        log.write_text("[1] [2]\nOverfull \\hbox (25.3pt too wide) in paragraph at lines 10--12\n"
                       "Overfull \\hbox (3.1pt too wide) in paragraph at lines 13--14\n[3]\n"
                       "Missing character: There is no X in font foo!\n[4]\n")
        result = z.review_sheet(pdf, self.root / "review", log)
        self.assertEqual({p["page"]: p["reasons"] for p in result["pages"]},
                         {1: ["first page"], 3: ["overfull"], 4: ["missing glyph"]})
        self.assertEqual(len(result["sheets"]), 1)
        self.assertTrue(Path(result["sheets"][0]["path"]).is_file())

    def test_status_reads_stage_progress_issues_and_tool_notes(self):
        root = self.root / "out"
        tex = root / ".zhouyi" / "2601_arXiv_A" / "tex"
        (tex / "chunks").mkdir(parents=True)
        (tex / "tools").mkdir()
        z.write_json(tex / "tex.json", {"main": "main.tex", "tex_files": ["main.tex"]})
        z.write_json(tex / "chunks" / "index.json", {"chunks": [{"id": "c01"}, {"id": "c02"}]})
        (tex / "chunks" / "c01.zh.tex").write_text("译文")
        z.write_json(tex / "progress.json", {"mode": "tex", "issues": [{"status": "open"}, {"status": "noted"}]})
        (tex / "tools" / "gen.py").write_text("# rebuild overrides/main.tex after editing c03\nprint()\n")
        (tex / "tools" / "__pycache__").mkdir()
        pdf = root / ".zhouyi" / "2601_arXiv_B"
        pdf.mkdir(parents=True)
        z.write_json(pdf / "progress.json", {"sections": [{"status": "verified"}, {"status": "pending"}], "issues": []})
        papers = {p["id"]: p for p in z.status(root)["papers"]}
        a, b = papers["2601_arXiv_A"], papers["2601_arXiv_B"]
        self.assertEqual((a["modes"], a["stage"], a["progress"], a["open_issues"]), (["tex"], "translating", "1/2 chunks", 1))
        self.assertEqual(a["tools"], [{"path": "gen.py", "note": "rebuild overrides/main.tex after editing c03"}])
        self.assertEqual((b["modes"], b["stage"], b["progress"]), (["pdf"], "translating", "1/2 sections verified"))
        self.assertLess(a["minutes_ago"], 5)

    def test_clean_removes_tmp_and_build_leftovers_only(self):
        work = self.root / ".zhouyi" / "2601_arXiv_A"
        tex = work / "tex"
        for rel in ("tmp/review/page_001.png", "tex/tmp/x.jpg", "tex/src/main.tex", "tex/build/main.tex",
                    "tex/build/main.aux", "tex/build/main.log", "tex/build/main.pdf", "tex/build/figs/plot.pdf",
                    "tex/chunks/c01.zh.tex", "tex/overrides/a.sty", "tex/tools/gen.py"):
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / rel).write_text("x")
        (tex / "src" / "figs").mkdir()
        (tex / "src" / "figs" / "plot.pdf").write_text("x")
        for rel in ("src/logo.pdf", "build/logo.pdf"):  # shipped with the source, next to the main file
            (tex / rel).write_text("x")
        plan = z.clean(work, dry_run=True)
        self.assertTrue((work / "tmp").exists())
        self.assertEqual(plan["count"], 5)
        z.clean(work)
        for rel in ("tex/src/main.tex", "tex/build/main.tex", "tex/build/figs/plot.pdf", "tex/chunks/c01.zh.tex",
                    "tex/overrides/a.sty", "tex/tools/gen.py", "tex/build/logo.pdf"):
            self.assertTrue((work / rel).exists(), rel)
        for rel in ("tmp", "tex/tmp", "tex/build/main.aux", "tex/build/main.log", "tex/build/main.pdf"):
            self.assertFalse((work / rel).exists(), rel)

    def test_fetch_downloads_pdf_and_source_of_the_same_version(self):
        import http.server, threading, io as _io, tarfile as _tar
        buf = _io.BytesIO()
        with _tar.open(fileobj=buf, mode="w:gz") as t:
            data = b"\\documentclass{article}"
            info = _tar.TarInfo("main.tex"); info.size = len(data); t.addfile(info, _io.BytesIO(data))
        atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/{id}v{v}</id>'
                '<published>2017-06-12T17:57:34Z</published><title>Attention Is\n All</title>'
                '<author><name>A. Author</name></author></entry></feed>')
        routes = {"/api?id_list=1706.03762": atom.format(id="1706.03762", v=7).encode(),
                  "/api?id_list=2001.00001": atom.format(id="2001.00001", v=1).encode(),
                  "/api?id_list=2002.00002": atom.format(id="2002.00002", v=2).encode(),
                  "/pdf/1706.03762v7": b"%PDF-1.5 seven", "/pdf/1706.03762v1": b"%PDF-1.5 one",
                  "/src/1706.03762v7": buf.getvalue(), "/src/1706.03762v1": buf.getvalue(),
                  "/pdf/2001.00001v1": b"%PDF-1.5", "/src/2001.00001v1": b"%PDF-1.5 only",
                  "/pdf/2002.00002v2": b"%PDF-1.5"}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = routes.get(self.path)
                self.send_response(200 if body else 404); self.end_headers()
                self.wfile.write(body or b"not found")
            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"
        old = (z.ARXIV_API, z.ARXIV_SITE)
        z.ARXIV_API, z.ARXIV_SITE = base + "/api?id_list=", base
        try:
            r = z.fetch("https://arxiv.org/abs/1706.03762", self.root)
            self.assertEqual((r["version"], r["latest"], r["yymm"], r["title"]), (7, 7, "1706", "Attention Is All"))
            self.assertEqual(r["source"]["type"], "tar.gz")
            self.assertTrue(Path(r["pdf"]["path"]).read_bytes().endswith(b"seven"))
            self.assertEqual(z.fetch("1706.03762v1", self.root)["version"], 1)
            self.assertEqual(z.fetch("2001.00001", self.root)["source"]["type"], "pdf-only")
            self.assertEqual(z.fetch("2002.00002", self.root)["source"]["type"], "none")
            with self.assertRaises(z.ZhouyiError):
                z.fetch("not an id", self.root)
        finally:
            z.ARXIV_API, z.ARXIV_SITE = old
            server.shutdown()

    @unittest.skipUnless(HAS_TEX and shutil.which("pandoc"), "set ZHOUYI_TEXBIN and install pandoc")
    def test_md2pdf_typesets_the_markdown_and_check_accepts_it(self):
        Image.new("RGB", (900, 300), "white").save(self.paper / "assets" / "fig_01.png", dpi=(300, 300))
        (self.paper / f"{self.pid}.md").write_text(
            "# 示例论文（An Example Paper）\n\n## 摘要（Abstract）\n\n这是一段摘要<sup>1</sup>。\n\n"
            "![图 1](assets/fig_01.png)\n\n*图 1：示例图注。*\n\n**1** 脚注的说明文字。\n", encoding="utf-8")
        result = z.md2pdf(self.paper, self.work, TEXBIN)
        self.assertEqual(result["missing_characters"], 0)
        import pymupdf as _p
        text = "".join("".join(page.get_text().split()) for page in _p.open(result["pdf"]))
        for piece in ("示例论文", "AnExamplePaper", "摘要", "图1", "示例图注", "脚注的说明文字"):
            self.assertIn(piece, text)
        self.assertTrue((self.work / "tmp" / "md2pdf").is_dir())
        self.assertNotIn(f"{self.pid}.zh.pdf", " ".join(z.check_delivery(self.paper)["errors"]))

    def test_inspection_detects_rotation_scan_and_preserves_progress(self):
        info = z.inspect_pdf(self.source, self.work)
        self.assertEqual(info["source"]["page_count"], 4)
        self.assertEqual(info["pages"][2]["rotation"], 90)
        self.assertEqual((info["pages"][2]["width_pt"], info["pages"][2]["height_pt"]), (800, 600))
        self.assertTrue(info["pages"][3]["needs_visual_reading"])
        self.assertIn("right column", info["pages"][0]["text"])
        state = self.state()
        state["glossary"]["attention"] = "注意力"
        self.save(state)
        before = (self.work / "progress.json").read_bytes()
        z.inspect_pdf(self.source, self.work)
        self.assertEqual(before, (self.work / "progress.json").read_bytes())

    def test_original_pixel_crop_on_normal_rotated_and_scanned_pages(self):
        for page in [1, 3, 4]:
            with self.subTest(page=page):
                self.crop(page, (.1, .2, .9, .8))
                with pymupdf.open(self.source) as doc:
                    pix = doc[page - 1].get_pixmap(dpi=72, colorspace=pymupdf.csRGB, alpha=False)
                    reference = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    expected = reference.crop((int(pix.width / 10), int(pix.height / 5), int(pix.width * 9 / 10), int(pix.height * 4 / 5)))
                with Image.open(self.paper / "assets/fig_01.png") as actual:
                    self.assertEqual(actual.size, expected.size)
                    self.assertIsNone(ImageChops.difference(expected, actual).getbbox())

    def test_crop_keeps_bottom_footnote_and_last_row(self):
        self.crop(2, (0.075, .21, .93, .355), "table_01_p02.png")
        with Image.open(self.paper / "assets/table_01_p02.png") as image:
            # The chosen source region includes the table's last row AND the note below it.
            gray = image.convert("L")
            self.assertLess(gray.crop((0, 83, gray.width, gray.height)).getextrema()[0], 128)
        rec = self.state()["assets"]["assets/table_01_p02.png"]
        self.assertEqual(rec["page"], 2)
        self.assertEqual(rec["pixel_box"][3], 284)

    def test_cross_page_records_and_recrop_invalidate_only_changed_image(self):
        self.crop(1, (.07, .60, .94, .79), "table_01_p01.png")
        self.crop(2, (.07, .18, .94, .36), "table_01_p02.png")
        self.markdown("\n![表1](assets/table_01_p01.png)\n![续表](assets/table_01_p02.png)\n")
        self.record_test_reviews()
        self.assertTrue(z.check_delivery(self.paper)["ready"])
        self.crop(2, (.06, .18, .95, .37), "table_01_p02.png")
        state = self.state()
        self.assertEqual(state["assets"]["assets/table_01_p01.png"]["visual_review"]["status"], "verified")
        self.assertEqual(state["assets"]["assets/table_01_p02.png"]["visual_review"]["status"], "pending")
        self.assertFalse(z.check_delivery(self.paper)["ready"])

    def test_changed_source_refuses_resume_without_touching_state(self):
        before = (self.work / "progress.json").read_bytes()
        with pymupdf.open(self.source) as doc:
            doc[0].insert_text((30, 790), "Changed version")
            other = self.root / "other.pdf"
            doc.save(other)
        with self.assertRaisesRegex(z.ZhouyiError, "differs"):
            z.inspect_pdf(other, self.work)
        self.assertEqual(before, (self.work / "progress.json").read_bytes())

    def test_invalid_page_box_and_corrupt_pdf(self):
        for page, box in [(0, (0, 0, 1, 1)), (5, (0, 0, 1, 1)),
                          (1, (-.1, 0, 1, 1)), (1, (.5, 0, .1, 1)), (1, (0, 0, float("nan"), 1))]:
            with self.subTest(page=page, box=box), self.assertRaises(z.ZhouyiError):
                self.crop(page, box)
        corrupt = self.root / "broken.pdf"
        corrupt.write_bytes(b"%PDF-1.7\nnot a PDF")
        with self.assertRaises(z.ZhouyiError):
            z.inspect_pdf(corrupt)
        for pages in ["0", "5", "3-2", "1-999999999999", "1,,2"]:
            with self.assertRaises(z.ZhouyiError):
                z.parse_pages(pages, 4)

    def test_missing_corrupt_external_and_escaping_images(self):
        for target in ["assets/missing.png", "https://example.org/image.png", "../escape.png", "/tmp/absolute.png", "assets/%2e%2e/escape.png"]:
            with self.subTest(target=target):
                self.markdown(f"\n![图]({target})\n")
                self.assertFalse(z.check_delivery(self.paper)["files_ok"])
        path = self.paper / "assets/fig_01.png"
        path.write_bytes(b"broken png")
        self.markdown("\n![图](assets/fig_01.png)\n")
        self.assertFalse(z.check_delivery(self.paper)["files_ok"])

    def test_pending_reviews_do_not_prevent_file_check_but_prevent_pack(self):
        self.crop()
        self.markdown("\n![图](assets/fig_01.png)\n")
        result = z.check_delivery(self.paper)
        self.assertTrue(result["files_ok"], result)
        self.assertFalse(result["ready"])
        with self.assertRaisesRegex(z.ZhouyiError, "not ready"):
            z.pack_delivery(self.paper)

    def test_markdown_and_image_edits_invalidate_recorded_reviews(self):
        self.crop()
        md = self.markdown("\n![图](assets/fig_01.png)\n")
        self.record_test_reviews()
        md.write_text(md.read_text() + "\n改动的正文。\n")
        result = z.check_delivery(self.paper)
        self.assertTrue(result["files_ok"])
        self.assertFalse(result["ready"])
        path = self.paper / "assets/fig_01.png"
        with Image.open(path) as image:
            modified = image.copy()
        modified.putpixel((0, 0), (0, 0, 0))
        modified.save(path)
        self.assertTrue(any("changed since" in e for e in z.check_delivery(self.paper)["errors"]))

    def test_pdf_identity_and_source_coverage_are_checked(self):
        self.markdown()
        self.record_test_reviews()
        state = self.state()
        state["sections"][0]["source_pages"] = [1, 2, 3]
        self.save(state)
        self.assertTrue(any("not accounted" in p for p in z.check_delivery(self.paper)["pending_reviews"]))
        with (self.paper / f"{self.pid}.pdf").open("ab") as stream:
            stream.write(b"\n% changed copy")
        self.assertFalse(z.check_delivery(self.paper, self.source)["files_ok"])

    def test_markdown_fences_math_reference_images_and_heading_depth(self):
        self.crop()
        good = '\n![图][panel]\n\n[panel]: assets/fig_01.png "Figure"\n\n```python\nprint("$ ![not-an-image](missing)")\n#### code comment\n```\n\n$$\ny = 1 \\tag{1}\n$$\n'
        self.markdown(good)
        self.assertTrue(z.check_delivery(self.paper)["files_ok"])
        for suffix in ["\n```text\nunclosed", "\n$$\ny = 1\n", "\n#### 太深的标题\n", "\n未闭合的 $x\n", "\n![坏引用][absent]\n"]:
            self.markdown(good + suffix)
            self.assertFalse(z.check_delivery(self.paper)["files_ok"], suffix)

    def test_zip_relocation_and_non_image_working_files(self):
        self.crop()
        self.markdown("\n![图](assets/fig_01.png)\n")
        self.record_test_reviews()
        packed = z.pack_delivery(self.paper)
        destination = self.root / "moved"
        with zipfile.ZipFile(packed["path"]) as archive:
            self.assertIsNone(archive.testzip())
            self.assertTrue(all(name.startswith(self.pid + "/") for name in archive.namelist()))
            self.assertFalse(any(".zhouyi" in name for name in archive.namelist()))
            archive.extractall(destination)
        self.assertTrue(z.check_delivery(destination / self.pid, self.source)["files_ok"])
        with self.assertRaisesRegex(z.ZhouyiError, "exists"):
            z.pack_delivery(self.paper)
        (self.paper / "notes.txt").write_text("working notes")
        self.assertFalse(z.check_delivery(self.paper)["files_ok"])

    def test_source_text_keeps_column_order_and_marks_scanned_pages(self):
        text = (self.work / "source.txt").read_text(encoding="utf-8")
        self.assertLess(text.index("An attention head"), text.index("Reading continues in the right column"))
        page4 = text.split("===== PAGE 4 =====", 1)[1]
        self.assertIn("No text layer", page4)

    def test_contact_sheets_group_and_label_images(self):
        previews = z.render_pdf(self.source, "all", self.root / "previews", dpi=60)
        paths = [Path(p["path"]) for p in previews]
        sheets = z.make_sheets(paths, self.root / "sheets", per=3, cols=2, max_side=800)
        self.assertEqual([len(s["members"]) for s in sheets], [3, 1])
        for sheet in sheets:
            with Image.open(sheet["path"]) as image:
                self.assertLessEqual(max(image.size), 800)
        with self.assertRaises(z.ZhouyiError):
            z.make_sheets(paths, self.root / "sheets", per=2, cols=3)
        with self.assertRaises(z.ZhouyiError):
            z.make_sheets([self.root / "missing.png"], self.root / "sheets")
        # --per 1 alone gives one image per sheet instead of tripping over the default two columns
        result = subprocess.run([sys.executable, str(SCRIPT), "sheet", str(paths[0]), "--output-dir",
                                 str(self.root / "single"), "--per", "1"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_section_records_without_units_are_enough(self):
        self.markdown()
        self.record_test_reviews()
        state = self.state()
        del state["sections"][0]["units"]
        self.save(state)
        self.assertTrue(z.check_delivery(self.paper)["ready"])
        state["sections"][0]["units"] = [{"id": "p1", "status": "pending"}]
        self.save(state)
        self.assertFalse(z.check_delivery(self.paper)["ready"])

    def test_existing_naming_convention_and_allowed_sidecar_files(self):
        pid = "2601_arXiv_Example"
        paper = self.root / pid
        (paper / "assets").mkdir(parents=True)
        shutil.copyfile(self.source, paper / f"{pid}.pdf")
        (paper / "meta.json").write_text("{}")
        (paper / f"{pid}.md").write_text("# 示例（Example）\n\n完整测试内容。\n", encoding="utf-8")
        work = self.root / ".zhouyi" / pid
        z.inspect_pdf(self.source, work)
        state = z.read_json(work / "progress.json")
        state["sections"] = [{"id": "all", "source_pages": [1, 2, 3, 4], "status": "verified"}]
        state["document_review"] = {"status": "verified", "markdown_sha256": z.sha256(paper / f"{pid}.md"),
                                     "note": "Synthetic document review for tests."}
        z.write_json(work / "progress.json", state)
        self.assertFalse(z.check_delivery(paper)["files_ok"])
        result = z.check_delivery(paper, allow_extra=["meta.json"])
        self.assertTrue(result["ready"], result)
        self.assertTrue(any("YYYYMM" in w for w in result["warnings"]))
        with self.assertRaisesRegex(z.ZhouyiError, "not ready"):
            z.pack_delivery(paper)
        packed = z.pack_delivery(paper, allow_extra=["meta.json"])
        with zipfile.ZipFile(packed["path"]) as archive:
            self.assertIn(f"{pid}/meta.json", archive.namelist())

    def test_cli_reports_errors_with_exit_codes(self):
        self.markdown()
        result = subprocess.run([sys.executable, str(SCRIPT), "check", str(self.paper)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["ready"])
        result = subprocess.run([sys.executable, str(SCRIPT), "check", str(self.paper), "--require-reviewed"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        result = subprocess.run([sys.executable, str(SCRIPT), "render", str(self.source), "--pages", "0", "--output-dir", str(self.root / "previews")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stderr))


if __name__ == "__main__":
    unittest.main()

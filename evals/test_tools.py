"""Behavioral tests for PDF identity, pixel crops, recovery and portable delivery."""
import importlib.util
import json
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

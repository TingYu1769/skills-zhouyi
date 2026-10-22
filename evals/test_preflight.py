"""First-run checks, including execution without any third-party packages."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pymupdf
from PIL import Image, ImageChops

from make_fixture import make_fixture

SCRIPT = Path(__file__).resolve().parents[1] / "skills/zhouyi/scripts/preflight.py"
spec = importlib.util.spec_from_file_location("preflight", SCRIPT)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class PreflightTest(unittest.TestCase):
    def test_missing_packages_are_reported_by_stdlib_only_python(self):
        run = subprocess.run([sys.executable, "-I", "-S", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 1, run.stderr)
        report = json.loads(run.stdout)
        self.assertFalse(report["programmatic_checks_ok"])
        self.assertEqual({x["name"] for x in report["checks"] if not x["ok"]}, {"pymupdf", "Pillow"})
        self.assertNotIn("Traceback", run.stderr)
        self.assertIsNone(report["preview"])
        self.assertFalse(report["agent_checks"])
        self.assertTrue(report["next_steps"])

    def test_incompatible_versions_are_reported(self):
        for versions in [{"pymupdf": "1.25.0", "Pillow": "13.0.0"},
                         {"pymupdf": "2.0.0", "Pillow": "10.4.0"}]:
            with self.subTest(versions=versions), patch("importlib.metadata.version", side_effect=versions.__getitem__):
                report = preflight.check_environment()
                self.assertFalse(report["programmatic_checks_ok"])
                self.assertEqual(sum(not x["ok"] for x in report["checks"]), 2)

    def test_installed_but_broken_import_is_reported(self):
        real_import = preflight.importlib.import_module

        def broken(name):
            if name == "pymupdf":
                raise ImportError("missing shared library")
            return real_import(name)

        with patch.object(preflight.importlib, "import_module", side_effect=broken):
            report = preflight.check_environment()
        self.assertFalse(report["programmatic_checks_ok"])
        self.assertTrue(any("missing shared library" in str(x) for x in report["checks"]))

    def test_real_input_output_and_original_page_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = make_fixture(root / "input.pdf")
            before = source.read_bytes()
            output = root / "reading"
            report = preflight.check_environment(source, output)
            self.assertTrue(report["programmatic_checks_ok"], report)
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse(list(output.glob(".zhouyi-check-*")))
            with pymupdf.open(source) as doc:
                pix = doc[0].get_pixmap(dpi=120, colorspace=pymupdf.csRGB, alpha=False)
                expected = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            with Image.open(report["preview"]) as actual:
                self.assertEqual(actual.size, expected.size)
                self.assertIsNone(ImageChops.difference(actual, expected).getbbox())
            self.assertTrue(report["agent_checks"])

    def test_invalid_inputs_and_unusable_output_are_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = make_fixture(root / "input.pdf")
            broken = root / "broken.pdf"
            broken.write_bytes(b"broken PDF")
            encrypted = root / "encrypted.pdf"
            with pymupdf.open(source) as doc:
                doc.save(encrypted, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                         owner_pw="owner", user_pw="reader")
            for pdf in [root / "missing.pdf", broken, encrypted]:
                with self.subTest(pdf=pdf.name):
                    self.assertFalse(preflight.check_environment(pdf, root / "out")["programmatic_checks_ok"])
            blocked = root / "file-instead-of-directory"
            blocked.write_text("existing user data")
            report = preflight.check_environment(source, blocked)
            self.assertFalse(report["programmatic_checks_ok"])
            self.assertIsNone(report["preview"])
            self.assertEqual(blocked.read_text(), "existing user data")

    def test_old_python_is_reported_before_dependency_loading(self):
        with patch.object(preflight.sys, "version_info", (3, 9)), \
             patch.object(preflight.importlib, "import_module") as import_module:
            report = preflight.check_environment()
        self.assertFalse(report["programmatic_checks_ok"])
        import_module.assert_not_called()

    def test_uv_is_optional_with_a_compatible_interpreter(self):
        with patch.object(preflight.shutil, "which", return_value=None):
            report = preflight.check_environment()
        self.assertTrue(report["programmatic_checks_ok"], report)
        self.assertIsNone(report["uv"])
        self.assertFalse(report["agent_checks"])
        self.assertTrue(report["next_steps"])


if __name__ == "__main__":
    unittest.main()

"""Behavioral tests for the arXiv LaTeX source mode: unpacking, Chinese setup and XeLaTeX builds."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/zhouyi/scripts/zhouyi_tex.py"
spec = importlib.util.spec_from_file_location("zhouyi_tex", SCRIPT)
zt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zt)

# Set ZHOUYI_TEXBIN to a TeX Live bin directory to run the build tests.
TEXBIN = Path(os.environ["ZHOUYI_TEXBIN"]) if os.environ.get("ZHOUYI_TEXBIN") else None
HAS_TEX = bool(shutil.which("latexmk", path=str(TEXBIN) if TEXBIN else None))
HAS_PYMUPDF = importlib.util.find_spec("pymupdf") is not None

MAIN = r"""\pdfoutput=1
\documentclass[twocolumn]{article}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{times}
\usepackage{amsmath}
\usepackage{algorithm}
\usepackage{algpseudocode}
\title{A Fictional Paper}
\author{Alex Example\thanks{Corresponding author.}}
\begin{document}
\maketitle
\begin{abstract}
这是一段中文摘要，引用 \cite{example2025}。
\end{abstract}
\input{sections/intro}
\section{Method}
% a comment that must stay untouched
The method uses a toy loop\footnote{The loop is fictional.} shown below.
\begin{figure}[t]
\centering
\fbox{FIG}
\caption{A toy figure with $x$ inside.}
\label{fig:toy}
\end{figure}
\begin{algorithm}[t]
\caption{Toy loop}
\begin{algorithmic}[1]
\State Initialize $x \gets 0$ \Comment{start here}
\For{$i = 1$ to $n$}
\State Update the value
\EndFor
\end{algorithmic}
\end{algorithm}
\begin{table}[t]
\centering
\begin{tabular}{lr}
Method & Score \\
Ours & 1.25 \\
\end{tabular}
\caption{虚构结果。}
\label{tab:main}
\end{table}
\bibliographystyle{plain}
\bibliography{refs}
\end{document}
"""
INTRO = r"""\section{Introduction}
注意力头按权重组合数值 $y = 0.25 v_1 + 0.75 v_2$，见表~\ref{tab:main}。
"""
BIB = "@book{example2025, title={A Fictional Baseline}, author={Example, A.}, year={2025}, publisher={Example Press}}\n"


def make_tarball(path: Path, files: dict[str, str]) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


class TexTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.files = {"main.tex": MAIN, "sections/intro.tex": INTRO, "refs.bib": BIB}
        self.tarball = make_tarball(self.root / "paper.src.tar.gz", self.files)
        self.work = self.root / "work"

    def tearDown(self):
        self.temp.cleanup()

    def test_prepare_finds_main_and_included_files_and_guards_the_source(self):
        record = zt.prepare(self.tarball, self.work)
        self.assertEqual(record["main"], "main.tex")
        self.assertEqual(record["tex_files"], ["main.tex", "sections/intro.tex"])
        self.assertEqual((self.work / "src/main.tex").read_text(), MAIN)
        self.assertEqual(zt.prepare(self.tarball, self.work), record)
        other = make_tarball(self.root / "other.tar.gz", {**self.files, "main.tex": MAIN + "%"})
        with self.assertRaisesRegex(zt.TexError, "differs"):
            zt.prepare(other, self.work)

    def test_unsafe_paths_are_rejected(self):
        bad = make_tarball(self.root / "bad.tar.gz", {"../escape.tex": "x", **self.files})
        with self.assertRaisesRegex(zt.TexError, "Unsafe"):
            zt.prepare(bad, self.root / "bad-work")

    def test_patch_comments_font_lines_once_and_inserts_setup(self):
        zt.prepare(self.tarball, self.work)
        build = self.work / "build"
        changes = zt.patch_main(build, "main.tex")
        self.assertEqual(len(changes), 6)
        text = (build / "main.tex").read_text()
        self.assertIn("% zhouyi: \\pdfoutput=1", text)
        self.assertIn("% zhouyi: \\usepackage{times}", text)
        self.assertLess(text.index("\\documentclass"), text.index("\\input{zh-setup.tex}"))
        # no-math rides on the \documentclass line: exactly one line is inserted, as locate() assumes
        self.assertIn("\\PassOptionsToPackage{no-math}{fontspec}\\documentclass[twocolumn]{article}", text)
        self.assertEqual(text.count("\n"), MAIN.count("\n") + 1)
        self.assertTrue((build / "zh-setup.tex").is_file())
        self.assertEqual(zt.patch_main(build, "main.tex"), [])
        self.assertEqual((build / "main.tex").read_text(), text)
        self.assertEqual((self.work / "src/main.tex").read_text(), MAIN)

    def test_font_lines_in_class_files_are_replaced_even_after_a_merge(self):
        cls = ("\\NeedsTeXFormat{LaTeX2e}\n\\ProvidesClass{mystyle}\n\\LoadClass{article}\n"
               "\\RequirePackage{libertine}\n\\RequirePackage{tgheros}\n\\RequirePackage{inconsolata}\n")
        sty = "\\renewcommand{\\rmdefault}{ptm}\n\\usepackage{geometry}\n"
        main = "\\documentclass{mystyle}\n\\usepackage{arxiv}\n\\begin{document}\nText.\n\\end{document}\n"
        tarball = make_tarball(self.root / "cls.tar.gz", {"main.tex": main, "mystyle.cls": cls, "arxiv.sty": sty})
        work = self.root / "cls-work"
        zt.prepare(tarball, work)
        build = work / "build"
        changes = zt.patch_main(build, "main.tex")
        self.assertIn("comment out \\RequirePackage{tgheros} in mystyle.cls", changes)
        self.assertIn("comment out \\renewcommand{\\rmdefault}{ptm} in arxiv.sty", changes)
        self.assertIn("\\RequirePackage{libertine}\n", (build / "mystyle.cls").read_text())
        self.assertEqual((build / "mystyle.cls").read_text().count("\n"), cls.count("\n"))
        flags = "\\def\\ZhouyiRm{}\\def\\ZhouyiSf{}\\def\\ZhouyiTtinconsolata{}\\input{zh-setup.tex}"
        self.assertIn(flags, (build / "main.tex").read_text())
        # A merge rewrites main.tex from the source but leaves the already patched class file;
        # the families must still be found.
        shutil.copyfile(work / "src/main.tex", build / "main.tex")
        zt.patch_main(build, "main.tex")
        self.assertIn(flags, (build / "main.tex").read_text())
        self.assertEqual((work / "src/mystyle.cls").read_text(), cls)

    def test_font_names_leave_package_lists_and_are_found_after_a_merge(self):
        # ICLR templates load \usepackage{iclr2027_conference,times}: the template must stay.
        main = ("\\documentclass{article}\n\\usepackage[final]{iclr2027_conference,times}\n"
                "\\begin{document}\nText.\n\\end{document}\n")
        sty = "\\RequirePackage{inconsolata,amsmath}\n\\RequirePackage{helvet,courier}\n\\RequirePackage{natbib,url}\n"
        tarball = make_tarball(self.root / "list.tar.gz", {"main.tex": main, "iclr2027_conference.sty": sty})
        work = self.root / "list-work"
        zt.prepare(tarball, work)
        build = work / "build"
        changes = zt.patch_main(build, "main.tex")
        self.assertIn("drop times from \\usepackage[final]{iclr2027_conference,times}", changes)
        text = (build / "main.tex").read_text()
        self.assertIn("\\usepackage[final]{iclr2027_conference} % zhouyi: dropped times\n", text)
        self.assertEqual((build / "iclr2027_conference.sty").read_text(),
                         "\\RequirePackage{amsmath} % zhouyi: dropped inconsolata\n"
                         "% zhouyi: dropped helvet,courier: \\RequirePackage{helvet,courier}\n"
                         "\\RequirePackage{natbib,url}\n")
        flags = "\\def\\ZhouyiRm{}\\def\\ZhouyiSf{}\\def\\ZhouyiTt{}\\def\\ZhouyiTtinconsolata{}\\input{zh-setup.tex}"
        self.assertIn(flags, text)
        self.assertEqual(text.count("\n"), main.count("\n") + 1)
        self.assertEqual(zt.patch_main(build, "main.tex"), [])
        # Inconsolata is named only in the style file, which a merge leaves already patched.
        shutil.copyfile(work / "src/main.tex", build / "main.tex")
        zt.patch_main(build, "main.tex")
        self.assertIn(flags, (build / "main.tex").read_text())

    def test_main_file_can_be_named_when_the_package_holds_two_documents(self):
        files = {**self.files, "rebuttal.tex": "\\documentclass{article}\n\\begin{document}\nReply.\n\\end{document}\n"}
        tarball = make_tarball(self.root / "two.tar.gz", files)
        work = self.root / "two-work"
        with self.assertRaisesRegex(zt.TexError, "--main"):
            zt.prepare(tarball, work)
        self.assertFalse((work / "src").exists() or (work / "build").exists())
        record = zt.prepare(tarball, work, "main.tex")
        self.assertEqual(record["tex_files"], ["main.tex", "sections/intro.tex"])
        with self.assertRaisesRegex(zt.TexError, "prepared with main.tex"):
            zt.prepare(tarball, work, "rebuttal.tex")
        with self.assertRaisesRegex(zt.TexError, "not in the source package"):
            zt.prepare(tarball, self.root / "other-work", "missing.tex")

    def test_deliver_includes_override_files_the_source_lacks(self):
        zt.prepare(self.tarball, self.work)
        zt.patch_main(self.work / "build", "main.tex")
        (self.work / "overrides/fonts").mkdir(parents=True)
        (self.work / "overrides/times.sty").write_text("% local stub\n")
        (self.work / "overrides/fonts/extra.sty").write_text("% nested\n")
        (self.work / "overrides/main.tex").write_text((self.work / "build/main.tex").read_text() + "% fixed\n")
        zt.apply_overrides(self.work)
        pdf = self.work / "main.pdf"
        pdf.write_bytes(b"%PDF-1.5\n")
        zt.write_json(self.work / "build-summary.json", {"ok": True, "pdf": str(pdf)})
        paper = self.root / "2601_arXiv_Example"
        paper.mkdir()
        delivered = zt.deliver(paper, self.work)
        self.assertEqual((paper / "zh-tex/times.sty").read_text(), "% local stub\n")
        self.assertEqual((paper / "zh-tex/fonts/extra.sty").read_text(), "% nested\n")
        self.assertTrue((paper / "zh-tex/main.tex").read_text().endswith("% fixed\n"))
        self.assertEqual(delivered["files"], 6)  # three source files, two new ones, zh-setup.tex

    def test_verbatim_environments_the_paper_defines_are_detected(self):
        src = self.root / "defs"
        src.mkdir()
        (src / "macros.sty").write_text(
            "\\newtcblisting[auto counter]{diagnosisbox}[2][]{listing only}\n"
            "% \\newtcblisting{commented}{}\n"
            "\\newenvironment%\n  {code}{\\VerbatimEnvironment\\begin{Verbatim}}{\\end{Verbatim}}\n"
            "\\NewDocumentEnvironment{xcode}{O{}}{\\VerbatimEnvironment\\begin{Verbatim}}{\\end{Verbatim}}\n"
            "\\newenvironment{plain}[1][x]{\\begin{center}}{\\end{center}}\n"
            "\\newminted[pycode]{python}{}\n\\newminted{bash}{}\n")
        (src / "main.tex").write_text(
            "\\begin{lstlisting}\n\\lstnewenvironment{inlisting}{}{}\n\\end{lstlisting}\n"
            "\\lstnewenvironment{prompt}{\\lstset{basicstyle=\\ttfamily}}{}\n")
        self.assertEqual(zt.detect_verbatim_envs(src), ["bashcode", "code", "diagnosisbox", "prompt", "pycode", "xcode"])

    def test_verbatim_bodies_and_inline_verbatim_hide_their_dollars(self):
        text = ("Before the box.\n\\begin{diagnosisbox}{Title}\nRun make -j$(nproc), 100% done.\n"
                "\\begin{lstlisting}\n\\end{diagnosisbox}\nAfter $x$ with \\verb|$HOME| and \\verb*+a$b+, "
                "\\lstinline[language=sh]{echo $1} and \\nolinkurl{x_$y}.\n")
        segs = zt.scan(text, verbatim=zt.VERBATIM_ENVS | {"diagnosisbox"})
        kept = [text[a:b] for kind, a, b in segs if kind == "keep"]
        prose = "".join(text[a:b] for kind, a, b in segs if kind == "text")
        self.assertIn("\\begin{diagnosisbox}{Title}\nRun make -j$(nproc), 100% done.\n\\begin{lstlisting}\n"
                      "\\end{diagnosisbox}", kept)  # ends at the first \end; no nesting, % is text
        for span in ("$x$", "\\verb|$HOME|", "\\verb*+a$b+", "\\lstinline[language=sh]{echo $1}", "\\nolinkurl{x_$y}"):
            self.assertIn(span, kept)
        self.assertIn("Before the box.", prose)
        self.assertIn("After ", prose)

    def test_a_dollar_left_open_across_a_blank_line_stops_the_split(self):
        with self.assertRaisesRegex(zt.TexError, "line 2: \\$ is not closed before a blank line.*--verbatim-env"):
            zt.scan("Intro.\nRun make -j$(nproc) here.\n\nNext paragraph costs $5.\n")

    def test_split_reports_verbatim_environments_and_long_kept_english(self):
        prompt = "Describe the task in plain words and answer briefly. " * 12
        main = MAIN.replace("\\usepackage{amsmath}", "\\usepackage{amsmath}\n\\newtcblisting{promptbox}{}").replace(
            "\\section{Method}", "\\section{Method}\n\\begin{promptbox}\n" + prompt + "cost $5\n\\end{promptbox}\n"
            "\\begin{notebox}\nA $3 fee.\n\\end{notebox}")
        self.tarball = make_tarball(self.root / "boxes.src.tar.gz", {**self.files, "main.tex": main})
        zt.prepare(self.tarball, self.work)
        result = zt.split(self.work, verbatim_envs=["notebox"])
        self.assertEqual(result["verbatim_envs"], ["notebox", "promptbox"])
        self.assertEqual([(k["env"], k["chunk"] is not None) for k in result["kept_prose"]], [("promptbox", True)])
        index = zt.read_json(self.work / "chunks/index.json")
        self.assertEqual(index["verbatim_envs"], ["notebox", "promptbox"])
        method = next(c["id"] for c in index["chunks"] if c["title"] == "Method")
        self.assertIn("The method uses a toy loop", (self.work / f"chunks/{method}.src.tex").read_text())

    def test_commented_begin_inside_a_kept_environment_is_ignored(self):
        text = ("Before.\n\\begin{tabular}{ll}\n    % \\begin{tabular}{\\textwidth}\n"
                "a & b \\\\\n\\end{tabular}\nAfter the table.\n")
        segs = zt.scan(text)
        kept = [text[a:b] for kind, a, b in segs if kind == "keep"]
        self.assertTrue(any(k.startswith("\\begin{tabular}") and k.endswith("\\end{tabular}") for k in kept), kept)
        self.assertIn("After the table.", "".join(text[a:b] for kind, a, b in segs if kind == "text"))

    def test_theorem_names_in_the_preamble_become_chinese_in_the_build_copy(self):
        main = ("\\documentclass{article}\n\\usepackage{amsthm}\n\\newtheorem{theorem}{Theorem}[section]\n"
                "\\newtheorem{lem}[theorem]{Lemma}\n\\newtheorem*{rem}{Remark}\n% \\newtheorem{old}{Theorem}\n"
                "\\newtheorem{gadget}{Gadget}\n\\begin{document}\nText.\n\\end{document}\n")
        tarball = make_tarball(self.root / "thm.tar.gz", {"main.tex": main})
        work = self.root / "thm-work"
        zt.prepare(tarball, work)
        changes = zt.patch_main(work / "build", "main.tex")
        text = (work / "build/main.tex").read_text()
        for line in ("\\newtheorem{theorem}{定理}[section]", "\\newtheorem{lem}[theorem]{引理}",
                     "\\newtheorem*{rem}{注}", "% \\newtheorem{old}{Theorem}", "\\newtheorem{gadget}{Gadget}"):
            self.assertIn(line, text)
        self.assertIn("name Theorem 定理", changes)
        self.assertEqual(text.count("\n"), main.count("\n") + 1)
        self.assertEqual(zt.patch_main(work / "build", "main.tex"), [])

    @unittest.skipUnless(HAS_TEX and HAS_PYMUPDF, "set ZHOUYI_TEXBIN and install PyMuPDF")
    def test_labels_printed_by_packages_come_out_in_chinese(self):
        main = ("\\documentclass{article}\n\\usepackage{algorithm}\n\\usepackage{algpseudocode}\n"
                "\\usepackage{amsthm}\n\\usepackage{cleveref}\n\\newtheorem{theorem}{Theorem}\n"
                "\\begin{document}\n\\section{Intro}\\label{sec:a}\n"
                "\\begin{figure}[h]\\centering\\fbox{A}\\caption{One.}\\label{fig:a}\\end{figure}\n"
                "\\begin{figure}[h]\\centering\\fbox{B}\\caption{Two.}\\label{fig:b}\\end{figure}\n"
                "See \\cref{fig:a,fig:b} and \\cref{sec:a}.\n"
                "\\begin{theorem}\\label{thm:a}Statement.\\end{theorem}\n\\begin{proof}Done.\\end{proof}\n"
                "\\begin{algorithm}[h]\\caption{Loop}\\begin{algorithmic}[1]\n\\Require $x$\n\\Ensure $y$\n"
                "\\For{$i$}\\State step\\EndFor\n\\State \\Return $y$\n\\end{algorithmic}\\end{algorithm}\n"
                "\\end{document}\n")
        self.tarball = make_tarball(self.root / "labels.src.tar.gz", {"main.tex": main})
        chunks, src = self.split()
        for cid, text in src.items():
            (self.work / f"chunks/{cid}.zh.tex").write_text(text)
        summary = zt.build(self.work, None, TEXBIN)
        self.assertTrue(summary["ok"], summary)
        import pymupdf
        pdf_text = "".join("".join(p.get_text().split()) for p in pymupdf.open(summary["pdf"]))
        for label in ("算法1", "输入：", "输出：", "对于", "结束循环", "返回", "定理1", "证明", "图1和2", "第1节"):
            self.assertIn(label, pdf_text)

    def test_commented_headings_do_not_name_a_chunk(self):
        text = ("\\documentclass{article}\n\\begin{document}\nIntro text.\n"
                "% \\section*{AI use statement}\n\\footnote{Corresponding author.}\n\\end{document}\n")
        chunk = {"file": "main.tex", "start": text.index("Intro"), "end": len(text)}
        self.assertEqual(zt.chunk_title(text, chunk, 1, True), "main.tex part 2")
        self.assertFalse(zt.in_comment("a \\% b", 4))
        self.assertTrue(zt.in_comment("a \\\\% b", 6))

    def test_log_summary_counts_errors_missing_glyphs_and_overfull_boxes(self):
        log = ("./sections/intro.tex:2: Undefined control sequence.\n"
               "Missing character: There is no 龘 in font x!\n"
               "Overfull \\hbox (25.0pt too wide) in paragraph\nOverfull \\hbox (3.0pt too wide) in paragraph\n"
               "LaTeX Warning: Citation `x' on page 1 undefined on input line 3.\n")
        summary = zt.summarize_log(log)
        self.assertEqual(summary["errors"], [{"file": "sections/intro.tex", "line": 2, "message": "Undefined control sequence."}])
        self.assertEqual((summary["missing_characters"], summary["overfull_over_20pt"], summary["undefined_references"]), (1, 1, 1))

    def split(self):
        zt.prepare(self.tarball, self.work)
        zt.split(self.work)
        chunks = zt.read_json(self.work / "chunks/index.json")["chunks"]
        return chunks, {c["id"]: (self.work / f"chunks/{c['id']}.src.tex").read_text() for c in chunks}

    def test_split_exposes_prose_and_hides_markup(self):
        chunks, src = self.split()
        prose = {cid: zt.PLACEHOLDER.sub("", text) for cid, text in src.items()}
        everything = "".join(prose.values())
        for text in ["A Fictional Paper", "Corresponding author.", "中文摘要", "注意力头", "toy loop",
                     "The loop is fictional.", "A toy figure with", "Toy loop", "Initialize", "start here",
                     "Update the value", "虚构结果"]:
            self.assertIn(text, everything)
        for text in ["Alex Example", "0.25 v_1", "Ours & 1.25", "a comment that must stay", "example2025",
                     "tab:main", "\\usepackage", "\\begin{figure}"]:
            self.assertNotIn(text, everything)
        self.assertEqual([c["file"] for c in chunks if "Introduction" in c["title"]], ["sections/intro.tex"])
        self.assertEqual([c["title"] for c in chunks][:2], ["front matter", "Abstract"])
        sections = zt.read_json(self.work / "progress.json")["sections"]
        self.assertEqual([s["chunk"] for s in sections], [c["id"] for c in chunks])

    def test_long_sections_are_cut_at_subsections_but_never_across_sections(self):
        body = ("\\section{A}\n" + "a" * 50 + "\n\\subsection{A1}\n" + "b" * 60 + "\n\\subsection{A2}\n"
                + "c" * 60 + "\n\\section{B}\n" + "d" * 30 + "\n")
        text = "\\documentclass{article}\n\\title{T}\n\\begin{document}\n" + body + "\\end{document}\n"
        titles = lambda chunks, source=text: [zt.chunk_title(source, c, k, True) for k, c in enumerate(chunks)]
        self.assertEqual(titles(zt.split_file("main.tex", text, True, 100)), ["front matter", "A", "A1", "A2", "B"])
        self.assertEqual(titles(zt.split_file("main.tex", text, True, 1000)), ["front matter", "A", "B"])
        nested = text.replace("\\section{B}", "\\section{Details for Section~\\ref{sec:a}}")
        self.assertEqual(titles(zt.split_file("main.tex", nested, True, 1000), nested)[-1], "Details for Section~\\ref{sec:a}")
        for chunk in zt.split_file("main.tex", text, True, 100):
            self.assertLessEqual(text[chunk["start"]:chunk["end"]].count("\\section"), 1)
        pieces = zt.split_file("main.tex", text, True, 100)
        self.assertEqual("".join(text[c["start"]:c["end"]] for c in pieces), text)

    def test_restore_keeps_a_control_word_apart_from_following_chinese(self):
        holders = {"⟦P1⟧": "\\xspace", "⟦P2⟧": "\\%", "⟦P3⟧": "\\cite{a}"}
        self.assertEqual(zt.restore("A⟦P1⟧我们⟦P2⟧的⟦P3⟧。", holders), "A\\xspace 我们\\%的\\cite{a}。")

    def test_inputs_with_dots_in_the_name_are_found(self):
        files = {**self.files, "sections/intro.tex": INTRO + "\\input{tables/x_1.7B_Student}\n",
                 "tables/x_1.7B_Student.tex": "Table note text.\n"}
        zt.prepare(make_tarball(self.root / "dots.tar.gz", files), self.work)
        self.assertIn("tables/x_1.7B_Student.tex", zt.read_json(self.work / "tex.json")["tex_files"])

    def test_overrides_are_applied_after_every_merge(self):
        chunks, src = self.split()
        (self.work / "overrides/sections").mkdir(parents=True)
        (self.work / "overrides/sections/intro.tex").write_text("% fixed by hand\n" + INTRO)
        result = zt.merge(self.work)
        self.assertEqual(result["overrides"], ["sections/intro.tex"])
        self.assertTrue((self.work / "build/sections/intro.tex").read_text().startswith("% fixed by hand"))

    def test_a_removed_override_no_longer_shadows_the_source(self):
        self.files["local.sty"] = "\\ProvidesPackage{local}\n"
        self.tarball = make_tarball(self.root / "local.src.tar.gz", self.files)
        self.split()
        overrides = self.work / "overrides"
        overrides.mkdir()
        (overrides / "local.sty").write_text("% patched\n")
        (overrides / "times.sty").write_text("% local stub\n")
        zt.merge(self.work)
        self.assertEqual((self.work / "build/local.sty").read_text(), "% patched\n")
        self.assertTrue((self.work / "build/times.sty").is_file())
        (overrides / "local.sty").unlink()
        (overrides / "times.sty").unlink()
        zt.merge(self.work)
        self.assertEqual((self.work / "build/local.sty").read_text(), "\\ProvidesPackage{local}\n")
        self.assertFalse((self.work / "build/times.sty").exists())
        self.assertEqual((self.work / "build/main.tex").read_text(), MAIN)  # merged files are left to merge

    def test_log_summary_counts_tall_vertical_boxes(self):
        log = "Overfull \\vbox (97.86307pt too high) has occurred while \\output is active\n"
        self.assertEqual(zt.summarize_log(log)["overfull_vbox_over_20pt"], 1)

    def test_merge_of_untouched_chunks_reproduces_the_source(self):
        chunks, src = self.split()
        for cid, text in src.items():
            (self.work / f"chunks/{cid}.zh.tex").write_text(text)
        result = zt.merge(self.work)
        self.assertEqual(result["rejected"], {})
        for rel in ["main.tex", "sections/intro.tex"]:
            self.assertEqual((self.work / "build" / rel).read_text(), self.files[rel])

    def test_merge_refuses_a_source_changed_after_split(self):
        chunks, src = self.split()
        main = self.work / "src/main.tex"
        main.write_text(main.read_text().replace("\\usepackage{times}", "\\usepackage{times}\n"))
        with self.assertRaisesRegex(zt.TexError, "changed after tex-split"):
            zt.merge(self.work)
        main.write_text(MAIN)
        self.assertEqual(zt.merge(self.work)["rejected"], {})
        # Work directories split before the check existed carry no hashes and still merge.
        index = zt.read_json(self.work / "chunks/index.json")
        del index["src_sha256"]
        zt.write_json(self.work / "chunks/index.json", index)
        main.write_text(MAIN + "%")
        self.assertEqual(zt.merge(self.work)["rejected"], {})

    def test_braces_are_checked_against_the_source_chunk_for_new_splits(self):
        holders = {}
        # Old work directories: braces outside placeholders must balance, counted as TeX reads them.
        self.assertEqual(zt.check_chunk("A {b} c", "甲 {乙} 丙", holders), [])
        self.assertEqual(zt.check_chunk("A \\{ b", "甲 \\{ 乙", holders), [])
        self.assertTrue(zt.check_chunk("A b", "甲\\\\{乙", holders))  # \\ then a bare brace
        # New splits: the translation leaves as many groups open as its source chunk.
        self.assertEqual(zt.check_chunk("{A b", "{甲 乙", holders, match_source=True), [])
        self.assertTrue(zt.check_chunk("{A b", "甲 乙", holders, match_source=True))  # a scope dropped
        self.assertTrue(zt.check_chunk("{A b", "{{甲 乙", holders, match_source=True))
        self.assertTrue(zt.check_chunk("A b", "甲\\\\{乙", holders, match_source=True))

    def test_new_splits_record_the_source_brace_rule_and_merge_uses_it(self):
        chunks, src = self.split()
        self.assertEqual(zt.read_json(self.work / "chunks/index.json")["brace_rule"], "source")
        method = next(c["id"] for c in chunks if c["title"] == "Method")
        (self.work / f"chunks/{method}.zh.tex").write_text(src[method] + "{")
        self.assertIn(method, zt.merge(self.work)["rejected"])

    def test_no_cut_inside_a_brace_group_and_unbalanced_files_fall_back(self):
        text = ("\\hypertarget{sec}{\\section{Alpha}\nAlpha text.\n\\subsection{Beta}\nBeta text.}\n"
                "\\section{Gamma}\nGamma text.\n")
        warnings = []
        chunks = zt.split_file("sections/x.tex", text, False, 14000, warnings=warnings)
        spans = [text[c["start"]:c["end"]] for c in chunks]
        self.assertEqual(len(spans), 2)
        self.assertIn("Alpha text.", spans[0]); self.assertIn("Beta text.}", spans[0])
        self.assertIn("Gamma text.", spans[1])
        self.assertTrue(all(zt.brace_net(s) == 0 for s in spans))
        self.assertIn("2 section start(s) inside a brace group", warnings[0])
        warnings = []
        text = "\\section{A}\nText { never closed.\n\\section{B}\nMore text.\n"
        self.assertEqual(len(zt.split_file("sections/y.tex", text, False, 14000, warnings=warnings)), 2)
        self.assertIn("do not balance (+1)", warnings[0])

    def test_merge_rejects_lost_duplicated_or_invented_placeholders(self):
        chunks, src = self.split()
        cid = next(c["id"] for c in chunks if c["title"] == "Method")
        good = src[cid]
        first = zt.PLACEHOLDER.search(good)[0]
        for broken in [good.replace(first, "", 1), good.replace(first, first + first, 1), good + "⟦P999⟧",
                       good + " {", good + " 50%"]:
            with self.subTest(broken=broken[-20:]):
                (self.work / f"chunks/{cid}.zh.tex").write_text(broken)
                result = zt.merge(self.work)
                self.assertIn(cid, result["rejected"])
                self.assertEqual((self.work / "build/main.tex").read_text(), MAIN)

    @unittest.skipUnless(HAS_TEX, "set ZHOUYI_TEXBIN to a TeX Live bin directory")
    def test_build_reverts_only_the_chunk_that_breaks_and_reopens_it(self):
        chunks, src = self.split()
        method = next(c["id"] for c in chunks if c["title"] == "Method")
        intro = next(c["id"] for c in chunks if c["title"] == "Introduction")
        (self.work / f"chunks/{method}.zh.tex").write_text(src[method].replace("Update the value", "更新数值"))
        (self.work / f"chunks/{intro}.zh.tex").write_text(src[intro] + "\\undefinedmacro\n")
        summary = zt.build(self.work, self.root / "paper.zh.pdf", TEXBIN)
        self.assertTrue(summary["ok"], summary)
        self.assertEqual([r["chunk"] for r in summary["reverted_this_run"]], [intro])
        self.assertIn("更新数值", (self.work / "build/main.tex").read_text())
        progress = zt.read_json(self.work / "progress.json")
        self.assertEqual({s["chunk"]: s["status"] for s in progress["sections"]}[intro], "pending")
        self.assertEqual([i["status"] for i in progress["issues"]], ["open"])
        self.assertTrue((self.work / f"chunks/{intro}.zh.tex").is_file())
        (self.work / f"chunks/{intro}.zh.tex").write_text(src[intro])  # repaired
        self.assertIn(intro, zt.build(self.work, None, TEXBIN)["merge"]["merged"])

    @unittest.skipUnless(HAS_TEX, "set ZHOUYI_TEXBIN to a TeX Live bin directory")
    def test_build_stops_instead_of_reverting_when_the_source_itself_fails(self):
        files = {**self.files, "main.tex": MAIN.replace("\\usepackage{amsmath}", "\\usepackage{amsmath}\n\\usepackage{nosuchpackage}")}
        self.tarball = make_tarball(self.root / "broken.src.tar.gz", files)
        chunks, src = self.split()
        for cid, text in src.items():
            (self.work / f"chunks/{cid}.zh.tex").write_text(text)
        summary = zt.build(self.work, None, TEXBIN)
        self.assertFalse(summary["ok"])
        self.assertLessEqual(len(summary["reverted_this_run"]), 1)
        self.assertIn("stopped", summary)

    @unittest.skipUnless(HAS_TEX, "set ZHOUYI_TEXBIN to a TeX Live bin directory")
    def test_an_override_error_at_begin_document_reverts_nothing_and_retry_merges_again(self):
        files = {**self.files, "local.sty": "\\ProvidesPackage{local}\n",
                 "main.tex": MAIN.replace("\\usepackage{amsmath}", "\\usepackage{amsmath}\n\\usepackage{local}")}
        self.tarball = make_tarball(self.root / "hook.src.tar.gz", files)
        chunks, src = self.split()
        for cid, text in src.items():
            (self.work / f"chunks/{cid}.zh.tex").write_text(text)
        front = chunks[0]["id"]
        (self.work / "overrides").mkdir()
        (self.work / "overrides/local.sty").write_text("\\ProvidesPackage{local}\n\\AtBeginDocument{\\undefinedinhook}\n")
        summary = zt.build(self.work, None, TEXBIN)
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["reverted_this_run"], [])  # the error sits on \begin{document}, inside the front chunk
        self.assertIn("did not change", summary["stopped"])
        # An older run reverted the front chunk for that error; once the override is fixed, retry it.
        (self.work / "overrides/local.sty").write_text("\\ProvidesPackage{local}\n")  # the override, fixed
        digest = hashlib.sha256((self.work / f"chunks/{front}.zh.tex").read_bytes()).hexdigest()
        zt.write_json(self.work / "chunks/reverted.json", {front: digest})
        self.assertNotIn(front, zt.build(self.work, None, TEXBIN)["merge"]["merged"])
        summary = zt.build(self.work, None, TEXBIN, retry_reverted=True)
        self.assertTrue(summary["ok"], summary)
        self.assertIn(front, summary["merge"]["merged"])

    @unittest.skipUnless(HAS_TEX, "set ZHOUYI_TEXBIN to a TeX Live bin directory")
    def test_deliver_and_check_follow_the_review_records(self):
        chunks, src = self.split()
        for cid, text in src.items():
            (self.work / f"chunks/{cid}.zh.tex").write_text(text)
        self.assertTrue(zt.build(self.work, None, TEXBIN)["ok"])
        paper = self.root / "2601_arXiv_Example"
        paper.mkdir()
        (self.work / "build/00README.json").write_text('{"process": {"compiler": "pdflatex"}}')
        (self.work / "src/00README.json").write_text('{"process": {"compiler": "pdflatex"}}')
        delivered = zt.deliver(paper, self.work)
        self.assertEqual(zt.read_json(paper / "zh-tex/00README.json")["process"]["compiler"], "xelatex")
        self.assertTrue((paper / "2601_arXiv_Example.zh.pdf").is_file())
        self.assertIn("\\input{zh-setup.tex}", (paper / "zh-tex/main.tex").read_text())
        self.assertFalse(any(p.suffix in {".aux", ".log", ".xdv"} for p in (paper / "zh-tex").rglob("*")))
        result = zt.check(paper, self.work)
        self.assertTrue(result["files_ok"], result)
        self.assertFalse(result["ready"])
        progress = zt.read_json(self.work / "progress.json")
        for section in progress["sections"]:
            section["status"] = "verified"
        progress["document_review"] = {"status": "verified", "note": "Synthetic layout review for tests."}
        zt.write_json(self.work / "progress.json", progress)
        self.assertTrue(zt.check(paper, self.work)["ready"])
        (paper / "zh-tex" / "notes.txt").write_text("not ours")
        (paper / "zh-tex" / "zh-setup.tex").unlink()
        with self.assertRaisesRegex(zt.TexError, "not written by zhouyi"):
            zt.deliver(paper, self.work)
        self.assertEqual(delivered["files"], 5)

    @unittest.skipUnless(HAS_TEX, "set ZHOUYI_TEXBIN to a TeX Live bin directory")
    def test_build_finds_the_pdf_of_a_main_file_in_a_subdirectory(self):
        readme = '{"sources": [{"filename": "texFiles/arxiv.tex", "usage": "toplevel"}]}'
        files = {"texFiles/arxiv.tex": MAIN, "sections/intro.tex": INTRO, "refs.bib": BIB, "00README.json": readme}
        self.tarball = make_tarball(self.root / "subdir.src.tar.gz", files)
        chunks, src = self.split()
        self.assertEqual(zt.read_json(self.work / "tex.json")["main"], "texFiles/arxiv.tex")
        for cid, text in src.items():
            (self.work / f"chunks/{cid}.zh.tex").write_text(text)
        summary = zt.build(self.work, None, TEXBIN)
        self.assertTrue(summary["ok"], summary)
        self.assertEqual(Path(summary["pdf"]), self.work / "build/arxiv.pdf")
        self.assertEqual(summary["undefined_references"], 0)

    @unittest.skipUnless(HAS_TEX, "set ZHOUYI_TEXBIN to a TeX Live bin directory")
    def test_a_stale_failed_log_is_not_read_as_this_build(self):
        zt.prepare(self.tarball, self.work)
        (self.work / "build/main.log").write_text("./sections/intro.tex:2: Undefined control sequence.\n")
        (self.work / "build/main.aux").write_text("\\relax\n\\citation{trunc")  # truncated by a failed run
        summary = zt.build(self.work, None, TEXBIN)
        self.assertTrue(summary["ok"], summary)
        self.assertEqual(summary["errors"], [])

    @unittest.skipUnless(HAS_TEX, "set ZHOUYI_TEXBIN to a TeX Live bin directory")
    def test_build_chinese_pdf_with_bibliography(self):
        zt.prepare(self.tarball, self.work)
        out = self.root / "paper.zh.pdf"
        summary = zt.build(self.work, out, TEXBIN)
        self.assertTrue(summary["ok"], summary)
        self.assertEqual((summary["missing_characters"], summary["undefined_references"]), (0, 0))
        self.assertTrue(out.read_bytes().startswith(b"%PDF"))

    @unittest.skipUnless(HAS_TEX, "set ZHOUYI_TEXBIN to a TeX Live bin directory")
    def test_build_reports_the_failing_file_and_line(self):
        zt.prepare(self.tarball, self.work)
        (self.work / "build/sections/intro.tex").write_text(INTRO + "\\undefinedmacro\n")
        summary = zt.build(self.work, None, TEXBIN)
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["errors"][0]["file"], "sections/intro.tex")
        self.assertEqual(summary["errors"][0]["line"], 3)


if __name__ == "__main__":
    unittest.main()

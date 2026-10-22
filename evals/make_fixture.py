"""Generate a small fictional paper with inspectable layout edge cases."""
from pathlib import Path
import argparse
import pymupdf as fitz


def table(page, y, rows, continued=False):
    xs = [50, 260, 405, 550]
    labels = [("Method", "Score", "Tokens"), *rows]
    bottom = y + 26 * len(labels)
    for x in xs:
        page.draw_line((x, y), (x, bottom), color=(0, 0, 0))
    for i, row in enumerate(labels):
        page.draw_line((50, y + 26 * i), (550, y + 26 * i), color=(0, 0, 0))
        for j, text in enumerate(row):
            page.insert_text((xs[j] + 8, y + 18 + i * 26), text, fontsize=11)
    page.draw_line((50, bottom), (550, bottom), color=(0, 0, 0))
    if continued:
        page.insert_text((50, bottom + 18), "Note: All scores are fictional; units are arbitrary.", fontsize=10)
    return bottom


def make_fixture(path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 45), "Attention Toy: A Fictional Evaluation Paper", fontsize=17)
    page.insert_text((50, 70), "Alex Example and Bo Sample | Example Institute", fontsize=11)
    page.insert_text((50, 105), "Abstract", fontsize=13)
    page.insert_textbox(fitz.Rect(50, 115, 550, 175),
                        "We study a two-value attention layer. This fictional paper tests document translation. "
                        "All reported scores are invented for this evaluation.", fontsize=11)
    page.insert_text((50, 195), "1 Introduction", fontsize=13)
    page.insert_textbox(fitz.Rect(50, 210, 285, 300),
                        "An attention head combines values using weights. In our example, the weights sum to one [1]. "
                        "The method may help on this toy task; no general improvement is claimed.", fontsize=11)
    page.insert_textbox(fitz.Rect(315, 210, 550, 300),
                        "Reading continues in the right column. The input has two tokens. "
                        "Figure 1 shows the computation. Table 1 continues on the next page.", fontsize=11)
    for x, label, color in [(65, "Input", (1, .8, .8)), (245, "Weighted sum", (.8, 1, .8)), (445, "Output", (.8, .8, 1))]:
        page.draw_rect(fitz.Rect(x, 340, x + 90, 405), color=(0, 0, 0), fill=color)
        page.insert_text((x + 6, 375), label, fontsize=10)
    for a, b in [(155, 245), (335, 445)]:
        page.draw_line((a, 372), (b, 372), color=(0, 0, 0), width=2)
    page.insert_text((50, 440), "Figure 1: The toy computation, from input to output.", fontsize=11)
    page.insert_text((50, 495), "Table 1: Fictional results (continued on the next page).", fontsize=11)
    table(page, 515, [("Alpha", "1.25", "16"), ("Beta", "2.50", "24"), ("Gamma", "3.75", "32")])
    page.insert_text((50, 720), "1. We use two values.  2. We keep all conditions fixed.", fontsize=11)
    page.insert_text((50, 760), "Footnote 1: The two tokens are synthetic examples.", fontsize=9)

    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 45), "2 Method", fontsize=13)
    page.insert_text((50, 72), "For query q and values v1 and v2, use the following weighted sum:", fontsize=11)
    page.insert_text((120, 110), "y = 0.25 v1 + 0.75 v2                         (1)", fontsize=13)
    page.insert_text((50, 155), "Table 1 (continued)", fontsize=11)
    table(page, 175, [("Delta", "4.00", "40"), ("Epsilon", "5.25", "48")], continued=True)
    page.draw_rect(fitz.Rect(50, 320, 550, 485), color=(0, 0, 0))
    for y, text in [(343, "Algorithm 1: Weighted combination"), (370, "Input: v1, v2"),
                    (397, "1. Set a = 0.25."), (424, "2. Compute y = a * v1 + (1 - a) * v2."),
                    (455, "3. Return y.")]:
        page.insert_text((65, y), text, fontsize=11)
    page.insert_text((50, 530), "References", fontsize=13)
    page.insert_text((50, 555), "[1] A. Example. A Fictional Baseline. Example Press, 2025.", fontsize=10)
    page.insert_text((50, 620), "A Additional Experiment", fontsize=13)
    page.insert_textbox(fitz.Rect(50, 635, 550, 710),
                        "The rotated figure on the next page has labels at every corner. "
                        "These labels are part of the figure and should remain visible.", fontsize=11)

    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 50), "A.1 Rotated Layout", fontsize=13)
    page.draw_rect(fitz.Rect(70, 140, 530, 570), color=(0, 0, 0), fill=(.95, .97, 1))
    for x, y, text, color in [(80, 170, "TOP LEFT", (1, 0, 0)), (425, 170, "TOP RIGHT", (0, .5, 0)),
                               (80, 550, "BOTTOM LEFT", (0, 0, 1)), (410, 550, "BOTTOM RIGHT", (.7, 0, .7))]:
        page.insert_text((x, y), text, fontsize=11, color=color)
    page.insert_text((50, 620), "Figure 2: A rotated panel with four boundary labels.", fontsize=11)
    page.set_rotation(90)

    scan = fitz.open()
    page = scan.new_page(width=600, height=800)
    page.insert_text((50, 55), "B Prompt Example", fontsize=13)
    page.insert_text((50, 85), "The following prompt is quoted as study material.", fontsize=11)
    page.draw_rect(fitz.Rect(50, 130, 550, 330), color=(0, 0, 0))
    for y, text in [(158, "Prompt 1: Toy task"), (190, "<task>"), (220, "Read {{question}} and explain the weighted sum."),
                    (250, 'Return JSON with the key "answer".'), (290, "</task>")]:
        page.insert_text((65, y), text, fontsize=11)
    page.insert_text((50, 375), "Code example:", fontsize=11)
    page.insert_text((50, 405), "print(2 + 3)", fontsize=12, fontname="cour")
    image = page.get_pixmap(dpi=150).tobytes("png")
    page = doc.new_page(width=600, height=800)
    page.insert_image(page.rect, stream=image)
    scan.close()
    doc.set_metadata({"title": "Attention Toy: A Fictional Evaluation Paper", "author": "Alex Example; Bo Sample"})
    doc.save(path)
    doc.close()
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    print(make_fixture(parser.parse_args().output))

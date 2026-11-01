"""
report.py — downloadable red-flag report (PDF or DOCX).

Layout of both formats:
  1. Header: file, document type, whose side, overall risk, counts,
     summary, not-legal-advice notice, colour legend.
  2. Annotated document: the full text with each flagged clause shaded
     (red = High, orange = Medium, yellow = Low) and a number marker [n]
     at the end of the clause.
  3. Red flag details: one entry per number with the quote, what it
     means, why it matters, and what to ask for.

Hover tooltips can't exist in a file, so section 3 carries the details.
"""

import io
import os
from datetime import datetime
from xml.sax.saxutils import escape

from highlighter import segments

COLORS = {"High": "FECACA", "Medium": "FED7AA", "Low": "FEF08A"}        # shading
INK = {"High": "B91C1C", "Medium": "C2410C", "Low": "A16207"}           # text accents
NAVY = "1E3A5F"
DISCLAIMER = ("LexiGuard AI provides automated decision support, not legal advice. "
              "Have a qualified lawyer review any document before you sign it.")


def _numbered(flags: list[dict]) -> list[dict]:
    return [dict(f, n=i + 1) for i, f in enumerate(flags)]


def _runs(text: str, flags: list[dict]):
    """Return lines: each line is a list of (chunk, level, markers)."""
    lines, cur, pos = [], [], 0
    for run in segments(text, flags):
        run_end = pos + len(run["text"])
        ends = sorted(flags[i]["n"] for i in run["flags"] if flags[i]["end"] == run_end)
        parts = run["text"].split("\n")
        for k, part in enumerate(parts):
            last = k == len(parts) - 1
            if part:
                cur.append((part, run["level"], ends if last else []))
            elif last and ends and cur:
                # clause ended exactly at a line break: attach markers to previous chunk
                chunk, lvl, m = cur[-1]
                cur[-1] = (chunk, lvl, m + ends)
            if not last:
                lines.append(cur)
                cur = []
        pos = run_end
    lines.append(cur)
    return lines


def _meta(data: dict) -> list[tuple[str, str]]:
    flags = data["flags"]
    counts = {lv: sum(1 for f in flags if f["risk_level"] == lv) for lv in ("High", "Medium", "Low")}
    when = data.get("uploaded_at")
    try:
        when = datetime.fromisoformat(when).strftime("%d %b %Y, %H:%M UTC") if when else ""
    except (TypeError, ValueError):
        pass
    return [
        ("File", data.get("filename") or ""),
        ("Document type", data.get("document_label") or "Legal document"),
        ("Reviewed from the side of", (data.get("perspective") or "signing party").title()),
        ("Overall risk", data.get("overall_risk_score") or "-"),
        ("Flags", f"{len(flags)}  (High {counts['High']} · Medium {counts['Medium']} · Low {counts['Low']})"),
        ("Analyzed", when or ""),
    ]


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

def build_docx(data: dict) -> bytes:
    from docx import Document
    from docx.enum.text import WD_BREAK
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    def shade(run, hex_fill):
        rpr = run._r.get_or_add_rPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), hex_fill)
        rpr.append(shd)

    flags = _numbered(data["flags"])
    doc = Document()
    styles = doc.styles
    styles["Normal"].font.name = "Calibri"
    styles["Normal"].font.size = Pt(10.5)

    h = doc.add_heading("LexiGuard AI — Red Flag Report", level=0)
    for r in h.runs:
        r.font.color.rgb = RGBColor.from_string(NAVY)

    table = doc.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    for k, v in _meta(data):
        cells = table.add_row().cells
        cells[0].text, cells[1].text = k, v
        cells[0].paragraphs[0].runs[0].bold = True
        if k == "Overall risk" and v in INK:
            run = cells[1].paragraphs[0].runs[0]
            run.bold = True
            run.font.color.rgb = RGBColor.from_string(INK[v])

    if data.get("contract_summary"):
        p = doc.add_paragraph()
        p.add_run("Summary: ").bold = True
        p.add_run(data["contract_summary"])

    p = doc.add_paragraph()
    r = p.add_run(DISCLAIMER)
    r.italic = True
    r.font.size = Pt(9)

    p = doc.add_paragraph()
    p.add_run("Legend: ").bold = True
    for lv in ("High", "Medium", "Low"):
        r = p.add_run(f" {lv} risk ")
        shade(r, COLORS[lv])
        p.add_run("   ")
    p.add_run("Numbers like [3] point to the details section.").italic = True

    doc.add_heading("Annotated document", level=1)
    if not data.get("document_text"):
        doc.add_paragraph("The original text was not stored for this analysis (it was run before highlighting "
                          "was added). Re-upload the document to get an annotated copy.")
    else:
        for line in _runs(data["document_text"], flags):
            para = doc.add_paragraph()
            para.paragraph_format.space_after = Pt(2)
            for chunk, level, markers in line:
                run = para.add_run(chunk)
                if level:
                    shade(run, COLORS[level])
                for n in markers:
                    m = para.add_run(f"[{n}]")
                    m.font.superscript = True
                    m.bold = True
                    m.font.color.rgb = RGBColor.from_string(INK[flags[n - 1]["risk_level"]])

    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    doc.add_heading("Red flag details", level=1)
    if not flags:
        doc.add_paragraph("No red flags were found.")
    for f in flags:
        title = f"[{f['n']}] {f['category']} — {f['risk_level']} risk"
        if f.get("section_reference"):
            title += f"  ({f['section_reference']})"
        hd = doc.add_heading(title, level=2)
        for r in hd.runs:
            r.font.color.rgb = RGBColor.from_string(INK[f["risk_level"]])
        q = doc.add_paragraph()
        qr = q.add_run(f"“{f['clause_text'].strip()}”")
        qr.italic = True
        shade(qr, COLORS[f["risk_level"]])
        for label, key in (("What it means", "plain_english_explanation"),
                           ("Why it matters", "why_it_matters"),
                           ("What to ask for", "suggested_action")):
            if f.get(key):
                p = doc.add_paragraph()
                p.add_run(f"{label}: ").bold = True
                p.add_run(f[key])

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

_FONT_CANDIDATES = [
    # (regular, bold, italic) — first set found is used; Arial on Windows
    # includes the rupee sign, Helvetica (the fallback) does not.
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/ariali.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf", "/Library/Fonts/Arial Italic.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
     "/System/Library/Fonts/Supplemental/Arial Italic.ttf"),
]


def _pdf_font() -> tuple[str, bool]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.fonts import addMapping

    if "LGSans" in pdfmetrics.getRegisteredFontNames():
        return "LGSans", True
    for reg, bold, ital in _FONT_CANDIDATES:
        if os.path.exists(reg):
            try:
                pdfmetrics.registerFont(TTFont("LGSans", reg))
                pdfmetrics.registerFont(TTFont("LGSans-Bold", bold if os.path.exists(bold) else reg))
                pdfmetrics.registerFont(TTFont("LGSans-Italic", ital if os.path.exists(ital) else reg))
                addMapping("LGSans", 0, 0, "LGSans")
                addMapping("LGSans", 1, 0, "LGSans-Bold")
                addMapping("LGSans", 0, 1, "LGSans-Italic")
                addMapping("LGSans", 1, 1, "LGSans-Bold")
                return "LGSans", True
            except Exception:
                continue
    return "Helvetica", False


def build_pdf(data: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)

    font, unicode_ok = _pdf_font()

    def esc(s: str) -> str:
        s = s or ""
        if not unicode_ok:
            s = s.replace("₹", "Rs. ")
        return escape(s)

    base = ParagraphStyle("base", fontName=font, fontSize=9.8, leading=14, alignment=TA_LEFT)
    small = ParagraphStyle("small", parent=base, fontSize=8.5, leading=11.5, textColor=colors.HexColor("#475569"))
    title = ParagraphStyle("title", parent=base, fontSize=19, leading=24, textColor=colors.HexColor("#" + NAVY))
    h1 = ParagraphStyle("h1", parent=base, fontSize=13.5, leading=18, spaceBefore=10, spaceAfter=6,
                        textColor=colors.HexColor("#" + NAVY))
    docline = ParagraphStyle("docline", parent=base, fontSize=9.6, leading=14.5)
    quote = ParagraphStyle("quote", parent=base, leftIndent=8, borderPadding=(4, 6, 4, 6), spaceBefore=3, spaceAfter=5)

    flags = _numbered(data["flags"])
    story = [Paragraph("LexiGuard AI — Red Flag Report", title), Spacer(1, 4 * mm)]

    meta_rows = [[Paragraph(f"<b>{esc(k)}</b>", base), Paragraph(esc(v), base)] for k, v in _meta(data)]
    t = Table(meta_rows, colWidths=[48 * mm, 122 * mm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F1F5F9")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story += [t, Spacer(1, 3 * mm)]
    if data.get("contract_summary"):
        story.append(Paragraph(f"<b>Summary:</b> {esc(data['contract_summary'])}", base))
    story += [Spacer(1, 2 * mm), Paragraph(f"<i>{esc(DISCLAIMER)}</i>", small), Spacer(1, 2 * mm)]
    legend = " &nbsp; ".join(f'<font backColor="#{COLORS[lv]}">&nbsp;{lv} risk&nbsp;</font>' for lv in ("High", "Medium", "Low"))
    story.append(Paragraph(f"<b>Legend:</b> {legend} &nbsp; Numbers like [3] point to the details section.", small))

    story.append(Paragraph("Annotated document", h1))
    if not data.get("document_text"):
        story.append(Paragraph("The original text was not stored for this analysis. Re-upload the document "
                               "to get an annotated copy.", base))
    else:
        for line in _runs(data["document_text"], flags):
            if not line:
                story.append(Spacer(1, 2.2 * mm))
                continue
            html = []
            for chunk, level, markers in line:
                piece = esc(chunk)
                if level:
                    piece = f'<font backColor="#{COLORS[level]}">{piece}</font>'
                for n in markers:
                    piece += f'<super><font color="#{INK[flags[n - 1]["risk_level"]]}"><b>[{n}]</b></font></super>'
                html.append(piece)
            story.append(Paragraph("".join(html), docline))

    story += [PageBreak(), Paragraph("Red flag details", h1)]
    if not flags:
        story.append(Paragraph("No red flags were found.", base))
    for f in flags:
        ink = INK[f["risk_level"]]
        head = f'<font color="#{ink}"><b>[{f["n"]}] {esc(f["category"])} — {f["risk_level"]} risk</b></font>'
        if f.get("section_reference"):
            head += f' <font color="#64748B">({esc(f["section_reference"])})</font>'
        qs = ParagraphStyle(f"q{f['n']}", parent=quote, backColor=colors.HexColor("#" + COLORS[f["risk_level"]]))
        block = [Paragraph(head, ParagraphStyle("fh", parent=base, fontSize=11, leading=15, spaceBefore=8)),
                 Paragraph(f"<i>“{esc(f['clause_text'].strip())}”</i>", qs)]
        for label, key in (("What it means", "plain_english_explanation"),
                           ("Why it matters", "why_it_matters"),
                           ("What to ask for", "suggested_action")):
            if f.get(key):
                block.append(Paragraph(f"<b>{label}:</b> {esc(f[key])}", base))
        story.append(KeepTogether(block))

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont(font, 7.5)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(18 * mm, 10 * mm, "LexiGuard AI · decision support, not legal advice")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc_.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                      bottomMargin=18 * mm, title=f"LexiGuard report — {data.get('filename', '')}",
                      author="LexiGuard AI").build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()

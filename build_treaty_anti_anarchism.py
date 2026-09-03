"""
Generates the Treaty of Anti-Anarchism — the instrument by which class 8E at
TISB came under Cyvathonian rule — into static/cyvathon-treaty-of-anti-anarchism.pdf.

It goes into static/ rather than the repo root because, unlike the foreign
treaties, this one is meant to be read by citizens from the website.

Build-time only: run `python build_treaty_anti_anarchism.py`. Not part of the
running website, and reportlab is deliberately not in requirements.txt.
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable, Table, TableStyle
)

OUT   = "static/cyvathon-treaty-of-anti-anarchism.pdf"
SIGNED = "2 September 2026"

INK   = colors.HexColor("#0d1117")
GREEN = colors.HexColor("#0e7a5f")      # the colour of settled ground
GREY  = colors.HexColor("#4a5568")
LINE  = colors.HexColor("#cbd5e0")

styles = getSampleStyleSheet()
title_style = ParagraphStyle("T", parent=styles["Title"], fontName="Times-Bold",
    fontSize=25, leading=30, textColor=INK, alignment=TA_CENTER, spaceAfter=4)
sub_style = ParagraphStyle("S", parent=styles["Normal"], fontName="Times-Italic",
    fontSize=13, textColor=GREY, alignment=TA_CENTER, spaceAfter=3)
article_style = ParagraphStyle("A", parent=styles["Heading1"], fontName="Times-Bold",
    fontSize=14, textColor=GREEN, spaceBefore=16, spaceAfter=4)
body_style = ParagraphStyle("B", parent=styles["Normal"], fontName="Times-Roman",
    fontSize=11.5, leading=16.5, textColor=colors.HexColor("#1a202c"),
    alignment=TA_JUSTIFY, spaceAfter=7)
preamble_style = ParagraphStyle("P", parent=body_style, fontName="Times-Italic", leading=17)
note_style = ParagraphStyle("N", parent=body_style, fontSize=10.5, leading=15,
    textColor=GREY, spaceAfter=5)


def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Times-Italic", 8)
    canvas.setFillColor(GREY)
    canvas.drawString(2 * cm, 1.2 * cm, "Treaty of Anti-Anarchism")
    canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, "Page %d" % doc.page)
    canvas.setStrokeColor(LINE)
    canvas.line(2 * cm, 1.5 * cm, A4[0] - 2 * cm, 1.5 * cm)
    canvas.restoreState()


def build():
    doc = SimpleDocTemplate(
        OUT, pagesize=A4,
        leftMargin=2.4 * cm, rightMargin=2.4 * cm, topMargin=2.2 * cm, bottomMargin=2.2 * cm,
        title="Treaty of Anti-Anarchism — the Accession of 8E",
        author="Republic of Cyvathon")
    e = []

    # ---- Title block ----
    e.append(Spacer(1, 1.2 * cm))
    e.append(Paragraph("&#9670;", ParagraphStyle("seal", parent=sub_style,
             fontSize=22, textColor=GREEN)))
    e.append(Paragraph("THE TREATY OF<br/>ANTI-ANARCHISM", title_style))
    e.append(Spacer(1, 0.25 * cm))
    e.append(Paragraph("being an instrument of accession", sub_style))
    e.append(Paragraph("between", sub_style))
    e.append(Paragraph("THE REPUBLIC OF CYVATHON", ParagraphStyle("n", parent=sub_style,
             fontName="Times-Bold", fontSize=14, textColor=INK)))
    e.append(Paragraph("and", sub_style))
    e.append(Paragraph("THE RESIDENTS OF CLASS 8E, TISB", ParagraphStyle("n2", parent=sub_style,
             fontName="Times-Bold", fontSize=14, textColor=INK)))
    e.append(Spacer(1, 0.45 * cm))
    e.append(HRFlowable(width="42%", thickness=1.1, color=GREEN, hAlign="CENTER"))
    e.append(Spacer(1, 0.55 * cm))

    # ---- Preamble ----
    e.append(Paragraph(
        "WHEREAS anarchy is not freedom but the absence of anything agreed, and a room without "
        "settled order is governed all the same &mdash; only by whoever happens to be loudest; "
        "and WHEREAS the Republic of Cyvathon, founded on the twenty-sixth day of May in the "
        "year two thousand and twenty-five, has since that day possessed citizens, a treasury, "
        "a government and a law, but no ground whatsoever upon which to stand; and WHEREAS the "
        "residents of Class 8E at The International School Bangalore, being of one mind on the "
        "matter, have resolved that an order they have chosen themselves is preferable to no "
        "order at all; NOW THEREFORE the Parties agree as follows:",
        preamble_style))

    e.append(Paragraph("ARTICLE I &mdash; The Territory", article_style))
    e.append(Paragraph(
        "The territory acceded to the Republic by this Treaty is <b>Class 8E at The "
        "International School Bangalore</b>, and no other place. It shall be known within the "
        "Republic as the first true territory of Cyvathon, and from the date of this Treaty the "
        "Republic ceases to be a nation of the web alone.", body_style))

    e.append(Paragraph("ARTICLE II &mdash; Accession by Unanimous Consent", article_style))
    e.append(Paragraph(
        "This Treaty was agreed by <b>every member of the class, unanimously</b>. No resident is "
        "bound by it who did not agree to it, and no resident was required to agree. The "
        "unanimity recorded here is the sole foundation of the Republic's title, and if it were "
        "ever untrue the title would fail with it.", body_style))

    e.append(Paragraph("ARTICLE III &mdash; By Consent, Not by Conquest", article_style))
    e.append(Paragraph(
        "The Republic claims this territory by invitation and by nothing else. No conquest is "
        "asserted, none is recognised, and none shall ever be attempted over any person or any "
        "place. The doctrine of the Republic remains, as in the War Room, "
        "<i>defence, not invasion</i>.", body_style))

    e.append(Paragraph("ARTICLE IV &mdash; Rights Retained by the Residents", article_style))
    e.append(Paragraph(
        "Every resident of the territory retains every right, possession and freedom they held "
        "before this Treaty, without exception. Citizenship of Cyvathon is offered, never "
        "imposed: a resident may hold it, decline it, or ignore the Republic entirely, and shall "
        "suffer nothing for doing so. Nothing in this Treaty obliges any person to do anything.",
        body_style))

    e.append(Paragraph("ARTICLE V &mdash; The Limits of This Treaty", article_style))
    e.append(Paragraph(
        "This Article prevails over every other. The authority of The International School "
        "Bangalore, of its teachers and of its staff is absolute within the territory and is in "
        "no way touched, shared or diminished by this Treaty. The Republic claims no power over "
        "any person, their belongings, their conduct or their work; it does not excuse any "
        "school rule, nor any instruction of any teacher, nor any duty a resident owes outside "
        "it. This is a compact of imagination between classmates, and it is to be read as "
        "nothing else.", body_style))

    e.append(Paragraph("ARTICLE VI &mdash; Government of the Territory", article_style))
    e.append(Paragraph(
        "Within the limits of Article V, the territory is governed by the law of Cyvathon as it "
        "stands upon the national website: its Gazette, its Courts, its Legislature and its "
        "Cabinet. The President is Head of State. Ministers hold their briefs subject to "
        "Presidential assent in matters of national policy.", body_style))

    e.append(Paragraph("ARTICLE VII &mdash; Withdrawal", article_style))
    e.append(Paragraph(
        "Any resident may withdraw their own consent at any time, for any reason or none, by "
        "saying so plainly; and upon doing so they cease to be bound by this Treaty in every "
        "respect. The territory itself may be released from the Republic by the same unanimity "
        "that granted it. A union that cannot be left is not consent, and Cyvathon does not "
        "want one.", body_style))

    e.append(Paragraph("ARTICLE VIII &mdash; Entry into Force", article_style))
    e.append(Paragraph(
        f"This Treaty entered into force on <b>{SIGNED}</b>, and is recorded in the National "
        "Timeline of the Republic as the day Cyvathon gained ground beneath it.", body_style))

    # ---- Signatures ----
    e.append(Spacer(1, 0.5 * cm))
    e.append(HRFlowable(width="100%", thickness=1, color=GREEN))
    e.append(Spacer(1, 0.3 * cm))
    e.append(Paragraph(
        "DONE by the unanimous agreement of the residents of Class 8E, the text being "
        "equally authentic to all.",
        ParagraphStyle("done", parent=body_style, fontName="Times-Italic", alignment=TA_CENTER)))
    e.append(Spacer(1, 1.1 * cm))

    sig = Table([
        ["______________________________", "", "______________________________"],
        ["Prathyay", "", "The Residents of Class 8E"],
        ["President of the Republic of Cyvathon", "", "Acceding unanimously, TISB"],
        ["", "", ""],
        [f"Date: {SIGNED}", "", f"Date: {SIGNED}"],
    ], colWidths=[7 * cm, 1.5 * cm, 7 * cm])
    sig.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME", (0, 1), (-1, 1), "Times-Bold"),
        ("FONTSIZE", (0, 1), (-1, 1), 12),
        ("FONTNAME", (0, 2), (-1, 2), "Times-Italic"),
        ("FONTSIZE", (0, 2), (-1, 2), 9.5),
        ("TEXTCOLOR", (0, 2), (-1, 2), GREY),
        ("FONTSIZE", (0, 4), (-1, 4), 10),
        ("TOPPADDING", (0, 0), (-1, 0), 2),
    ]))
    e.append(sig)

    e.append(Spacer(1, 0.9 * cm))
    e.append(HRFlowable(width="30%", thickness=0.7, color=LINE, hAlign="CENTER"))
    e.append(Spacer(1, 0.3 * cm))
    e.append(Paragraph(
        "Cyvathon is a student-run micronation: a game of government played among classmates. "
        "This document has no legal force of any kind and creates no obligation on any person, "
        "school or institution.",
        ParagraphStyle("foot", parent=note_style, alignment=TA_CENTER, fontName="Times-Italic")))

    doc.build(e, onFirstPage=header_footer, onLaterPages=header_footer)
    print("Wrote " + OUT)


if __name__ == "__main__":
    build()

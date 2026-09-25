"""
Generates the Presidential Declaration on the Pufferbuck Debt — the Republic's
acknowledgement of the theft that seeded its economy, the repayment it offered,
and the banishment of Aqualithia — into
static/cyvathon-declaration-pufferbuck-debt.pdf.

Build-time only: run `python build_declaration.py`. Not needed at runtime.
The same words are shown on /foreign (from static/declaration.json, written
alongside the PDF), so edit the text here and rebuild both.
"""
import json
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable, Table, TableStyle, KeepTogether
)

PDF_OUT  = "static/cyvathon-declaration-pufferbuck-debt.pdf"
JSON_OUT = "static/declaration.json"

INK   = colors.HexColor("#0d1117")
GOLD  = colors.HexColor("#a86f00")
GREY  = colors.HexColor("#4a5568")
LINE  = colors.HexColor("#cbd5e0")

TITLE    = "Declaration on the Pufferbuck Debt"
SUBTITLE = "An acknowledgement of wrong, a standing offer of repayment, and the banishment of Aqualithia"
DATE     = "25 September 2026"
REF      = "Presidential Declaration No. 1 of 2026"

PREAMBLE = (
    "A nation that asks its citizens to be honest must first be honest about itself. "
    "This Declaration sets down, for the record and for every citizen who comes after, "
    "a wrong that was done at the very beginning of the Republic, what the Republic "
    "did when it learned of it, and why the Republic now stands apart from Aqualithia."
)

ARTICLES = [
    ("I", "The wrong we acknowledge",
     "In the earliest days of the Republic, before its laws and its institutions stood, "
     "certain persons of the old order took <b>5,000 Pufferbucks</b> that belonged to "
     "Aqualithia. That money became the seed of the Republic's first economy. The Republic "
     "did not know of it at the time, but ignorance is not innocence: Cyvathon began with "
     "money that was not its own. The Republic accepts this, plainly and without excuse, "
     "and accepts as a nation the duty to put it right."),
    ("II", "Justice was done at home",
     "Those responsible were brought before Cyvathonian justice and jailed. They are not "
     "named in this Declaration. The Republic punishes wrongdoers; it does not parade them. "
     "Their crime is recorded so that it is never repeated, not so that it follows them."),
    ("III", "The repayment we offered",
     "The moment the President learned of the theft, the Republic offered to repay "
     "Aqualithia <b>in full, with fifty percent interest: 7,500 Pufferbucks</b> for the "
     "5,000 taken. It was offered freely, before any demand was made, because it was "
     "owed."),
    ("IV", "The answer we received",
     "Aqualithia refused the repayment. It did not want its money back. It wanted war, "
     "and it declared it. Since then its intelligence service has probed the Republic's "
     "systems without pause, and an account of our own was broken into. The Republic "
     "answered with defence and never with attack, and it will keep to that."),
    ("V", "Banishment",
     "The Republic of Aqualithia is hereby <b>banished</b>. From this day Cyvathon extends "
     "to it no recognition, no treaty, no trade, no embassy and no welcome. Its agents "
     "are barred at the border, and every attempt against the Republic will be recorded. "
     "Banishment is a judgement on Aqualithia's government and its war, not on its people."),
    ("VI", "The offer still stands",
     "Banishment does not cancel a debt. A debt is not wiped away because the one it is "
     "owed to chose anger over payment. The Pufferbuck has since been withdrawn, so the "
     "Republic will pay <b>7,500 Crystallines</b>, the same value in full, from the "
     "National Treasury on the day Aqualithia lays down its war and asks for what it is "
     "owed. This offer has no expiry. It is recorded here so that no future government "
     "of Cyvathon may quietly forget it."),
]

CLOSING = (
    "Cyvathon was not born clean. It is choosing to become honest. It has admitted the "
    "wrong, punished those who did it, and offered more than was taken. The rest is "
    "Aqualithia's to decide."
)

LEDGER = [
    ["", "Pufferbucks", "Today"],
    ["Taken at the founding", "5,000 PUFB", "5,000 CRY"],
    ["Interest offered (50%)", "2,500 PUFB", "2,500 CRY"],
    ["Repayment offered", "7,500 PUFB", "7,500 CRY"],
]

SIGNED_BY = "Prathyay"
SIGNED_AS = "President of the Republic of Cyvathon"


def export_json():
    """The web pages show the same words as the PDF."""
    strip = lambda t: t.replace("<b>", "").replace("</b>", "")
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump({
            "title": TITLE, "subtitle": SUBTITLE, "date": DATE, "ref": REF,
            "preamble": PREAMBLE,
            "articles": [{"no": n, "head": h, "text": strip(t)} for n, h, t in ARTICLES],
            "closing": CLOSING, "ledger": LEDGER,
            "signed_by": SIGNED_BY, "signed_as": SIGNED_AS,
            "pdf": "/" + PDF_OUT,
        }, f, ensure_ascii=False, indent=1)
    print("Wrote", JSON_OUT)


def build_pdf():
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("T", parent=styles["Title"], fontName="Times-Bold",
        fontSize=25, leading=30, textColor=INK, alignment=TA_CENTER, spaceAfter=4)
    sub_style = ParagraphStyle("S", parent=styles["Normal"], fontName="Times-Italic",
        fontSize=12.5, leading=16, textColor=GREY, alignment=TA_CENTER, spaceAfter=3)
    ref_style = ParagraphStyle("R", parent=sub_style, fontName="Times-Bold", fontSize=10.5,
        textColor=GOLD, spaceAfter=0)
    article_style = ParagraphStyle("A", parent=styles["Heading1"], fontName="Times-Bold",
        fontSize=14, textColor=GOLD, spaceBefore=15, spaceAfter=4)
    body_style = ParagraphStyle("B", parent=styles["Normal"], fontName="Times-Roman",
        fontSize=11.5, leading=16.5, textColor=colors.HexColor("#1a202c"),
        alignment=TA_JUSTIFY, spaceAfter=7)
    preamble_style = ParagraphStyle("P", parent=body_style, fontName="Times-Italic", leading=17)
    center_style = ParagraphStyle("C", parent=body_style, alignment=TA_CENTER)

    def header_footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Times-Italic", 8); canvas.setFillColor(GREY)
        canvas.drawString(2*cm, 1.2*cm, f"{REF} — {TITLE}")
        canvas.drawRightString(A4[0]-2*cm, 1.2*cm, "Page %d" % doc.page)
        canvas.setStrokeColor(LINE); canvas.line(2*cm, 1.5*cm, A4[0]-2*cm, 1.5*cm)
        canvas.restoreState()

    doc = SimpleDocTemplate(PDF_OUT, pagesize=A4,
        leftMargin=2.4*cm, rightMargin=2.4*cm, topMargin=2.2*cm, bottomMargin=2.2*cm,
        title=TITLE, author="Republic of Cyvathon")
    e = [Spacer(1, 1.0*cm),
         Paragraph("&#9670;", ParagraphStyle("seal", parent=sub_style, fontSize=24, textColor=GOLD)),
         Paragraph("THE REPUBLIC OF CYVATHON", ParagraphStyle("n", parent=sub_style,
                   fontName="Times-Bold", fontSize=12, textColor=INK, spaceAfter=10)),
         Paragraph(TITLE.upper(), title_style),
         Spacer(1, 0.15*cm),
         Paragraph(SUBTITLE, sub_style),
         Spacer(1, 0.25*cm),
         Paragraph(f"{REF} · given {DATE}", ref_style),
         Spacer(1, 0.5*cm),
         HRFlowable(width="42%", thickness=1.1, color=GOLD, hAlign="CENTER"),
         Spacer(1, 0.6*cm),
         Paragraph(PREAMBLE, preamble_style)]

    for no, head, text in ARTICLES:
        e.append(KeepTogether([Paragraph(f"ARTICLE {no} — {head}", article_style),
                               Paragraph(text, body_style)]))
        if no == "III":
            tbl = Table(LEDGER, colWidths=[6.4*cm, 4*cm, 4*cm])
            tbl.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, 0), "Times-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Times-Roman"),
                ("FONTNAME", (0, -1), (-1, -1), "Times-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 11),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, 0), (-1, 0), GOLD),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#fdf8ec"), colors.white]),
                ("GRID", (0, 0), (-1, -1), 0.5, LINE),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ]))
            e += [Spacer(1, 0.15*cm), tbl, Spacer(1, 0.3*cm)]

    e += [Spacer(1, 0.5*cm),
          HRFlowable(width="100%", thickness=0.6, color=LINE),
          Spacer(1, 0.4*cm),
          Paragraph(f"<i>{CLOSING}</i>", center_style),
          Spacer(1, 1.3*cm)]
    sig = Table([["", ""], [SIGNED_BY, DATE], [SIGNED_AS, "Date"]],
                colWidths=[8.2*cm, 5*cm])
    sig.setStyle(TableStyle([
        ("LINEABOVE", (0, 1), (-1, 1), 0.8, INK),
        ("FONTNAME", (0, 1), (-1, 1), "Times-Bold"),
        ("FONTNAME", (0, 2), (-1, 2), "Times-Italic"),
        ("FONTSIZE", (0, 1), (-1, -1), 10.5),
        ("TEXTCOLOR", (0, 2), (-1, 2), GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    e.append(sig)
    doc.build(e, onFirstPage=header_footer, onLaterPages=header_footer)
    print("Wrote", PDF_OUT)


if __name__ == "__main__":
    build_pdf()
    export_json()

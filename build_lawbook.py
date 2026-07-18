"""
Generates the formal Cyvathon Lawbook PDF into static/cyvathon-lawbook.pdf.
Build-time only — run `python build_lawbook.py` whenever the laws change.
Not required at runtime (the PDF is served as a static file).
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, HRFlowable, Table, TableStyle
)

INK   = colors.HexColor("#0d1117")
BLUE  = colors.HexColor("#2b6cb0")
ACCENT= colors.HexColor("#1d6fb8")
GREY  = colors.HexColor("#4a5568")

styles = getSampleStyleSheet()

title_style = ParagraphStyle("Title2", parent=styles["Title"], fontName="Times-Bold",
    fontSize=30, leading=36, textColor=INK, alignment=TA_CENTER, spaceAfter=6)
subtitle_style = ParagraphStyle("Sub2", parent=styles["Normal"], fontName="Times-Italic",
    fontSize=14, textColor=GREY, alignment=TA_CENTER, spaceAfter=4)
article_style = ParagraphStyle("Article", parent=styles["Heading1"], fontName="Times-Bold",
    fontSize=15, textColor=BLUE, spaceBefore=18, spaceAfter=4)
section_style = ParagraphStyle("Section", parent=styles["Heading2"], fontName="Times-Bold",
    fontSize=11.5, textColor=INK, spaceBefore=10, spaceAfter=2)
body_style = ParagraphStyle("Body2", parent=styles["Normal"], fontName="Times-Roman",
    fontSize=11, leading=16, textColor=colors.HexColor("#1a202c"),
    alignment=TA_JUSTIFY, spaceAfter=6)
preamble_style = ParagraphStyle("Preamble", parent=body_style, fontName="Times-Italic",
    fontSize=11.5, leading=17)


def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Times-Italic", 8)
    canvas.setFillColor(GREY)
    canvas.drawString(2*cm, 1.2*cm, "Republic of Cyvathon — Official Lawbook")
    canvas.drawRightString(A4[0]-2*cm, 1.2*cm, "Page %d" % doc.page)
    canvas.setStrokeColor(colors.HexColor("#cbd5e0"))
    canvas.line(2*cm, 1.5*cm, A4[0]-2*cm, 1.5*cm)
    canvas.restoreState()


def build():
    doc = SimpleDocTemplate(
        "static/cyvathon-lawbook.pdf", pagesize=A4,
        leftMargin=2.4*cm, rightMargin=2.4*cm, topMargin=2.2*cm, bottomMargin=2.2*cm,
        title="The Constitution & Lawbook of the Republic of Cyvathon",
        author="Office of the President of Cyvathon",
    )
    e = []

    # -------- TITLE PAGE --------
    e.append(Spacer(1, 4.5*cm))
    e.append(Paragraph("THE CONSTITUTION<br/>&amp; LAWBOOK", title_style))
    e.append(Spacer(1, 0.3*cm))
    e.append(Paragraph("of the", subtitle_style))
    e.append(Paragraph("R E P U B L I C&nbsp;&nbsp;O F&nbsp;&nbsp;C Y V A T H O N", subtitle_style))
    e.append(Spacer(1, 0.6*cm))
    e.append(HRFlowable(width="40%", thickness=1.2, color=BLUE, hAlign="CENTER"))
    e.append(Spacer(1, 0.6*cm))
    e.append(Paragraph("&#9650;", ParagraphStyle("seal", parent=subtitle_style, fontSize=26, textColor=BLUE)))
    e.append(Paragraph("Code. Conquer. Cause Creativity.", subtitle_style))
    e.append(Spacer(1, 3.5*cm))
    e.append(Paragraph("Enacted by the Office of the President · Established 2025",
             ParagraphStyle("foot", parent=subtitle_style, fontSize=10)))
    e.append(PageBreak())

    # -------- PREAMBLE --------
    e.append(Paragraph("PREAMBLE", article_style))
    e.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#cbd5e0")))
    e.append(Spacer(1, 0.2*cm))
    e.append(Paragraph(
        "We, the citizens of the Republic of Cyvathon, a sovereign cyber micronation founded upon "
        "the principles of coding excellence, honest commerce, and the veneration of the Chair, do "
        "hereby ordain and establish this Constitution and Lawbook. Its articles shall govern our "
        "currencies, our companies, our offices of state, and the conduct of every citizen, that "
        "the Republic may prosper in good order and unshaken loyalty.", preamble_style))

    # -------- ARTICLES --------
    articles = [
        ("ARTICLE I — CITIZENSHIP", [
            ("§1. Admission", "Any person may petition for citizenship by registering an account with the "
             "Republic. Upon admission, every new citizen is granted one hundred (100) Cybucks, one "
             "hundred (100) Pufferbucks, and one hundred (100) Aquilines from the National Treasury."),
            ("§2. Equality", "All citizens stand equal before the law and shall be treated with respect "
             "regardless of origin, rank, or office."),
            ("§3. Records", "Every citizen shall hold an official Identity Card recording their designation, "
             "holdings, and permanent record. No record may be falsified."),
            ("§4. Oath of Allegiance", "Any citizen may swear the Oath of Allegiance to the Republic. Upon "
             "swearing the Oath, the citizen is recognised as a full Cyvathonian citizen, and this citizenship "
             "is binding and irrevocable. It may not be renounced, withdrawn, or taken back except by a formal "
             "written revocation of the Oath, submitted on paper to the President and accepted by the same."),
        ]),
        ("ARTICLE II — CURRENCY &amp; THE EXCHEQUER", [
            ("§1. Legal Tender", "The lawful currencies of the Republic are the Cybuck (CB), the Pufferbuck "
             "(PUFB), and the Aquiline (AQ). No other tender shall be recognised in commerce."),
            ("§2. Exchange Rate", "The fixed rates of exchange shall be: one (1) Cybuck equal to five (5) "
             "Pufferbucks; one (1) Pufferbuck equal to ten (10) Aquilines. Accordingly, one Cybuck equals "
             "fifty (50) Aquilines."),
            ("§3. The Treasury", "There shall be a single National Treasury into which all taxes, fees, "
             "repayments, and forfeited assets shall flow, and from which all salaries shall be paid. The "
             "Treasury may be inspected only by the President."),
        ]),
        ("ARTICLE III — TAXATION", [
            ("§1. Value-Added Tax", "A Value-Added Tax of ten percent (10%) of every citizen's holdings shall "
             "be levied each month upon all three currencies and remitted to the National Treasury."),
            ("§2. Universality", "No citizen is exempt from the VAT. Additional levies may be imposed by law "
             "upon those who reject the practice of Chairism."),
        ]),
        ("ARTICLE IV — COMMERCE &amp; COMPANIES", [
            ("§1. Right of Enterprise", "Any citizen may found a company upon payment of one thousand (1,000) "
             "Cybucks to the National Treasury, whereupon they assume the designation of Founder."),
            ("§2. Categories", "Companies shall be registered under a lawful category, being Finance, Selling, "
             "Service, Technology, or Other."),
            ("§3. Salaries", "Citizens shall receive a weekly salary according to their office and designation, "
             "disbursed from the National Treasury. A Founder shall earn five hundred (500) Cybucks per week."),
        ]),
        ("ARTICLE V — LOANS &amp; DEBT", [
            ("§1. Borrowing", "A citizen may borrow from the Treasury a sum not exceeding five thousand (5,000) "
             "Cybucks, to be repaid in full within thirty (30) days."),
            ("§2. Default", "Should a debtor fail to repay within the appointed term, the entirety of their "
             "assets in all currencies shall be seized to the Treasury, and the default shall be entered "
             "permanently upon their record."),
        ]),
        ("ARTICLE VI — GOVERNMENT", [
            ("§1. The President", "The President is Head of State, guardian of the Treasury, and sole authority "
             "empowered to open a national vote. The office is held by Prathyay."),
            ("§2. The Cabinet", "The Government comprises the President, the Prime Minister, the Security "
             "Minister, the Head of Coding, the Head of Hacking, and such other offices as may be created by law."),
            ("§3. Elections", "The Prime Minister and other elective offices shall be chosen by national vote. "
             "Upon the close of a vote, the candidate with the most ballots is installed into office."),
        ]),
        ("ARTICLE VII — VOTING", [
            ("§1. Convening", "Only the President may convene a vote and set its candidates."),
            ("§2. Suffrage", "Every citizen is entitled to one ballot per vote. No citizen may vote twice in the "
             "same matter, and votes once cast are final."),
            ("§3. The Presidential Vote", "A national presidential vote shall be convened once every six (6) "
             "years, whereby the citizens shall elect or confirm the Head of State of the Republic."),
        ]),
        ("ARTICLE VIII — CONDUCT &amp; CRIMINAL LAW", [
            ("§1. Respect", "Citizens shall respect all nations, communities, and one another. Inappropriate "
             "language or behaviour is forbidden."),
            ("§2. Prohibited Acts", "Theft, fraud, murder, unlawful attack, and all illegal activity are "
             "prohibited. Attacks are permitted only as the zoning laws allow."),
            ("§3. Penalties", "Violations shall be recorded upon the offender's permanent record and may be "
             "punished by fine, seizure of assets, or loss of office."),
        ]),
        ("ARTICLE IX — CHAIRISM", [
            ("§1. The Faith", "Chairism is the valued faith of the Republic. We honour the prophets of the "
             "Chair, and our loyalty remains unshaken. ALL HAIL THE CHAIR."),
        ]),
        ("ARTICLE X — AMENDMENT", [
            ("§1. Authority", "This Constitution and Lawbook may be amended only by the President, whose decree "
             "shall be entered into the national law and published to all citizens."),
        ]),
    ]

    for title, sections in articles:
        e.append(Paragraph(title, article_style))
        e.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#cbd5e0")))
        for head, text in sections:
            e.append(Paragraph(head, section_style))
            e.append(Paragraph(text, body_style))

    # -------- RATIFICATION --------
    e.append(Spacer(1, 0.8*cm))
    e.append(HRFlowable(width="100%", thickness=1, color=BLUE))
    e.append(Spacer(1, 0.4*cm))
    e.append(Paragraph(
        "Done and ratified by the Office of the President of the Republic of Cyvathon, "
        "in the year two thousand twenty-five, and in force from that day forward.",
        ParagraphStyle("ratify", parent=body_style, fontName="Times-Italic", alignment=TA_CENTER)))
    e.append(Spacer(1, 1*cm))
    sig = Table([["_______________________"], ["Prathyay, President of Cyvathon"]],
                colWidths=[8*cm])
    sig.setStyle(TableStyle([
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("FONTNAME", (0,1), (0,1), "Times-Italic"),
        ("FONTSIZE", (0,1), (0,1), 10),
        ("TEXTCOLOR", (0,1), (0,1), GREY),
    ]))
    e.append(sig)

    doc.build(e, onFirstPage=lambda c, d: None, onLaterPages=header_footer)
    print("Lawbook written to static/cyvathon-lawbook.pdf")


if __name__ == "__main__":
    build()

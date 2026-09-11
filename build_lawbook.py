"""
Generates the Lawbook of the Republic of Cyvathon into static/cyvathon-lawbook.pdf.

The Constitution (build_constitution.py) says how the Republic is governed.
The Lawbook says what a citizen keeps to, day to day: conduct, money, trade,
debt, the services and the courts. It is written to be read, not obeyed.

Build-time only — run `python build_lawbook.py` whenever the laws change.
Not required at runtime (the PDF is served as a static file).
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, HRFlowable, Table, TableStyle,
    KeepTogether,
)

OUT     = "static/cyvathon-lawbook.pdf"
ISSUED  = "11 September 2026"

INK    = colors.HexColor("#0d1117")
BLUE   = colors.HexColor("#2b6cb0")
GREY   = colors.HexColor("#4a5568")
LINE   = colors.HexColor("#cbd5e0")

styles = getSampleStyleSheet()
title_style = ParagraphStyle("T", parent=styles["Title"], fontName="Times-Bold",
    fontSize=30, leading=36, textColor=INK, alignment=TA_CENTER, spaceAfter=6)
sub_style = ParagraphStyle("S", parent=styles["Normal"], fontName="Times-Italic",
    fontSize=14, textColor=GREY, alignment=TA_CENTER, spaceAfter=4)
chapter_style = ParagraphStyle("C", parent=styles["Heading1"], fontName="Times-Bold",
    fontSize=15, textColor=BLUE, spaceBefore=18, spaceAfter=4)
section_style = ParagraphStyle("H", parent=styles["Heading2"], fontName="Times-Bold",
    fontSize=11.5, textColor=INK, spaceBefore=10, spaceAfter=2)
body_style = ParagraphStyle("B", parent=styles["Normal"], fontName="Times-Roman",
    fontSize=11, leading=16, textColor=colors.HexColor("#1a202c"),
    alignment=TA_JUSTIFY, spaceAfter=6)
intro_style = ParagraphStyle("P", parent=body_style, fontName="Times-Italic",
    fontSize=11.5, leading=17)
note_style = ParagraphStyle("N", parent=body_style, fontSize=10, leading=14.5,
    textColor=GREY, alignment=TA_CENTER, fontName="Times-Italic")

INTRO = (
    "This Lawbook collects the laws a citizen of Cyvathon keeps to. It is issued under the "
    "Constitution of the Republic, which governs how these laws are made and changed: by the "
    "citizens, in the Legislature, in the open. Read it once and you will know how the "
    "Republic expects you to treat one another, your money, your trades, your debts and its "
    "courts. None of it is a trap; all of it is on the website."
)

CHAPTERS = [
    ("CHAPTER 1 — CONDUCT", [
        ("Respect",
         "Treat every citizen, every community and every other nation with respect. "
         "Harassment, abuse, threats and inappropriate language have no place in the public "
         "square, in chat, in blogs, in video or in the press."),
        ("Honesty",
         "Do not steal, defraud, scam or cheat. Do not falsify a record, impersonate another "
         "citizen, or claim an office you do not hold. A trade agreed is a trade kept."),
        ("Property",
         "What another citizen has earned is theirs. What you carry for Cyvazon you hand over. "
         "What you borrow from Cyvalend you give back on time — an overdue item is somebody "
         "else's property."),
        ("Peace",
         "No citizen may attack another. The Republic's own doctrine is <i>defence, not "
         "invasion</i>, and it applies to citizens as much as to nations. Play fights are a "
         "matter for the school's rules, which this Lawbook does not touch, and are never done "
         "in Cyvathon's name."),
    ]),
    ("CHAPTER 2 — MONEY", [
        ("Legal tender",
         "The lawful currencies are the Cybuck (CB), the Pufferbuck (PUFB), the Aquiline (AQ) "
         "and the Cybit (CBT). One Cybuck equals one Pufferbuck, ten Aquilines and fifty "
         "Cybits. Cybits are the small change of a Cybuck and are kept automatically."),
        ("The welcome grant",
         "Every new citizen receives one hundred (100) of each currency. The grant cannot be "
         "sent to anyone else — it is yours to start with, not to give away."),
        ("Tax",
         "A Value-Added Tax of ten percent (10%) is collected from every citizen's holdings "
         "each month, together with a five percent (5%) delivery levy while Cyvazon runs. "
         "Both go to the National Treasury, whose holdings every citizen may inspect. The rates "
         "are set by law and published; no one is taxed for their beliefs."),
        ("Savings and bonds",
         "The Bank pays five percent (5%) monthly interest on savings. A Government Bond "
         "returns ten percent (10%) after thirty (30) days."),
        ("Payments",
         "Money may be sent by username, by scanning a citizen's debit card, or through "
         "Cyvapay on any website. A company may be paid the same way."),
        ("Invitations",
         "A citizen who invites a friend is paid five hundred (500) Cybucks by the Treasury "
         "when that friend is admitted."),
        ("The Casino",
         "The Casino takes bets in Cybucks against the Treasury, at the coin flip, the dice and "
         "the slots. The House can win, and often does; bet what you can afford to lose."),
    ]),
    ("CHAPTER 3 — TRADE AND WORK", [
        ("Companies",
         "Any citizen may found a company for one thousand (1,000) Cybucks, registered under "
         "a lawful category — Finance, Selling, Service, Technology or Other — and becomes its "
         "Founder. A company may go public and its shares be traded on the Stock Exchange; it "
         "may pay dividends to its shareholders."),
        ("Jobs",
         "Any citizen may apply to any company for a salaried role through the Jobs Board. "
         "An employer pays the wage they promised."),
        ("Markets",
         "Goods are bought and sold in the national Import &amp; Export hub and in each state's "
         "own marketplace. A sale raises a Cyvazon delivery so the goods actually change hands."),
        ("Salaries",
         "Salaries are paid weekly from the Treasury by office: Prime Minister 1,000 CB; "
         "Minister, Security Minister and Judge 900; Head of Coding, Head of Hacking and "
         "Founder 800; Employee 500; Citizen 100. Approved couriers draw 500 CB besides. "
         "The President draws nothing."),
    ]),
    ("CHAPTER 4 — CREDIT AND DEBT", [
        ("Loans",
         "A citizen may borrow up to five thousand (5,000) Cybucks from the Treasury and must "
         "repay it within thirty (30) days. Borrowed money cannot be sent away — only money "
         "you have earned can."),
        ("Falling behind",
         "A loan not repaid by the end of its term is called in: the Treasury takes your "
         "Cybucks, Pufferbucks and Aquilines in settlement — all of them, not only what you "
         "owe — and the default goes on your record. It is the hardest rule in the Republic, "
         "so do not borrow what you cannot repay in thirty days. While a loan is still owed you "
         "may not stand for office; once it is repaid or called in, you may."),
    ]),
    ("CHAPTER 5 — THE PUBLIC SERVICES", [
        ("Cyvazon",
         "Delivery is free for whoever requests it, anywhere in the territory: say which class "
         "it is collected from and which it goes to. Couriers are approved before they carry "
         "anything, may not carry their own parcels, and may not stand down while holding one. "
         "Only the person receiving a parcel may confirm it arrived."),
        ("Cyvashield",
         "Free cover against theft, lost parcels, bad trades and scams. Pick a plan; report a "
         "loss honestly; the claim is checked against the record and ruled on. A claim found "
         "fraudulent is itself an offence."),
        ("Cyvalend",
         "Borrow what you forgot — a calculator, a pen, a charger — free of charge, and return "
         "it by the time agreed. An owner may ask a returnable deposit of up to two hundred "
         "(200) Cybucks, which comes back to you when the item does. The owner confirms the "
         "return."),
        ("The Armoury",
         "Hand in a G2 pen for the Republic's defence and the Armoury desk pays the "
         "war-effort rate, four hundred (400) Cybucks a round, delivered by Cyvazon to the "
         "Registrar. Every round is logged."),
        ("Card packets",
         "Trade Match Attax cards with other citizens. An accepted trade swaps the cards and "
         "raises a parcel each way, so the real cards move too."),
    ]),
    ("CHAPTER 6 — TRAVEL", [
        ("Passports",
         "Issue a Passport on the Passport page before crossing any state border. Border "
         "Control stamps it as you go; the stamps are yours to collect."),
        ("The states",
         "Neonhaven, Cryptvale, Silica Plains, Portus Mare and Aetheris each keep a "
         "marketplace and a channel of their own. Settle in a state to become its resident and "
         "join its channel; you may settle again elsewhere later."),
        ("The capital",
         "Aetheris, the seat of the President, admits sworn citizens: swear the Oath of "
         "Allegiance to enter. The Oath is voluntary, and binding once sworn."),
    ]),
    ("CHAPTER 7 — ELECTIONS AND OFFICE", [
        ("Voting",
         "Every citizen has one vote in every election and on every bill. A vote cast is "
         "final. Elections for the Prime Minister and the Judge are convened by the President, "
         "who names the candidates on the ballot; a vacant ministry opens its own election the "
         "moment four eligible citizens stand. The President may also appoint a citizen to a "
         "ministry, or dismiss a minister."),
        ("Standing",
         "Any citizen may stand for a vacant ministry who is not serving a sentence, does not "
         "carry a conviction, and does not still owe a loan."),
        ("Making law",
         "Any citizen may table a bill. It is debated and voted on by all; with more Ayes than "
         "Nays it passes to the President for assent, and on assent it becomes an Act, "
         "numbered and published in the Gazette."),
    ]),
    ("CHAPTER 8 — JUSTICE", [
        ("Reporting a crime",
         "Any citizen may file a report (an FIR) with evidence. Any citizen may bring a case "
         "before the National Court."),
        ("Hearing",
         "Both sides argue before the elected Judge. The President may also sit, and sits alone "
         "when no Judge holds office. No one rules on a case they are party to."),
        ("Sentences",
         "A sentence of the Court is a fine, a term in jail, or both; no term exceeds three "
         "hundred and sixty-five (365) days. A jailed citizen is confined to the jail page until "
         "the term is served, then released. Convictions go on the criminal record and bar a "
         "citizen from standing for office. The President may pardon."),
        ("Detention by order",
         "The President may also jail a citizen by order, without a case, for a stated reason "
         "and for no more than 365 days. The order and its reason go on the citizen's public "
         "record the day it is made, and count as a conviction. It is the one judgment in the "
         "Republic given without a hearing, and the Constitution names it (Article IV) so that "
         "nobody is surprised by it."),
    ]),
    ("CHAPTER 9 — CULTURE", [
        ("Chairism",
         "Chairism is the Republic's valued culture and is encouraged as a cultural practice. "
         "It is never required, and no citizen is taxed, fined or disadvantaged for declining "
         "it. All hail the Chair — if you like."),
    ]),
    ("CHAPTER 10 — HOW THESE LAWS CHANGE", [
        ("By the citizens",
         "These laws are changed by Act of the Legislature, as the Constitution provides, and "
         "every change is published in the Gazette. The national levers — tax rates, the "
         "courier wage, the Armoury rate, loan limits — may be moved by the President, or "
         "proposed by a minister and assented to, and their current values are always shown "
         "on the website. Where this Lawbook and the Constitution differ, the Constitution "
         "prevails."),
    ]),
]

CLOSING = ("Issued under the Constitution of the Republic of Cyvathon on " + ISSUED +
           ", in place of the Lawbook of 2025.")

DISCLAIMER = (
    "Cyvathon is a student-run micronation: a game of government played among classmates. "
    "This document has no legal force of any kind and creates no obligation on any person, "
    "school or institution."
)


def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Times-Italic", 8)
    canvas.setFillColor(GREY)
    canvas.drawString(2 * cm, 1.2 * cm, "Republic of Cyvathon — The Lawbook")
    canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, "Page %d" % doc.page)
    canvas.setStrokeColor(LINE)
    canvas.line(2 * cm, 1.5 * cm, A4[0] - 2 * cm, 1.5 * cm)
    canvas.restoreState()


def build():
    doc = SimpleDocTemplate(
        OUT, pagesize=A4,
        leftMargin=2.4 * cm, rightMargin=2.4 * cm, topMargin=2.2 * cm, bottomMargin=2.2 * cm,
        title="The Lawbook of the Republic of Cyvathon",
        author="The Legislature of the Republic of Cyvathon",
    )
    e = []

    # -------- TITLE PAGE --------
    e.append(Spacer(1, 4.5 * cm))
    e.append(Paragraph("THE LAWBOOK", title_style))
    e.append(Spacer(1, 0.3 * cm))
    e.append(Paragraph("of the", sub_style))
    e.append(Paragraph("R E P U B L I C&nbsp;&nbsp;O F&nbsp;&nbsp;C Y V A T H O N", sub_style))
    e.append(Spacer(1, 0.6 * cm))
    e.append(HRFlowable(width="40%", thickness=1.2, color=BLUE, hAlign="CENTER"))
    e.append(Spacer(1, 0.6 * cm))
    e.append(Paragraph("&#9650;", ParagraphStyle("seal", parent=sub_style, fontSize=26, textColor=BLUE)))
    e.append(Paragraph("Code. Conquer. Cause Creativity.", sub_style))
    e.append(Spacer(1, 3.5 * cm))
    e.append(Paragraph("Issued under the Constitution &middot; " + ISSUED,
             ParagraphStyle("foot", parent=sub_style, fontSize=10)))
    e.append(PageBreak())

    # -------- INTRODUCTION --------
    e.append(Paragraph("TO THE CITIZEN", chapter_style))
    e.append(HRFlowable(width="100%", thickness=0.6, color=LINE))
    e.append(Spacer(1, 0.2 * cm))
    e.append(Paragraph(INTRO, intro_style))

    # -------- CHAPTERS --------
    for title, sections in CHAPTERS:
        block = [Paragraph(title, chapter_style), HRFlowable(width="100%", thickness=0.6, color=LINE)]
        for i, (head, text) in enumerate(sections, 1):
            block.append(Paragraph("&sect;%d. %s" % (i, head), section_style))
            block.append(Paragraph(text, body_style))
        e.append(KeepTogether(block[:4]))
        e.extend(block[4:])

    # -------- CLOSING --------
    e.append(Spacer(1, 0.8 * cm))
    e.append(HRFlowable(width="100%", thickness=1, color=BLUE))
    e.append(Spacer(1, 0.4 * cm))
    e.append(Paragraph(CLOSING, ParagraphStyle("ratify", parent=body_style,
             fontName="Times-Italic", alignment=TA_CENTER)))
    e.append(Spacer(1, 1 * cm))
    sig = Table([
        ["______________________________", "", "______________________________"],
        ["The Legislature of Cyvathon", "", "Prathyay"],
        ["by whom these laws are made", "", "President of the Republic"],
        ["", "", ""],
        ["Date: " + ISSUED, "", "Date: " + ISSUED],
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
    e.append(Paragraph(DISCLAIMER, note_style))

    doc.build(e, onFirstPage=lambda c, d: None, onLaterPages=header_footer)
    print("Lawbook written to " + OUT)


if __name__ == "__main__":
    build()

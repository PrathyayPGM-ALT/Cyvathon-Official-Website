"""
Generates the Constitution of the Republic of Cyvathon.

Two outputs, from one text, so the page and the PDF can never disagree:
  static/cyvathon-constitution.pdf   — the formal document citizens download
  static/constitution.json           — the same articles, read by /constitution

Build-time only: run `python build_constitution.py` whenever the text changes.
reportlab is deliberately not in requirements.txt; the site only serves the files.

Every clause here describes something the website actually does. Where the
code gives a power to the President, the Constitution says so plainly and then
says what bounds it — that is the difference between a republic and a decree.
"""
import json
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, HRFlowable, Table, TableStyle,
    KeepTogether,
)

PDF_OUT  = "static/cyvathon-constitution.pdf"
JSON_OUT = "static/constitution.json"
ADOPTED  = "11 September 2026"

# ---------------------------------------------------------------------------
#  THE TEXT
# ---------------------------------------------------------------------------

PREAMBLE = (
    "We, the citizens of the Republic of Cyvathon — a micronation founded on the twenty-sixth "
    "day of May in the year two thousand and twenty-five, built by its own citizens around "
    "code, creativity and community — do adopt this Constitution as the founding law of our "
    "Republic. We hold that a nation is its people and not its ruler. We hold that every office "
    "exists to serve the citizens who fill the treasury and cast the ballots. We hold that law is "
    "made in the open, argued in the open and recorded in the open. And we hold that nothing in "
    "Cyvathon is held by force, because everything in Cyvathon was joined by choice. Our motto is "
    "<i>Code. Conquer. Cause Creativity.</i> — and what we conquer is problems, never people."
)

ARTICLES = [
    {
        "num": "I", "title": "The Republic",
        "sections": [
            ("Name and founding",
             "The nation is the Republic of Cyvathon. It was founded on 26 May 2025 and its "
             "website — the seat of its government and the ground on which its laws run — went "
             "live on 31 May 2025. Its motto is <i>Code. Conquer. Cause Creativity.</i>"),
            ("A nation of citizens",
             "Cyvathon is its citizens. Its institutions — the Treasury, the Legislature, the "
             "Courts, the Cabinet and the public services — exist to serve them, and draw every "
             "power they hold from this Constitution and from the citizens' consent."),
            ("Territory held by consent",
             "The Republic's first true territory is Class 8E at The International School "
             "Bangalore, acceded by the Treaty of Anti-Anarchism on 2 September 2026 by the "
             "unanimous agreement of everyone in the class. It is held by invitation and by "
             "nothing else. The authority of the school, its teachers and its staff is absolute "
             "there and is in no way touched by this Constitution; any resident may withdraw "
             "their consent at any time and the territory may be released by the same unanimity "
             "that granted it. Conquest is neither claimed nor recognised, anywhere, ever."),
            ("The states",
             "Within the Republic's own world lie five states — Neonhaven, Cryptvale, Silica "
             "Plains, Portus Mare and Aetheris, the national capital and seat of the President. "
             "Each keeps its own marketplace and its own channel; citizens travel between them "
             "on a passport and settle where they choose."),
        ],
    },
    {
        "num": "II", "title": "Citizenship",
        "sections": [
            ("Admission",
             "Any person may seek citizenship by registering with the Republic. New citizens are "
             "reviewed by the President before their first login, for one purpose only: to keep "
             "bad actors out. A fellow micronation may register as a foreign nation."),
            ("The welcome grant",
             "On admission every citizen receives one hundred (100) of each currency from the "
             "National Treasury. The grant is a gift to start with, not money to send away: it "
             "cannot be transferred out of the citizen's own accounts."),
            ("Identity",
             "Every citizen holds an Identity Card recording their designation, holdings and "
             "record. No citizen may falsify a record, impersonate another citizen, or hold "
             "themselves out as an office they do not fill."),
            ("Equality",
             "All citizens stand equal before the law and before every institution of the "
             "Republic, whatever their origin, rank, office, wealth or belief."),
            ("The Oath of Allegiance",
             "Any citizen may swear the Oath of Allegiance. Swearing it is entirely voluntary; "
             "no service, office or right is withheld from a citizen who has not sworn, save "
             "entry to the capital. Once sworn the Oath is binding, and may be renounced only "
             "by a written request on paper to the President."),
        ],
    },
    {
        "num": "III", "title": "Rights and Freedoms",
        "sections": [
            ("Voice",
             "Every citizen may speak, write, publish and broadcast — in the public square, in "
             "blogs, in video and in the press — subject only to the law of conduct, which "
             "forbids harassment, fraud and abuse and nothing else."),
            ("Property and enterprise",
             "Every citizen may earn, hold, save, invest, lend and spend money; found a company, "
             "take it public and trade its shares; and buy and sell in any market of the "
             "Republic. What a citizen has earned is theirs, and may be taken only by a "
             "judgment of the Court, by a tax laid down in law, or in settlement of a loan the "
             "citizen has not repaid (Article VIII &sect;5)."),
            ("The ballot",
             "Every citizen has one vote in every election and one vote on every bill, cast once "
             "and final. Every citizen may stand for any vacant ministry, unless they are serving "
             "a sentence, carry a conviction on their record, or still owe a loan. For the "
             "Chancellor and the Judge the President convenes the vote and names the candidates on "
             "the ballot; the citizens choose between them."),
            ("The law",
             "Every citizen may table a bill before the Legislature, may bring a case before the "
             "Court, may report a crime, and is entitled to be heard before the Court gives any "
             "judgment against them. No citizen may be judged by a person who is a party to "
             "their case."),
            ("Belief",
             "Chairism is the valued culture of the Republic and is honoured by it. It is never "
             "compelled. No citizen is taxed, fined, ranked, or disadvantaged in any way for "
             "declining to practise it."),
            ("Openness",
             "The Treasury's holdings, the Gazette, the record of every Act and decree, the "
             "National Timeline, the proceedings of the Legislature and the Court, and every "
             "citizen's public record are open to every citizen to read."),
        ],
    },
    {
        "num": "IV", "title": "The President",
        "sections": [
            ("Office",
             "The President is Head of State. The office is held by the founder of the "
             "Republic, Prathyay. Once every six years a national vote, which the President is "
             "bound to convene, is held in which the citizens elect or confirm the President."),
            ("Powers",
             "The President: administers the National Treasury and sets the national levers of "
             "the economy; gives or withholds assent to bills passed by the Legislature; issues "
             "decrees within the law; names the Vice President; convenes national votes for the "
             "Chancellor and the Judge and names the candidates, and may name a Chancellor to "
             "serve until the citizens elect one; may appoint a citizen to a ministry, or dismiss a "
             "minister; reviews new citizens and approves couriers; confirms alliances with "
             "other nations; may sit in the Court, and sits alone when no Judge is elected; may "
             "detain a citizen by order, for a stated reason and a stated term; may release or "
             "pardon a citizen; and keeps the National Timeline. This list is complete: a power "
             "not written here is not held."),
            ("Limits",
             "The President is bound by this Constitution. A decree may not repeal an Act of the "
             "Legislature, override a judgment of the Court, or amend this Constitution. The "
             "President may not rule on a case to which they are a party. A detention by "
             "presidential order may not exceed three hundred and sixty-five (365) days, is "
             "entered on the citizen's public record with its reason on the day it is made, and "
             "counts as a conviction; it is the one judgment given without a hearing in the "
             "Republic, and this Article names it so that no citizen is surprised by it. Every "
             "decree, assent, appointment, detention and pardon is entered in the public record."),
            ("No salary",
             "The President draws no salary. The Treasury is held for the nation and spent on "
             "it, and the person who holds it takes nothing from it."),
            ("The Vice President",
             "The Vice President is named by the President and stands beside them, deputising "
             "at the President's request and in their absence from the ceremonies of state. The "
             "office carries none of the President's powers by right."),
        ],
    },
    {
        "num": "V", "title": "The Cabinet and the Ministries",
        "sections": [
            ("The Chancellor",
             "The Chancellor leads the Cabinet. The office is filled by national vote, which the "
             "President convenes; until one is held, the President may name a Chancellor."),
            ("Ministries",
             "Ministries are created by law and run their departments on budgets drawn from the "
             "Treasury. A ministry's brief follows its name: Defence, Finance, Transport &amp; "
             "Logistics, and Justice &amp; Home Affairs, with such further offices — the "
             "Security Minister, the Head of Coding, the Head of Hacking — as the Republic "
             "keeps."),
            ("How ministers are chosen",
             "A vacant ministry is filled by election: any eligible citizen may stand, and when "
             "four have done so an election opens of its own accord and every citizen votes. The "
             "President may also appoint a citizen to a ministry, and may dismiss a minister; an "
             "appointment is entered on the citizen's record."),
            ("Duties held outright",
             "A minister's duties are theirs to carry out without asking: the Defence Minister "
             "works the Armoury desk; the Transport Minister vets and approves the couriers of "
             "Cyvazon; the Justice Minister rules on Cyvashield claims."),
            ("Policy by proposal",
             "A minister who would move a national lever — a tax rate, the courier wage, the "
             "Armoury rate, a levy — proposes it. Nothing changes until the President assents, "
             "and the proposal and its answer are both recorded."),
        ],
    },
    {
        "num": "VI", "title": "The Legislature",
        "sections": [
            ("Who makes law",
             "Law is made by the citizens. Any citizen may table a bill; every citizen may "
             "debate it and vote on it; the sponsor may withdraw it."),
            ("Passage",
             "A bill that receives more Ayes than Nays passes and goes to the President for "
             "assent. On assent it is enacted, numbered as an Act of the year, and published in "
             "the Gazette. A bill refused assent does not become law, and is marked refused on "
             "the Legislature's roll."),
            ("The Gazette",
             "The Gazette is the Republic's record of law: every Act and every decree, numbered "
             "in order and open to all. What is not in the Gazette is not the law."),
        ],
    },
    {
        "num": "VII", "title": "The Courts and Justice",
        "sections": [
            ("The National Court",
             "The National Court hears every case a citizen brings. The elected Judge presides. "
             "The President may also sit, and sits alone when no Judge holds office. No one may "
             "rule on a case to which they are a party."),
            ("A fair hearing",
             "Both sides of a case may argue it before judgment. A citizen may report a crime "
             "with evidence, and the report is heard in the same way."),
            ("Sentences",
             "A sentence of the Court is a fine, a term in jail, or both. No term of jail — "
             "whether given by the Court or by presidential order under Article IV — may exceed "
             "three hundred and sixty-five (365) days. A jailed citizen is confined to the jail "
             "until the term is served, and is released when it is. Convictions, including a "
             "detention by order, are entered on the criminal record and bar the citizen from "
             "standing for office."),
            ("Mercy",
             "The President may pardon any conviction. A pardon is recorded like any other act "
             "of the office."),
        ],
    },
    {
        "num": "VIII", "title": "The Treasury and the Economy",
        "sections": [
            ("One Treasury, in the open",
             "There is a single National Treasury into which all taxes, levies, fees and "
             "forfeitures flow and from which every salary and public service is paid. Its "
             "holdings are published, and every citizen may read them at any time."),
            ("Currency",
             "The lawful currencies are the Cybuck (CB), the Pufferbuck (PUFB), the Aquiline "
             "(AQ) and the Cybit (CBT), pegged at one Cybuck to one Pufferbuck, ten Aquilines "
             "and fifty Cybits. Cybits are the change of a Cybuck."),
            ("Taxation",
             "A Value-Added Tax is levied monthly on every citizen's holdings, at a rate set by "
             "law and published — ten percent (10%) at the adoption of this Constitution — "
             "together with the delivery levy that keeps Cyvazon free. Cyvashield cover is free; "
             "the Republic may set an insurance levy only if payouts outrun revenue, and must "
             "publish it. No tax may be laid on a citizen for what they believe."),
            ("Salaries",
             "Salaries are paid weekly from the Treasury by office: the Vice President and the "
             "Chancellor 1,000 CB; a Minister, the Security Minister and the Judge 900; the Head of Coding, the "
             "Head of Hacking and a company Founder 800; an Employee 500; a Citizen 100. "
             "Couriers of Cyvazon draw 500 CB besides. The President draws nothing."),
            ("Credit",
             "A citizen may borrow up to five thousand (5,000) Cybucks from the Treasury, to be "
             "repaid within thirty (30) days. Borrowed money may not be sent away. A loan not "
             "repaid by the end of its term is called in: the Treasury takes the citizen's "
             "Cybucks, Pufferbucks and Aquilines in settlement — the whole of them, not only the "
             "sum owed — and the default is entered on the record. This is the hardest rule in "
             "the Republic and it is written here so that no one borrows without knowing it. "
             "While a loan is still owed a citizen may not stand for office; once it is repaid "
             "or called in, they may."),
        ],
    },
    {
        "num": "IX", "title": "The Public Services",
        "sections": [
            ("Cyvazon",
             "Delivery anywhere in the territory is free to whoever asks for it, paid for by "
             "the delivery levy. Couriers carry other citizens' property and are therefore "
             "approved before they carry anything; only the person receiving a parcel may "
             "confirm that it arrived."),
            ("Cyvashield",
             "Every citizen may take free cover against theft, lost parcels, bad trades and "
             "scams. Claims are checked against the record and ruled on openly."),
            ("Cyvalend",
             "Citizens lend one another the things they forgot, free of charge — though an "
             "owner may ask a returnable deposit, given back when the item comes home — and "
             "give them back on time."),
            ("The Armoury",
             "The Republic keeps a defensive Armoury. A citizen who hands in a G2 pen is paid "
             "the war-effort rate by the Defence desk, and every round is logged."),
            ("Cyvapay and the markets",
             "Any of the Republic's currencies may be taken as payment on any website through "
             "Cyvapay, and goods traded "
             "in the national and state marketplaces and the card packets. Every sale raises a "
             "delivery so that nobody may take the money and keep the goods."),
        ],
    },
    {
        "num": "X", "title": "Defence and Foreign Relations",
        "sections": [
            ("Doctrine",
             "The doctrine of the Republic is <i>defence, not invasion</i>. Cyvathon's forces "
             "exist to defend it. No office of the Republic may order an attack on another "
             "nation, its citizens or its servers, and none has."),
            ("Athena and the Registry",
             "The intelligence service and the classified Registry serve the Republic's "
             "defence and its knowledge of the world, within the doctrine above and within the "
             "law. Both answer to the President, and nothing held in the Registry may be used "
             "against a citizen except in the open, before the Court."),
            ("Treaties",
             "The Republic keeps its word. It signed a Treaty of Peace, Friendship and Free "
             "Trade with the Republic of Crystonia on 14 June 2026, and the Treaty of "
             "Anti-Anarchism on 2 September 2026. Alliances are requested by nations and "
             "confirmed by the President; the Republic may also name a rival, and a rival is "
             "watched, never attacked. Every treaty is entered in the National Timeline."),
        ],
    },
    {
        "num": "XI", "title": "Culture",
        "sections": [
            ("Chairism",
             "Chairism is the valued culture of the Republic. We honour the prophets of the "
             "Chair, and those who wish to say so may say it: <i>all hail the Chair.</i> It is a "
             "cultural practice, freely encouraged and freely declined, as Article III provides."),
            ("The record",
             "The National Timeline is the Republic's memory. Its founding entries are fixed; "
             "everything since is written by the citizens who live it."),
        ],
    },
    {
        "num": "XII", "title": "Amendment and Supremacy",
        "sections": [
            ("Amendment",
             "This Constitution is amended by an Act of the Legislature — a bill tabled by a "
             "citizen, passed with more Ayes than Nays, and given assent — and by nothing else. "
             "No decree may amend it."),
            ("Supremacy",
             "This Constitution is the highest law of the Republic. A decree, an Act or a "
             "ruling that conflicts with it is void to the extent of the conflict."),
            ("Practice and principle",
             "The Republic's law is exercised through its national website. Where a feature of "
             "the website and a clause of this Constitution do not yet match, the clause states "
             "the principle the Republic holds itself to, and the feature is to be brought into "
             "line with it."),
        ],
    },
]

CLOSING = (
    "Adopted by the Republic of Cyvathon on " + ADOPTED + ", in place of the Constitution "
    "and Lawbook of 2025, and in force from that day."
)

DISCLAIMER = (
    "Cyvathon is a student-run micronation: a game of government played among classmates. "
    "This document has no legal force of any kind and creates no obligation on any person, "
    "school or institution."
)

# ---------------------------------------------------------------------------
#  STYLE — the house style of the Republic's documents (Times, A4, a hairline)
# ---------------------------------------------------------------------------

INK  = colors.HexColor("#0d1117")
GOLD = colors.HexColor("#9a6b00")      # the founding colour on the National Timeline
GREY = colors.HexColor("#4a5568")
LINE = colors.HexColor("#cbd5e0")

styles = getSampleStyleSheet()
title_style = ParagraphStyle("T", parent=styles["Title"], fontName="Times-Bold",
    fontSize=30, leading=36, textColor=INK, alignment=TA_CENTER, spaceAfter=6)
sub_style = ParagraphStyle("S", parent=styles["Normal"], fontName="Times-Italic",
    fontSize=14, textColor=GREY, alignment=TA_CENTER, spaceAfter=4)
article_style = ParagraphStyle("A", parent=styles["Heading1"], fontName="Times-Bold",
    fontSize=15, textColor=GOLD, spaceBefore=18, spaceAfter=4)
section_style = ParagraphStyle("H", parent=styles["Heading2"], fontName="Times-Bold",
    fontSize=11.5, textColor=INK, spaceBefore=10, spaceAfter=2)
body_style = ParagraphStyle("B", parent=styles["Normal"], fontName="Times-Roman",
    fontSize=11, leading=16, textColor=colors.HexColor("#1a202c"),
    alignment=TA_JUSTIFY, spaceAfter=6)
preamble_style = ParagraphStyle("P", parent=body_style, fontName="Times-Italic",
    fontSize=11.5, leading=17)
note_style = ParagraphStyle("N", parent=body_style, fontSize=10, leading=14.5,
    textColor=GREY, alignment=TA_CENTER, fontName="Times-Italic")


def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Times-Italic", 8)
    canvas.setFillColor(GREY)
    canvas.drawString(2 * cm, 1.2 * cm, "The Constitution of the Republic of Cyvathon")
    canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, "Page %d" % doc.page)
    canvas.setStrokeColor(LINE)
    canvas.line(2 * cm, 1.5 * cm, A4[0] - 2 * cm, 1.5 * cm)
    canvas.restoreState()


def build_pdf():
    doc = SimpleDocTemplate(
        PDF_OUT, pagesize=A4,
        leftMargin=2.4 * cm, rightMargin=2.4 * cm, topMargin=2.2 * cm, bottomMargin=2.2 * cm,
        title="The Constitution of the Republic of Cyvathon",
        author="The citizens of the Republic of Cyvathon")
    e = []

    # ---- Title page ----
    e.append(Spacer(1, 4.2 * cm))
    e.append(Paragraph("THE CONSTITUTION", title_style))
    e.append(Spacer(1, 0.3 * cm))
    e.append(Paragraph("of the", sub_style))
    e.append(Paragraph("R E P U B L I C&nbsp;&nbsp;O F&nbsp;&nbsp;C Y V A T H O N", sub_style))
    e.append(Spacer(1, 0.6 * cm))
    e.append(HRFlowable(width="40%", thickness=1.2, color=GOLD, hAlign="CENTER"))
    e.append(Spacer(1, 0.6 * cm))
    e.append(Paragraph("&#9670;", ParagraphStyle("seal", parent=sub_style, fontSize=26, textColor=GOLD)))
    e.append(Paragraph("Code. Conquer. Cause Creativity.", sub_style))
    e.append(Spacer(1, 3.2 * cm))
    e.append(Paragraph("Founded 26 May 2025 &middot; Adopted " + ADOPTED,
             ParagraphStyle("foot", parent=sub_style, fontSize=10)))
    e.append(Paragraph("A nation is its people, not its ruler.",
             ParagraphStyle("foot2", parent=sub_style, fontSize=10)))
    e.append(PageBreak())

    # ---- Preamble ----
    e.append(Paragraph("PREAMBLE", article_style))
    e.append(HRFlowable(width="100%", thickness=0.6, color=LINE))
    e.append(Spacer(1, 0.2 * cm))
    e.append(Paragraph(PREAMBLE, preamble_style))

    # ---- Articles ----
    for a in ARTICLES:
        block = [Paragraph("ARTICLE %s &mdash; %s" % (a["num"], a["title"].upper()), article_style),
                 HRFlowable(width="100%", thickness=0.6, color=LINE)]
        for i, (head, text) in enumerate(a["sections"], 1):
            block.append(Paragraph("&sect;%d. %s" % (i, head), section_style))
            block.append(Paragraph(text, body_style))
        # keep the heading with at least its first section
        e.append(KeepTogether(block[:4]))
        e.extend(block[4:])

    # ---- Adoption ----
    e.append(Spacer(1, 0.8 * cm))
    e.append(HRFlowable(width="100%", thickness=1, color=GOLD))
    e.append(Spacer(1, 0.4 * cm))
    e.append(Paragraph(CLOSING, ParagraphStyle("ratify", parent=body_style,
             fontName="Times-Italic", alignment=TA_CENTER)))
    e.append(Spacer(1, 1 * cm))
    sig = Table([
        ["______________________________", "", "______________________________"],
        ["Prathyay", "", "The Citizens of Cyvathon"],
        ["President of the Republic", "", "in whose name this is adopted"],
        ["", "", ""],
        ["Date: " + ADOPTED, "", "Date: " + ADOPTED],
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
    print("Wrote " + PDF_OUT)


def build_json():
    data = {
        "title": "The Constitution of the Republic of Cyvathon",
        "adopted": ADOPTED,
        "founded": "26 May 2025",
        "pdf": "/" + PDF_OUT,
        "preamble": PREAMBLE,
        "articles": [{"num": a["num"], "title": a["title"],
                      "sections": [{"head": h, "text": t} for h, t in a["sections"]]}
                     for a in ARTICLES],
        "closing": CLOSING,
        "disclaimer": DISCLAIMER,
    }
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("Wrote " + JSON_OUT)


if __name__ == "__main__":
    build_pdf()
    build_json()

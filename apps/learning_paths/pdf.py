from datetime import date, datetime
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

NAVY = colors.HexColor("#29347d")
BLUE = colors.HexColor("#4654a3")
ORANGE = colors.HexColor("#f39200")
INK = colors.HexColor("#26324b")
MUTED = colors.HexColor("#6d7890")
PALE_BLUE = colors.HexColor("#f1f4fb")
PALE_ORANGE = colors.HexColor("#fff5e6")
RULE = colors.HexColor("#e2e7f0")


def _paragraph(value: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(str(value or "")).replace("\n", "<br/>"), style)


def _formatted_paragraph(value: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(value, style)


def _format_enrolled_date(value: date | datetime | None) -> str:
    if not isinstance(value, (date, datetime)):
        return "Date inconnue"
    return value.strftime("%d/%m/%Y")


def build_learning_path_pdf(
    learning_path: dict,
    learners: list[dict] | None = None,
) -> bytes:
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=29 * mm,
        bottomMargin=20 * mm,
        title=learning_path["title"],
        author="SkillForge",
        subject="Parcours d'apprentissage",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "LearningPathTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=23,
        leading=28,
        alignment=TA_LEFT,
        textColor=NAVY,
        spaceAfter=4 * mm,
    )
    section_style = ParagraphStyle(
        "LearningPathSection",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=17,
        textColor=NAVY,
        spaceBefore=6 * mm,
        spaceAfter=3 * mm,
        keepWithNext=True,
    )
    body_style = ParagraphStyle(
        "LearningPathBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=15,
        textColor=INK,
        spaceAfter=2 * mm,
    )
    meta_style = ParagraphStyle(
        "LearningPathMeta",
        parent=body_style,
        fontSize=8,
        leading=11,
        textColor=MUTED,
    )
    step_title_style = ParagraphStyle(
        "LearningPathStepTitle",
        parent=body_style,
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=14,
        textColor=NAVY,
    )
    step_number_style = ParagraphStyle(
        "LearningPathStepNumber",
        parent=step_title_style,
        alignment=TA_CENTER,
        textColor=colors.white,
        fontSize=11,
    )
    learner_name_style = ParagraphStyle(
        "LearningPathLearnerName",
        parent=body_style,
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=12,
    )
    small_style = ParagraphStyle(
        "LearningPathSmall",
        parent=meta_style,
        fontSize=7.5,
    )

    story = []
    theme_label = learning_path.get("theme_label", "")
    level_label = learning_path.get("level_label", "")
    metadata = "   •   ".join(
        value for value in (theme_label, level_label) if value
    )
    if metadata:
        story.append(_paragraph(metadata.upper(), meta_style))
        story.append(Spacer(1, 2 * mm))

    story.append(_paragraph(learning_path["title"], title_style))
    story.append(_paragraph(
        learning_path.get("description", "") or "Aucune description fournie.",
        body_style,
    ))
    story.append(Spacer(1, 3 * mm))

    courses = learning_path.get("courses", [])
    course_count_label = (
        f"{len(courses)} cours" if len(courses) != 1 else "1 cours"
    )
    summary_cells = [
        _formatted_paragraph(
            f"<b>{escape(course_count_label)}</b><br/>Étapes ordonnées",
            body_style,
        ),
    ]
    if learners is not None:
        summary_cells.append(_formatted_paragraph(
            f"<b>{len(learners)} apprenant(s)</b><br/>Inscrits au parcours",
            body_style,
        ))
    summary = Table(
        [summary_cells],
        colWidths=[(A4[0] - 40 * mm) / len(summary_cells)] * len(summary_cells),
    )
    summary_styles = [
        ("BACKGROUND", (0, 0), (0, -1), PALE_BLUE),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]
    if learners is not None:
        summary_styles.append(("BACKGROUND", (1, 0), (1, -1), PALE_ORANGE))
    summary.setStyle(TableStyle(summary_styles))
    story.extend([summary, Spacer(1, 5 * mm)])
    story.append(_paragraph("Étapes du parcours", section_style))

    for position, course in enumerate(courses, start=1):
        details = [_paragraph(course.get("title", "Cours sans titre"), step_title_style)]
        if course.get("description"):
            details.append(_paragraph(course["description"], body_style))
        if course.get("detail"):
            details.append(_paragraph(course["detail"], meta_style))
        step = Table(
            [[_paragraph(str(position), step_number_style), details]],
            colWidths=[14 * mm, A4[0] - 40 * mm - 14 * mm],
        )
        step.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), BLUE),
            ("BACKGROUND", (1, 0), (1, 0), PALE_BLUE),
            ("BOX", (0, 0), (-1, -1), 0.5, RULE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (0, 0), 5),
            ("RIGHTPADDING", (0, 0), (0, 0), 5),
            ("LEFTPADDING", (1, 0), (1, 0), 10),
            ("RIGHTPADDING", (1, 0), (1, 0), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.extend([KeepTogether(step), Spacer(1, 3 * mm)])

    if learners is not None:
        story.append(_paragraph("Apprenants inscrits", section_style))
        if not learners:
            story.append(_paragraph(
                "Aucun apprenant n’est inscrit à ce parcours.",
                body_style,
            ))
        else:
            learner_rows = [[
                _formatted_paragraph("<b>Apprenant</b>", small_style),
                _formatted_paragraph("<b>Inscription</b>", small_style),
                _formatted_paragraph("<b>Progression</b>", small_style),
            ]]
            for learner in learners:
                completed = learner.get("completed_count", 0)
                total = learner.get("total_courses", 0)
                percentage = learner.get("progress_percentage", 0)
                learner_rows.append([
                    _paragraph(learner.get("name", "Compte indisponible"), learner_name_style),
                    _paragraph(_format_enrolled_date(learner.get("enrolled_at")), small_style),
                    _paragraph(
                        f"{completed}/{total} cours ({percentage} %)",
                        small_style,
                    ),
                ])
            learner_table = Table(
                learner_rows,
                colWidths=[
                    (A4[0] - 40 * mm) * 0.48,
                    (A4[0] - 40 * mm) * 0.22,
                    (A4[0] - 40 * mm) * 0.30,
                ],
                repeatRows=1,
                hAlign=TA_LEFT,
            )
            learner_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), PALE_BLUE),
                ("TEXTCOLOR", (0, 0), (-1, 0), NAVY),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfe")]),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, BLUE),
                ("LINEBELOW", (0, 1), (-1, -1), 0.35, RULE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]))
            story.append(learner_table)

    def draw_page(canvas, doc):
        canvas.saveState()
        page_width, page_height = A4
        canvas.setFillColor(NAVY)
        canvas.rect(0, page_height - 8 * mm, page_width, 8 * mm, fill=1, stroke=0)
        canvas.setFillColor(ORANGE)
        canvas.rect(0, page_height - 8 * mm, 34 * mm, 1.5 * mm, fill=1, stroke=0)
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(20 * mm, 14 * mm, page_width - 20 * mm, 14 * mm)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(NAVY)
        canvas.drawString(20 * mm, 9 * mm, "SKILLFORGE  |  PARCOURS D’APPRENTISSAGE")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(page_width - 20 * mm, 9 * mm, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return buffer.getvalue()

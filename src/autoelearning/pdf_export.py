from __future__ import annotations

import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def render_markdown_pdf(
    markdown_path: Path,
    pdf_path: Path,
    *,
    title: str,
    course: str,
    submission_copy: bool = False,
) -> Path:
    normalized = markdown_path.read_text(encoding="utf-8")
    normalized = (
        normalized.replace("—", " - ")
        .replace("–", "-")
        .replace("‑", "-")
        .replace(r"\qquad", r"\;")
        .replace("qquad", r"\;")
        .replace("Polyak's stepsize", "Polyak stepsize")
        .replace("Polyak' s", "Polyak's")
        .replace("Polyak’ s", "Polyak's")
    )
    normalized = re.sub(r"(?m)^[ \t]*\\\[[ \t]*$", "$$", normalized)
    normalized = re.sub(r"(?m)^[ \t]*\\\][ \t]*$", "$$", normalized)
    normalized = normalized.replace("\\(", "$").replace("\\)", "$")
    if submission_copy:
        normalized = re.split(
            r"(?im)^##\s+(Assumptions|Self-check)", normalized, maxsplit=1
        )[0]
        normalized = re.sub(
            r"(?im)^This is a draft for student review, not a submission\.\s*$",
            "",
            normalized,
        )
        normalized = re.sub(r"(?m)^#\s+.+\n+", "", normalized, count=1)
    if _render_with_pandoc(
        normalized, pdf_path, title=title, course=course,
        submission_copy=submission_copy,
    ):
        return pdf_path

    font = _register_font()
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "ChineseBody", parent=styles["BodyText"], fontName=font, fontSize=10.5,
        leading=17, textColor=colors.HexColor("#25324A"), spaceAfter=7,
    )
    heading1 = ParagraphStyle(
        "ChineseH1", parent=body, fontSize=17, leading=22, textColor=colors.HexColor("#17243D"),
        spaceBefore=14, spaceAfter=8,
    )
    heading2 = ParagraphStyle(
        "ChineseH2", parent=body, fontSize=13, leading=18, textColor=colors.HexColor("#315E93"),
        spaceBefore=11, spaceAfter=6,
    )
    bullet = ParagraphStyle("ChineseBullet", parent=body, leftIndent=14, firstLineIndent=-8)
    code = ParagraphStyle(
        "Code", parent=body, fontName="Courier", fontSize=8.4, leading=12,
        leftIndent=10, rightIndent=10, backColor=colors.HexColor("#F3F5F8"),
        borderPadding=8, spaceBefore=5, spaceAfter=8,
    )
    title_style = ParagraphStyle(
        "DocumentTitle", parent=heading1, fontSize=23, leading=29, alignment=TA_CENTER,
        spaceAfter=8,
    )
    meta_style = ParagraphStyle(
        "Meta", parent=body, fontSize=9, leading=13, textColor=colors.HexColor("#68758A"),
        alignment=TA_CENTER,
    )

    text = normalized
    story = [
        Paragraph(_inline(title), title_style),
        Paragraph(_inline(course), meta_style),
        Spacer(1, 9 * mm),
    ]
    story.extend(_markdown_flowables(text, body, heading1, heading2, bullet, code))
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(pdf_path), pagesize=A4, rightMargin=20 * mm, leftMargin=20 * mm,
        topMargin=20 * mm, bottomMargin=19 * mm, title=title, author="eLearning Agent Draft",
    )

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#D9DEE7"))
        canvas.line(20 * mm, 14 * mm, A4[0] - 20 * mm, 14 * mm)
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor("#7D889B"))
        canvas.drawString(20 * mm, 9 * mm, "Agent draft - review required before use")
        canvas.drawRightString(A4[0] - 20 * mm, 9 * mm, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return pdf_path


def _render_with_pandoc(
    text: str, pdf_path: Path, *, title: str, course: str,
    submission_copy: bool,
) -> bool:
    try:
        import pypandoc

        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        text = re.sub(
            r"`([^`]+)`",
            lambda match: rf"\path{{{Path(match.group(1).replace('\\', '/')).name}}}",
            text,
        )
        text = re.sub(r"(?m)^#\s+.*Reviewable Draft\s*$\n?", "", text, count=1)
        yaml_title = title.replace('"', "'")
        yaml_course = course.replace('"', "'")
        date_line = "" if submission_copy else 'date: "Agent draft - review required before use"\n'
        text = f'---\ntitle: "{yaml_title}"\nsubtitle: "{yaml_course}"\n{date_line}---\n\n' + text
        pypandoc.convert_text(
            text,
            to="pdf",
            format="markdown+raw_tex+tex_math_dollars",
            outputfile=str(pdf_path),
            extra_args=[
                "--pdf-engine=xelatex",
                "-V", "mainfont=Times New Roman",
                "-V", "CJKmainfont=Microsoft YaHei",
                "-V", "papersize=a4",
                "-V", "geometry:margin=15mm",
                "-V", "fontsize=10pt",
                "-V", "colorlinks=true",
                "-V", "linkcolor=blue",
            ],
        )
        return pdf_path.exists() and pdf_path.stat().st_size > 0
    except Exception:
        return False


def _markdown_flowables(text, body, heading1, heading2, bullet, code):
    result = []
    paragraph_lines: list[str] = []
    code_lines: list[str] = []
    in_code = False

    def flush_paragraph():
        if paragraph_lines:
            result.append(Paragraph(_inline(" ".join(paragraph_lines)), body))
            paragraph_lines.clear()

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            flush_paragraph()
            if in_code:
                result.append(Preformatted("\n".join(code_lines), code))
                code_lines.clear()
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line.strip():
            flush_paragraph()
            continue
        if line.startswith("# "):
            flush_paragraph(); result.append(Paragraph(_inline(line[2:]), heading1)); continue
        if line.startswith("## "):
            flush_paragraph(); result.append(Paragraph(_inline(line[3:]), heading2)); continue
        if line.startswith("### "):
            flush_paragraph(); result.append(Paragraph(_inline(line[4:]), heading2)); continue
        if re.match(r"^\s*[-*]\s+", line):
            flush_paragraph()
            content = re.sub(r"^\s*[-*]\s+", "", line)
            result.append(Paragraph(f"•&nbsp; {_inline(content)}", bullet))
            continue
        numbered = re.match(r"^\s*(\d+)\.\s+(.*)", line)
        if numbered:
            flush_paragraph()
            result.append(Paragraph(f"{numbered.group(1)}.&nbsp; {_inline(numbered.group(2))}", bullet))
            continue
        if line.startswith("|") and line.endswith("|"):
            flush_paragraph()
            cells = [Paragraph(_inline(cell.strip()), body) for cell in line.strip("|").split("|")]
            table = Table([cells], colWidths=None, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F7FA")),
                ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9DEE7")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E5E9EF")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]))
            result.append(KeepTogether([table, Spacer(1, 4)]))
            continue
        paragraph_lines.append(line.strip())
    flush_paragraph()
    if code_lines:
        result.append(Preformatted("\n".join(code_lines), code))
    return result


def _inline(value: str) -> str:
    escaped = html.escape(value)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"`(.+?)`", r"<font name='Courier'>\1</font>", escaped)
    escaped = escaped.replace("$", "")
    return escaped


def _register_font() -> str:
    name = "AutoELearningCJK"
    if name in pdfmetrics.getRegisteredFontNames():
        return name
    candidates = [
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
    ]
    for path in candidates:
        if path.exists():
            try:
                pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=0))
                return name
            except Exception:
                continue
    return "Helvetica"

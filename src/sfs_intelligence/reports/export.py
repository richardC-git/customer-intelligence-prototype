"""Save a report as Markdown or PDF.

Reports can hold CRM data, so they go to the git-ignored private directory by
default. Source titles are web text an attacker controls, and Markdown viewers
load remote images and follow links, so that text is escaped before writing.
"""

import re
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

from sfs_intelligence.llm.guard import clean_text
from sfs_intelligence.reports.document import ReportDocument

DEFAULT_REPORTS_DIR = Path("data/private/reports")
INTERNAL_NOTICE = "SFS internal: contains CRM or field sales data. Do not share outside SFS."
AI_NOTICE = "AI-generated. Check the cited sources before acting on it."

# Without [ and ], text cannot form a link or image, so ( ) and ! can stay readable.
_MARKDOWN_SPECIALS = re.compile(r"([\\`*_{}\[\]<>#|~])")
_NOT_SLUG = re.compile(r"[^\w-]+")

# Helvetica in PDF covers Latin-1 only, so common typography is replaced.
_PDF_REPLACEMENTS = str.maketrans(
    {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...", "•": "-",
     "™": "(TM)", "®": "(R)", "≥": ">=", "≤": "<=", " ": " "}
)


def to_markdown(document: ReportDocument) -> str:
    lines = [f"# {document.title}: {_escape(document.subject)}", ""]
    if document.is_internal:
        lines += [f"> **{INTERNAL_NOTICE}**", ""]
    lines += [
        f"**{document.headline}**",
        "",
        _escape(document.reason),
        "",
        f"Generated {document.generated}. {AI_NOTICE}",
        "",
    ]
    lines += _markdown_list("Top actions", document.top_actions)
    lines += _markdown_list("Key warnings", document.warnings)
    lines += ["## Summary", "", _escape(document.summary), ""]
    for section in document.sections:
        lines += _markdown_list(section.heading, section.items)
    lines += ["## Sources", ""]
    lines += [f"- {_source_line(source, markdown=True)}" for source in document.sources]
    lines.append("")
    lines += _markdown_list("Limitations", document.limitations)
    lines += [f"Produced by: {_escape(document.provider)}", ""]
    return "\n".join(lines)


def write_pdf(document: ReportDocument, path: Path) -> None:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def paragraph(text: str, size: int = 10, style: str = "", gap: float = 2) -> None:
        pdf.set_font("Helvetica", style, size)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, size * 0.55, _pdf_text(text))
        pdf.ln(gap)

    def bullets(heading: str, items: tuple[str, ...], size: int = 10) -> None:
        if not items:
            return
        paragraph(heading, 12, "B", 1)
        for item in items:
            paragraph(f"- {item}", size, gap=1)
        pdf.ln(2)

    paragraph(f"{document.title}: {document.subject}", 16, "B", 3)
    if document.is_internal:
        paragraph(INTERNAL_NOTICE, 9, "B", 3)
    paragraph(document.headline, 13, "B", 1)
    paragraph(document.reason)
    paragraph(f"Generated {document.generated}. {AI_NOTICE}", 8, gap=4)
    bullets("Top actions", document.top_actions)
    bullets("Key warnings", document.warnings)
    paragraph("Summary", 12, "B", 1)
    paragraph(document.summary, gap=4)
    for section in document.sections:
        bullets(section.heading, section.items)
    bullets(
        "Sources",
        tuple(_source_line(source, markdown=False) for source in document.sources),
        size=8,
    )
    bullets("Limitations", document.limitations, size=9)
    paragraph(f"Produced by: {document.provider}", 8)
    pdf.output(str(path))


def export_report(
    document: ReportDocument, file_format: str, directory: Path | str = DEFAULT_REPORTS_DIR
) -> Path:
    """Write the report and return its path. ``file_format`` is ``md`` or ``pdf``."""
    if file_format not in ("md", "pdf"):
        raise ValueError(f"Unsupported report format: {file_format}")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"{_slug(document.subject)}-{stamp}.{file_format}"
    if file_format == "md":
        path.write_text(to_markdown(document), encoding="utf-8")
    else:
        write_pdf(document, path)
    return path


def _markdown_list(heading: str, items: tuple[str, ...]) -> list[str]:
    if not items:
        return []
    return [f"## {heading}", "", *(f"- {_escape(item)}" for item in items), ""]


def _source_line(source, markdown: bool) -> str:
    escape = _escape if markdown else clean_text
    parts = [f"[{source.id}]" if not markdown else f"\\[{source.id}\\]", escape(source.title)]
    if source.published:
        parts.append(f"({source.published})")
    if source.url:
        parts.append(f"<{source.url}>" if markdown else str(source.url))
    elif source.page_text:
        parts.append(escape(" | ".join(clean_text(source.page_text).splitlines())))
    return " ".join(parts)


def _escape(text: str) -> str:
    return _MARKDOWN_SPECIALS.sub(r"\\\1", " ".join(clean_text(text).split()))


def _pdf_text(text: str) -> str:
    cleaned = clean_text(text).translate(_PDF_REPLACEMENTS)
    return cleaned.encode("latin-1", "replace").decode("latin-1")


def _slug(subject: str) -> str:
    return _NOT_SLUG.sub("-", subject).strip("-")[:60] or "report"

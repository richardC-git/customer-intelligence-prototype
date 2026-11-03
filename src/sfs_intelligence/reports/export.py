"""Save a report as Markdown, HTML, or PDF.

Reports can hold CRM data, so they go to the git-ignored private directory by
default. Source titles are web text an attacker controls, and Markdown viewers
load remote images and follow links, so that text is escaped before writing.
"""

import re
from datetime import datetime
from pathlib import Path

from sfs_intelligence.llm.guard import clean_text
from sfs_intelligence.reports.document import AI_NOTICE, INTERNAL_NOTICE, ReportDocument
from sfs_intelligence.reports.html import to_html
from sfs_intelligence.reports.pdf import write_pdf

DEFAULT_REPORTS_DIR = Path("data/private/reports")

# Without [ and ], text cannot form a link or image, so ( ) and ! can stay readable.
_MARKDOWN_SPECIALS = re.compile(r"([\\`*_{}\[\]<>#|~])")
_NOT_SLUG = re.compile(r"[^\w-]+")

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
    lines += [f"- {_source_line(source)}" for source in document.sources]
    lines.append("")
    lines += _markdown_list("Limitations", document.limitations)
    lines += [f"Produced by: {_escape(document.provider)}", ""]
    return "\n".join(lines)


def export_report(
    document: ReportDocument, file_format: str, directory: Path | str = DEFAULT_REPORTS_DIR
) -> tuple[Path, ...]:
    """Write the report and return the saved paths, main file first.

    ``md`` saves the full report as Markdown and as an HTML page that opens in
    any browser. ``pdf`` saves the one-page brief, which may leave items out,
    with both full versions next to it.
    """
    if file_format not in ("md", "pdf"):
        raise ValueError(f"Unsupported report format: {file_format}")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = directory / f"{_slug(document.subject)}-{stamp}"
    html = stem.with_suffix(".html")
    html.write_text(to_html(document), encoding="utf-8")
    markdown = stem.with_suffix(".md")
    markdown.write_text(to_markdown(document), encoding="utf-8")
    if file_format == "md":
        return (html, markdown)
    pdf = stem.with_suffix(".pdf")
    write_pdf(document, pdf)
    return (pdf, html, markdown)


def _markdown_list(heading: str, items: tuple[str, ...]) -> list[str]:
    if not items:
        return []
    return [f"## {heading}", "", *(f"- {_escape(item)}" for item in items), ""]


def _source_line(source) -> str:
    parts = [f"\\[{source.id}\\]", _escape(source.title)]
    if source.published:
        parts.append(f"({source.published})")
    if source.url:
        parts.append(f"<{source.url}>")
    elif source.page_text:
        parts.append(_escape(" | ".join(clean_text(source.page_text).splitlines())))
    return " ".join(parts)


def _escape(text: str) -> str:
    return _MARKDOWN_SPECIALS.sub(r"\\\1", " ".join(clean_text(text).split()))


def _slug(subject: str) -> str:
    return _NOT_SLUG.sub("-", subject).strip("-")[:60] or "report"

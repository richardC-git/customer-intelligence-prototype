"""One-page PDF brief on US Letter paper.

The page has a header with the verdict, two columns (the sales story on the
left, warnings and quick facts on the right), and a footer listing only the
sources the page cites. If the content does not fit, the text shrinks a little,
then the lowest-ranked items are cut and each cut is noted on the page, so the
brief never runs to a second page. The Markdown report saved with it keeps
everything.

Noto Sans is bundled so names print correctly in any Latin, Greek, or Cyrillic
script, with Noto Sans JP as the fallback for Japanese and Chinese characters.
"""

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from fpdf import FPDF

from sfs_intelligence.llm.guard import clean_text
from sfs_intelligence.models.prospect import Source, SourceKind
from sfs_intelligence.reports.document import AI_NOTICE, INTERNAL_NOTICE, ReportDocument

FONT_DIR = Path(__file__).with_name("fonts")
FONT = "NotoSans"
CJK_FONT = "NotoSansJP"

MARGIN = 12.0
GUTTER = 7.0
MAIN_SHARE = 0.6
# Body sizes tried in order before any item is cut.
FONT_SIZES = (9.0, 8.5, 8.0)
SMALL = 7.0
LINE_SPACING = 1.3
BULLET_INDENT = 3.0
MAX_ITEM_CHARS = 320
MAX_SUMMARY_CHARS = 900
MAX_LIMITATIONS = 3
SOURCE_ID_WIDTH = 7.0

INK = (27, 37, 48)
MUTED = (90, 104, 118)
RULE = (205, 213, 221)
ACCENT = (47, 102, 144)
ALERT = (170, 40, 40)
_VERDICT_COLORS = {
    "Pursue": (46, 125, 79),
    "Research further": (176, 112, 0),
}
_NEUTRAL_VERDICT = (100, 110, 120)

_PT_TO_MM = 25.4 / 72
_TRAILING_CITATION = re.compile(r"\s*\[[A-Z]\d+(?:, [A-Z]\d+)*\]$")
_CITATION = re.compile(r"\[([A-Z]\d+(?:, [A-Z]\d+)*)\]")


@dataclass
class Block:
    """A heading and its items in one column."""

    heading: str
    items: list[str]
    bullets: bool = True
    # Summaries, warnings, and CRM facts are never cut.
    trimmable: bool = True
    hidden: int = 0


@dataclass
class PagePlan:
    size: float
    main: list[Block]
    side: list[Block]
    limitations: list[str]
    sources: list[Source] = field(default_factory=list)

    @property
    def hidden(self) -> int:
        return sum(block.hidden for block in (*self.main, *self.side))


def new_pdf() -> FPDF:
    pdf = FPDF(format="Letter", unit="mm")
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.set_auto_page_break(False)  # The plan guarantees a fit; never start page two.
    pdf.c_margin = 0  # Columns and bullets do their own spacing.
    pdf.add_font(FONT, "", FONT_DIR / "NotoSans-Regular.ttf")
    pdf.add_font(FONT, "B", FONT_DIR / "NotoSans-Bold.ttf")
    pdf.add_font(CJK_FONT, "", FONT_DIR / "NotoSansJP-Regular.otf")
    pdf.set_fallback_fonts([CJK_FONT], exact_match=False)
    pdf.add_page()
    return pdf


def write_pdf(document: ReportDocument, path: Path) -> None:
    pdf = new_pdf()
    pdf.set_title(f"{document.title}: {_clean(document.subject)}")
    _draw(pdf, document, plan_page(pdf, document))
    pdf.output(str(path))


def plan_page(pdf: FPDF, document: ReportDocument) -> PagePlan:
    """Choose the text size and items that fit on one page."""
    for size in FONT_SIZES:
        plan = _initial_plan(document, size)
        if _overflowing(pdf, document, plan) is None:
            return plan
    while (column := _overflowing(pdf, document, plan)) is not None and _trim(column):
        pass
    return plan


def _initial_plan(document: ReportDocument, size: float) -> PagePlan:
    main = [Block("Summary", [_shorten(document.summary, MAX_SUMMARY_CHARS)], False, False)]
    side = []
    if document.warnings:
        side.append(Block("Key warnings", _items(document.warnings), trimmable=False))
    if document.crm_record:
        side.append(Block("SFS CRM", _items(document.crm_record), bullets=False, trimmable=False))
    for section in document.sections:
        if section.items:
            (side if section.side else main).append(Block(section.heading, _items(section.items)))
    # The footer always carries the AI notice, so a limitation repeating it is skipped.
    limitations = [item for item in document.limitations if not item.startswith("AI-generated")]
    limitations = [_shorten(item, 200) for item in limitations[:MAX_LIMITATIONS]]
    return PagePlan(size, main, side, limitations)


def _items(items: tuple[str, ...]) -> list[str]:
    return [_shorten(item, MAX_ITEM_CHARS) for item in items]


def _overflowing(pdf: FPDF, document: ReportDocument, plan: PagePlan) -> list[Block] | None:
    """Return the column that does not fit, or None if the page fits."""
    plan.sources = _cited_sources(document, plan)
    top = _header(pdf, document, plan.size, draw=False)
    bottom = pdf.h - MARGIN - _footer(pdf, document, plan, 0, draw=False) - 3
    main_width, side_width = _column_widths(pdf)
    for column, width in ((plan.main, main_width), (plan.side, side_width)):
        height = sum(_block(pdf, block, 0, 0, width, plan.size, draw=False) for block in column)
        if top + height > bottom:
            return column
    return None


def _trim(column: list[Block]) -> bool:
    """Cut the last item of the longest section, keeping one item per section while possible."""
    candidates = [block for block in column if block.trimmable and len(block.items) > 1]
    candidates = candidates or [block for block in column if block.trimmable and block.items]
    if not candidates:
        return False
    # Ties go to the later, lower-ranked section.
    block = max(reversed(candidates), key=lambda block: len(block.items))
    block.items.pop()
    block.hidden += 1
    return True


def _cited_sources(document: ReportDocument, plan: PagePlan) -> list[Source]:
    texts = [document.reason, *(item for block in (*plan.main, *plan.side) for item in block.items)]
    cited = {
        source_id
        for text in texts
        for match in _CITATION.finditer(text)
        for source_id in match.group(1).split(", ")
    }
    return [source for source in document.sources if source.id in cited]


# Drawing. Each function returns the height it uses; with draw=False it only measures,
# so measuring and drawing cannot disagree.


def _draw(pdf: FPDF, document: ReportDocument, plan: PagePlan) -> None:
    top = _header(pdf, document, plan.size, draw=True)
    footer_height = _footer(pdf, document, plan, 0, draw=False)
    footer_top = pdf.h - MARGIN - footer_height
    _footer(pdf, document, plan, footer_top, draw=True)

    main_width, side_width = _column_widths(pdf)
    side_x = MARGIN + main_width + GUTTER
    for column, x, width in ((plan.main, MARGIN, main_width), (plan.side, side_x, side_width)):
        y = top
        for block in column:
            y += _block(pdf, block, x, y, width, plan.size, draw=True)
    pdf.set_draw_color(*RULE)
    pdf.set_line_width(0.2)
    rule_x = side_x - GUTTER / 2
    pdf.line(rule_x, top, rule_x, footer_top - 3)


def _header(pdf: FPDF, document: ReportDocument, size: float, draw: bool) -> float:
    """Draw the title and verdict; return the y position where the columns start."""
    width = pdf.w - 2 * MARGIN
    y = MARGIN
    if draw:
        pdf.set_font(FONT, "B", SMALL)
        pdf.set_text_color(*MUTED)
        pdf.set_xy(MARGIN, y)
        pdf.cell(width / 2, _line(SMALL), document.title.upper())
        pdf.set_xy(MARGIN + width / 2, y)
        pdf.cell(width / 2, _line(SMALL), f"Generated {document.generated}", align="R")
    y += _line(SMALL) + 0.5
    y += _text(pdf, MARGIN, y, width, _clean(document.subject), 17, "B", INK, draw)
    if document.is_internal:
        y += _text(pdf, MARGIN, y, width, INTERNAL_NOTICE, SMALL, "B", ALERT, draw)
    y += 2.5

    label = (document.verdict or "Key takeaway").upper()
    pdf.set_font(FONT, "B", size)
    badge_width = pdf.get_string_width(label) + 6
    badge_height = _line(size) + 2.5
    if draw:
        color = ACCENT if not document.verdict else _VERDICT_COLORS.get(document.verdict, _NEUTRAL_VERDICT)
        pdf.set_fill_color(*color)
        pdf.rect(MARGIN, y, badge_width, badge_height, style="F", round_corners=True, corner_radius=1.2)
        pdf.set_text_color(255, 255, 255)
        pdf.set_xy(MARGIN, y)
        pdf.cell(badge_width, badge_height, label, align="C")
    reason_x = MARGIN + badge_width + 4
    reason = _shorten(document.reason, MAX_ITEM_CHARS)
    reason_height = _text(pdf, reason_x, y + 1, pdf.w - MARGIN - reason_x, reason, size + 0.5, "", INK, draw)
    y += max(badge_height, reason_height + 1) + 3
    if draw:
        pdf.set_draw_color(*RULE)
        pdf.set_line_width(0.3)
        pdf.line(MARGIN, y, pdf.w - MARGIN, y)
    return y + 3


def _block(pdf: FPDF, block: Block, x: float, y: float, width: float, size: float, draw: bool) -> float:
    top = y
    y += _text(pdf, x, y, width, block.heading.upper(), size - 1, "B", ACCENT, draw) + 0.8
    indent = BULLET_INDENT if block.bullets else 0
    for item in block.items:
        if draw and block.bullets:
            _text(pdf, x, y, indent, "•", size, "", MUTED, draw)
        y += _text(pdf, x + indent, y, width - indent, item, size, "", INK, draw) + 0.8
    if block.hidden:
        note = f"+{block.hidden} more in the Markdown report"
        y += _text(pdf, x + indent, y, width - indent, note, size - 1, "", MUTED, draw)
    return y - top + 3.5


def _footer(pdf: FPDF, document: ReportDocument, plan: PagePlan, y: float, draw: bool) -> float:
    width = pdf.w - 2 * MARGIN
    top = y
    if draw:
        pdf.set_draw_color(*RULE)
        pdf.set_line_width(0.3)
        pdf.line(MARGIN, y, pdf.w - MARGIN, y)
    y += 2
    if plan.sources:
        y += _text(pdf, MARGIN, y, width, "SOURCES CITED", SMALL, "B", ACCENT, draw) + 0.5
        column_width = (width - GUTTER) / 2
        rows = math.ceil(len(plan.sources) / 2)
        if draw:
            pdf.set_font(FONT, "", SMALL)
            pdf.set_text_color(*INK)
            label_width = column_width - SOURCE_ID_WIDTH
            for index, source in enumerate(plan.sources):
                column, row = divmod(index, rows)
                pdf.set_xy(MARGIN + column * (column_width + GUTTER), y + row * _line(SMALL))
                pdf.cell(SOURCE_ID_WIDTH, _line(SMALL), source.id)
                pdf.cell(label_width, _line(SMALL), _fit(pdf, _source_label(source), label_width))
        y += rows * _line(SMALL) + 1.5
    if plan.limitations:
        limitations = "Limitations: " + "  ·  ".join(plan.limitations)
        y += _text(pdf, MARGIN, y, width, limitations, SMALL, "", MUTED, draw)
    notice = f"{AI_NOTICE} Produced by: {_clean(document.provider)}."
    y += _text(pdf, MARGIN, y + 0.5, width, notice, SMALL, "", MUTED, draw) + 0.5
    return y - top


def _text(
    pdf: FPDF, x: float, y: float, width: float, text: str, size: float, style: str,
    color: tuple[int, int, int], draw: bool,
) -> float:
    """Measure, and optionally draw, wrapped text; return its height."""
    pdf.set_font(FONT, style, size)
    lines = pdf.multi_cell(width, _line(size), text, align="L", dry_run=True, output="LINES")
    if draw:
        pdf.set_text_color(*color)
        pdf.set_xy(x, y)
        pdf.multi_cell(width, _line(size), text, align="L")
    return len(lines) * _line(size)


def _line(size: float) -> float:
    return size * _PT_TO_MM * LINE_SPACING


def _column_widths(pdf: FPDF) -> tuple[float, float]:
    usable = pdf.w - 2 * MARGIN - GUTTER
    return usable * MAIN_SHARE, usable * (1 - MAIN_SHARE)


def _source_label(source: Source) -> str:
    if source.kind is SourceKind.INTERNAL or not source.url:
        return f"{_clean(source.title)} (SFS internal)"
    site = (urlsplit(str(source.url)).hostname or "").removeprefix("www.")
    published = str(source.published) if source.published else "undated"
    return f"{site} · {published} · {_clean(source.title)}"


def _fit(pdf: FPDF, text: str, width: float) -> str:
    """Shorten text to one line of ``width`` with the current font."""
    if pdf.get_string_width(text) <= width:
        return text
    while text and pdf.get_string_width(text + "…") > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _shorten(text: str, limit: int) -> str:
    """Cap text at ``limit`` characters, keeping a trailing citation such as [W1, W2]."""
    text = _clean(text)
    if len(text) <= limit:
        return text
    match = _TRAILING_CITATION.search(text)
    citation = match.group(0) if match else ""
    body = text[: len(text) - len(citation)]
    body = body[: max(limit - len(citation) - 1, 0)].rsplit(" ", 1)[0]
    return f"{body}…{citation}"


def _clean(text: str) -> str:
    return " ".join(clean_text(text).split())

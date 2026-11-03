"""Render the full report as a single HTML page that opens in any browser.

Everything a model or a web page wrote is escaped, and the page's content
security policy blocks scripts and remote content, so a hostile source title
can neither run code nor load anything when the report is opened.
"""

import re
from html import escape

from sfs_intelligence.llm.guard import clean_text
from sfs_intelligence.reports.document import AI_NOTICE, INTERNAL_NOTICE, ReportDocument

_CITATION = re.compile(r"\[([A-Z]\d+(?:, [A-Z]\d+)*)\]")
_VERDICT_COLORS = {"Pursue": "#2e7d4f", "Research further": "#b07000"}
_NEUTRAL_VERDICT = "#646e78"
_ACCENT = "#2f6690"

_STYLE = """
body { font: 16px/1.55 "Segoe UI", system-ui, -apple-system, sans-serif; color: #1b2530;
  background: #fff; max-width: 760px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 1.6rem; margin: 0 0 4px; }
h2 { font-size: 1.1rem; margin: 28px 0 8px; border-bottom: 1px solid #cdd5dd; padding-bottom: 4px; }
.meta { color: #5a6876; font-size: 0.9rem; margin: 0 0 16px; }
.notice { background: #fbebeb; border-left: 4px solid #aa2828; padding: 8px 12px; font-weight: 600; }
.verdict { display: inline-block; color: #fff; font-weight: 700; border-radius: 4px;
  padding: 2px 10px; margin: 8px 0; }
ul { padding-left: 1.3em; } li { margin: 4px 0; }
.sources li { overflow-wrap: anywhere; }
.internal { color: #5a6876; font-size: 0.9rem; white-space: pre-line; }
a { color: #2f6690; }
@media print { body { padding: 0; } a { color: inherit; } }
"""


def to_html(document: ReportDocument) -> str:
    source_ids = {source.id for source in document.sources}

    def text(value: str) -> str:
        """Escape model and web text, then link citations to sources in this report."""
        escaped = escape(" ".join(clean_text(value).split()))
        return _CITATION.sub(lambda match: _cite(match.group(1), source_ids), escaped)

    def items(heading: str, values: tuple[str, ...]) -> list[str]:
        if not values:
            return []
        listed = [f"<li>{text(value)}</li>" for value in values]
        return [f"<h2>{escape(heading)}</h2>", "<ul>", *listed, "</ul>"]

    color = _VERDICT_COLORS.get(document.verdict, _NEUTRAL_VERDICT) if document.verdict else _ACCENT
    parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        # No scripts, frames or remote loads, whatever the report contains.
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; "
        "style-src 'unsafe-inline'\">",
        f"<title>{escape(document.title)}: {text(document.subject)}</title>",
        f"<style>{_STYLE}</style>",
        "</head>",
        "<body>",
        f"<h1>{escape(document.title)}: {text(document.subject)}</h1>",
        f'<p class="meta">Generated {document.generated}. {escape(AI_NOTICE)}</p>',
    ]
    if document.is_internal:
        parts.append(f'<p class="notice">{escape(INTERNAL_NOTICE)}</p>')
    parts += [
        f'<div class="verdict" style="background: {color}">{escape(document.headline)}</div>',
        f"<p>{text(document.reason)}</p>",
    ]
    parts += items("Top actions", document.top_actions)
    parts += items("Key warnings", document.warnings)
    parts += ["<h2>Summary</h2>", f"<p>{text(document.summary)}</p>"]
    if document.crm_record:
        parts += ["<h2>CRM record</h2>", '<p class="internal">']
        parts += [escape(clean_text(line)) for line in document.crm_record]
        parts.append("</p>")
    for section in document.sections:
        parts += items(section.heading, section.items)
    parts += ["<h2>Sources</h2>", '<ul class="sources">']
    parts += [_source_item(source) for source in document.sources]
    parts.append("</ul>")
    parts += items("Limitations", document.limitations)
    parts += [f'<p class="meta">Produced by: {text(document.provider)}</p>', "</body>", "</html>", ""]
    return "\n".join(parts)


def _cite(ids: str, known: set[str]) -> str:
    links = [
        f'<a href="#{source_id}">{source_id}</a>' if source_id in known else source_id
        for source_id in ids.split(", ")
    ]
    return f"[{', '.join(links)}]"


def _source_item(source) -> str:
    title = escape(" ".join(clean_text(source.title).split()))
    date = f" ({source.published})" if source.published else ""
    source_id = escape(source.id)
    line = f'<li id="{source_id}"><strong>[{source_id}]</strong> '
    if source.url:
        url = escape(str(source.url))
        # Source URLs are validated http(s) URLs, so the link cannot run script.
        line += f'<a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a>{date}'
    else:
        line += f"{title}{date}"
        if source.page_text:
            line += f'<div class="internal">{escape(clean_text(source.page_text))}</div>'
    return line + "</li>"

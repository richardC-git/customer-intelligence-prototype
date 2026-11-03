"""Briefing and export tests. All company data here is made up."""

import json
import re
from datetime import date

import pytest

from sfs_intelligence.llm.base import LLMError, LLMResponse
from sfs_intelligence.llm.guard import GuardedLLM
from sfs_intelligence.models.competitor import CompetitorReport
from sfs_intelligence.models.prospect import (
    Finding,
    Opportunity,
    ProspectAnalysis,
    ProspectProfile,
    Recommendation,
    Source,
    SourceKind,
)
from sfs_intelligence.reports.document import (
    MAX_TOP_ACTIONS,
    MAX_WARNINGS,
    NOT_ASSESSED,
    competitor_document,
    prospect_document,
)
from sfs_intelligence.reports.export import (
    DEFAULT_REPORTS_DIR,
    INTERNAL_NOTICE,
    export_report,
    to_markdown,
)
from sfs_intelligence.workflows.prospect import LLMProspectProvider

TODAY = date(2026, 11, 2)


def web(id="W1", published=date(2026, 6, 1), title="Acme news") -> Source:
    return Source(
        id=id, title=title, kind=SourceKind.PUBLIC, url="https://a.example/x", published=published
    )


def internal() -> Source:
    return Source(
        id="I1",
        title="SFS CRM record",
        kind=SourceKind.INTERNAL,
        page_text="Company: Acme\nPotential: 300,000",
    )


def finding(text="Acme opened a plant.", source="W1") -> Finding:
    return Finding(statement=text, source_ids=(source,))


def opportunity(number: int) -> Opportunity:
    return Opportunity(description=f"Action {number}", rationale="Good fit.", source_ids=("W1",))


def profile(**overrides) -> ProspectProfile:
    values = {
        "company_name": "Acme",
        "summary": "Acme makes roofs.",
        "recommendation": Recommendation.PURSUE,
        "recommendation_reason": finding("A new plant needs fasteners."),
        "sources": (web(),),
        "provider": "test",
    }
    return ProspectProfile(**(values | overrides))


def document(**overrides):
    return prospect_document(profile(**overrides), TODAY)


# Briefing


@pytest.mark.parametrize(
    ("value", "label"),
    [
        (Recommendation.PURSUE, "Pursue"),
        (Recommendation.RESEARCH_FURTHER, "Research further"),
        (Recommendation.LOW_PRIORITY, "Low priority"),
        (Recommendation.NOT_ENOUGH_EVIDENCE, "Not enough evidence"),
    ],
)
def test_verdicts_have_plain_labels(value, label):
    assert document(recommendation=value).verdict == label


def test_reason_shows_its_citations():
    assert document().reason == "A new plant needs fasteners. [W1]"


def test_without_analysis_the_verdict_says_not_assessed():
    result = document(recommendation=None, recommendation_reason=None)

    assert result.verdict == NOT_ASSESSED
    assert "No AI analysis" in result.reason


def test_top_actions_are_limited_and_keep_the_model_order():
    result = document(opportunities=tuple(opportunity(i) for i in range(1, 6)))

    assert result.top_actions == ("Action 1", "Action 2", "Action 3")
    assert len(result.top_actions) == MAX_TOP_ACTIONS


def test_warnings_are_limited():
    result = document(key_warnings=tuple(f"Warning {i}" for i in range(9)))

    assert len(result.warnings) == MAX_WARNINGS


def test_old_pricing_is_flagged_whatever_the_model_said():
    result = document(
        pricing_signals=(finding("Prices rose this year."),),
        sources=(web(published=date(2021, 11, 15)),),
    )

    assert "2021-11-15" in result.warnings[0]


def test_undated_pricing_is_flagged():
    result = document(pricing_signals=(finding(),), sources=(web(published=None),))

    assert "no publication dates" in result.warnings[0]


def test_recent_pricing_is_not_flagged():
    assert document(pricing_signals=(finding(),)).warnings == ()


def test_one_recent_source_clears_older_ones():
    sources = (web(published=date(2020, 1, 1)), web("W2"))
    signals = (finding(), finding("Newer price.", "W2"))

    assert document(pricing_signals=signals, sources=sources).warnings == ()


def test_uncited_old_sources_do_not_count():
    sources = (web(), web("W2", published=date(2010, 1, 1)))

    assert document(pricing_signals=(finding(),), sources=sources).warnings == ()


def test_a_recent_uncited_source_cannot_hide_old_pricing():
    sources = (web(published=date(2021, 11, 15)), web("W2", published=date(2026, 10, 1)))

    result = document(pricing_signals=(finding(),), sources=sources)

    assert "2021-11-15" in result.warnings[0]


def test_age_warning_outranks_others_so_the_limit_cannot_drop_it():
    result = document(
        pricing_signals=(finding(),),
        sources=(web(published=None),),
        key_warnings=tuple(f"Warning {i}" for i in range(9)),
    )

    assert "no publication dates" in result.warnings[0]


def test_competitor_briefing():
    report = CompetitorReport(
        competitor="Acme",
        summary="Found.",
        key_takeaway=finding("Prices are rising."),
        implications=tuple(finding(f"Action {i}") for i in range(5)),
        sources=(web(),),
        provider="test",
    )

    result = competitor_document(report, TODAY)

    assert result.headline == "Key takeaway"
    assert result.reason == "Prices are rising. [W1]"
    assert len(result.top_actions) == MAX_TOP_ACTIONS


def test_competitor_without_analysis_says_not_assessed():
    report = CompetitorReport(competitor="Acme", summary="Found.", provider="test")

    assert competitor_document(report, TODAY).headline == f"Verdict: {NOT_ASSESSED}"


# Workflow


class Model:
    name = "fake"
    model = "m1"

    def __init__(self, reply):
        self.reply = reply

    def complete(self, request):
        if isinstance(self.reply, Exception):
            raise self.reply
        return LLMResponse(text=self.reply, model=self.model)


class Research:
    def generate_profile(self, company_name):
        return ProspectProfile(
            company_name=company_name, summary="Found.", sources=(web(),), provider="Web"
        )


def analysis(**overrides) -> str:
    return json.dumps(
        {
            "subject_is_company": True,
            "recommendation": "research_further",
            "recommendation_reason": {"statement": "A reason.", "source_ids": ["W1"]},
            "summary": "Acme news.",
            "developments": [],
            "pricing_signals": [],
            "relationships": [],
            "opportunities": [],
            "limitations": ["First gap.", "Second gap.", "Third gap."],
        }
        | overrides
    )


def provider(reply) -> LLMProspectProvider:
    return LLMProspectProvider(Research(), GuardedLLM(Model(reply)))


def test_verdict_and_top_limitations_reach_the_profile():
    result = provider(analysis()).generate_profile("Acme")

    assert result.recommendation is Recommendation.RESEARCH_FURTHER
    assert result.key_warnings == ("First gap.", "Second gap.")


def test_failed_analysis_leads_with_the_warning():
    result = provider(LLMError("HTTP 500")).generate_profile("Acme")

    assert result.recommendation is None
    assert "HTTP 500" in result.key_warnings[0]


def test_unknown_verdict_is_rejected():
    result = provider(analysis(recommendation="definitely_buy")).generate_profile("Acme")

    assert result.recommendation is None
    assert "recommendation" in result.limitations[-1]


def test_verdict_reason_cannot_cite_an_unknown_source():
    reply = analysis(recommendation_reason={"statement": "A reason.", "source_ids": ["W9"]})

    assert provider(reply).generate_profile("Acme").recommendation is None


def test_verdict_reason_needs_a_citation():
    reply = analysis(recommendation_reason={"statement": "A reason.", "source_ids": []})

    assert provider(reply).generate_profile("Acme").recommendation is None


def test_non_company_gets_no_verdict():
    result = provider(analysis(subject_is_company=False)).generate_profile("Acme")

    assert result.recommendation is None


def test_model_schema_requires_a_verdict():
    assert {"recommendation", "recommendation_reason"} <= set(ProspectAnalysis.model_fields)


def test_failing_profile_citation_is_rejected_by_the_model():
    with pytest.raises(ValueError, match="not in sources"):
        profile(recommendation_reason=finding(source="W9"))


# Markdown


HOSTILE_TITLE = "![x](https://evil.example/p.png) <script>alert(1)</script> [click](https://evil.example)"


def test_markdown_leads_with_the_briefing():
    text = to_markdown(document(opportunities=(opportunity(1),), key_warnings=("Check this.",)))

    assert text.index("Verdict: Pursue") < text.index("Top actions")
    assert text.index("Top actions") < text.index("Key warnings") < text.index("Summary")
    assert text.index("Summary") < text.index("Sources")


def test_web_titles_cannot_become_images_links_or_html():
    text = to_markdown(document(sources=(web(title=HOSTILE_TITLE),)))

    assert not re.search(r"(?<!\\)\]\(", text)  # An unescaped ](  would form a link or image.
    assert "<script>" not in text


def test_ordinary_punctuation_stays_readable():
    text = to_markdown(document(summary="Approvals (FM Global, Miami-Dade)! Wow."))

    assert "Approvals (FM Global, Miami-Dade)! Wow." in text


def test_control_characters_are_removed():
    text = to_markdown(document(sources=(web(title="Acme\x1b[31m news‮"),)))

    assert "\x1b" not in text and "‮" not in text


def test_newlines_cannot_start_new_markdown_blocks():
    text = to_markdown(document(summary="Line one\n# Fake heading\n> Fake quote"))

    assert "\n# Fake heading" not in text


def test_newlines_cannot_add_list_items():
    text = to_markdown(document(summary="Line one\n\n- Fake item"))

    assert "\n- Fake item" not in text


def test_source_urls_stay_usable():
    assert "<https://a.example/x>" in to_markdown(document())


def test_internal_reports_are_marked():
    assert INTERNAL_NOTICE in to_markdown(document(sources=(web(), internal())))


def test_public_reports_are_not_marked():
    assert INTERNAL_NOTICE not in to_markdown(document())


def test_internal_source_text_is_included():
    assert "Potential: 300,000" in to_markdown(document(sources=(web(), internal())))


def test_empty_sections_are_left_out():
    assert "Pricing signals" not in to_markdown(document())


# Files


def test_exports_markdown(tmp_path):
    path = export_report(document(), "md", tmp_path)

    assert path.suffix == ".md"
    assert "Verdict: Pursue" in path.read_text(encoding="utf-8")


def test_exports_a_real_pdf(tmp_path):
    path = export_report(document(), "pdf", tmp_path)

    assert path.read_bytes().startswith(b"%PDF")


def test_pdf_handles_non_latin_text_long_urls_and_many_sources(tmp_path):
    long_url = "https://a.example/" + "x" * 400
    sources = tuple(
        Source(
            id=f"W{i}",
            title="日本製鉄 – Acme’s “news” ™ — roofing",
            kind=SourceKind.PUBLIC,
            url=long_url,
        )
        for i in range(1, 80)
    )

    path = export_report(document(sources=sources, summary="Müller’s plant 日本"), "pdf", tmp_path)

    assert path.stat().st_size > 1000


def test_pdf_handles_an_empty_report(tmp_path):
    empty = profile(recommendation=None, recommendation_reason=None, sources=())

    assert export_report(prospect_document(empty, TODAY), "pdf", tmp_path).exists()


@pytest.mark.parametrize("name", ["../../etc/evil", "A/B Roofing", "..", "/abs/path", "日本製鉄"])
def test_file_names_stay_inside_the_directory(tmp_path, name):
    path = export_report(document(company_name=name), "md", tmp_path)

    assert path.resolve().parent == tmp_path.resolve()


def test_unknown_format_is_refused(tmp_path):
    with pytest.raises(ValueError, match="Unsupported"):
        export_report(document(), "exe", tmp_path)


def test_reports_default_to_the_ignored_private_directory():
    assert str(DEFAULT_REPORTS_DIR).startswith("data/private")

"""Progress reporting and the compact CLI output. All company data here is made up."""

import pytest

from sfs_intelligence import cli, progress
from sfs_intelligence.models.competitor import CompetitorReport
from sfs_intelligence.models.crm import Competitor
from sfs_intelligence.models.prospect import Source, SourceKind
from sfs_intelligence.reports.document import competitor_document
from sfs_intelligence.research.search import SearchError
from sfs_intelligence.workflows.competitor import SearchCompetitorProvider
from tests.test_competitor import COMPETITOR, FakeSearch

SNIPPET = "Example Fasteners announced an eight percent price increase on screws."


def report(name="Example Fasteners") -> CompetitorReport:
    return CompetitorReport(
        competitor=name,
        summary="Found 1 web result.",
        sources=(
            Source(
                id="W1",
                title="Example Fasteners raises prices",
                kind=SourceKind.PUBLIC,
                url="https://www.news.example/prices",
                snippet=SNIPPET,
                page_text="A long page. " * 200,
            ),
        ),
        provider="Test",
    )


class FakeProvider:
    def generate_report(self, competitor):
        if competitor.name == "Broken Fasteners":
            raise SearchError("All searches failed.")
        progress.report("analyzing")
        return report(competitor.name)


def answers(monkeypatch, *replies):
    replies = iter(replies)
    monkeypatch.setattr("builtins.input", lambda _="": next(replies))


def test_report_does_nothing_without_a_callback():
    progress.report("searching")


def test_reporting_to_collects_steps_and_stops_afterwards():
    steps = []
    with progress.reporting_to(steps.append):
        progress.report("searching (1/2)")
    progress.report("after")
    assert steps == ["searching (1/2)"]


def test_search_workflow_reports_each_category():
    provider = SearchCompetitorProvider(FakeSearch())
    steps = []
    with progress.reporting_to(steps.append):
        provider.generate_report(COMPETITOR)

    total = len(provider._config.categories)
    assert steps == [f"searching ({number}/{total})" for number in range(1, total + 1)]


def test_progress_line_rewrites_one_terminal_line(capsys):
    line = cli.ProgressLine("Example Fasteners")
    line._live = True
    line.update("searching (1/9)")
    line.update("reading pages (10/24)")
    line.finish("done: Pursue")

    output = capsys.readouterr().out
    assert output.count("\n") == 1
    assert output.startswith("\rExample Fasteners  searching (1/9)...")
    # Padded so the longer step before it doesn't show through.
    assert output.endswith("\rExample Fasteners  done: Pursue" + " " * 12 + "\n")


def test_progress_line_prints_only_the_outcome_off_a_terminal(capsys):
    line = cli.ProgressLine("Example Fasteners")
    line.update("searching (1/9)")
    line.finish("done: Analyzed")

    assert capsys.readouterr().out == "Example Fasteners  done: Analyzed\n"


def test_one_failed_competitor_does_not_stop_the_rest(capsys):
    competitors = [
        Competitor(name="Broken Fasteners"),
        Competitor(name="Example Fasteners"),
    ]
    documents, failures = cli.run_competitors(competitors, FakeProvider())

    assert [document.subject for document in documents] == ["Example Fasteners"]
    assert failures == {"Broken Fasteners": "All searches failed."}
    assert capsys.readouterr().out.splitlines() == [
        "[1/2] Broken Fasteners   failed: All searches failed.",
        "[2/2] Example Fasteners  done: Not assessed",
    ]


def test_results_table_lists_reports_and_failures(monkeypatch, capsys):
    answers(monkeypatch, "")
    cli.review_reports([competitor_document(report())], {"Broken Fasteners": "quota exceeded"})

    lines = capsys.readouterr().out.splitlines()
    assert lines[1].split() == ["#", "Company", "Result", "Sources"]
    assert lines[2].split() == ["1", "Example", "Fasteners", "Not", "assessed", "1"]
    assert "Failed: quota exceeded" in lines[3]


@pytest.mark.parametrize("selection", ["9", "x"])
def test_results_table_rejects_unknown_selection(monkeypatch, capsys, selection):
    answers(monkeypatch, selection, "")
    cli.review_reports([competitor_document(report())], {})
    assert "Invalid selection." in capsys.readouterr().out


def test_briefing_hides_sources_until_the_full_report_is_asked_for(monkeypatch, capsys):
    answers(monkeypatch, "4")
    cli.review_report(competitor_document(report()))
    briefing = capsys.readouterr().out
    assert "Based on 1 web source." in briefing
    assert "[W1]" not in briefing

    answers(monkeypatch, "1", "4")
    cli.review_report(competitor_document(report()))
    full = capsys.readouterr().out
    assert "[W1] Example Fasteners raises prices, news.example" in full
    assert SNIPPET not in full
    assert "A long page." not in full

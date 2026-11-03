import os
import shutil
import sys
import textwrap
from collections.abc import Callable, Sequence
from urllib.parse import urlsplit

from dotenv import load_dotenv

from sfs_intelligence import progress
from sfs_intelligence.llm.base import PROVIDER_ENV_VAR, LLMError
from sfs_intelligence.ingestion.competitors import import_competitors
from sfs_intelligence.ingestion.crm import import_crm
from sfs_intelligence.ingestion.tables import ImportFileError
from sfs_intelligence.llm.guard import GuardedLLM, clean_text, llm_from_env
from sfs_intelligence.models.crm import Competitor
from sfs_intelligence.models.prospect import SourceKind
from sfs_intelligence.reports.document import (
    ReportDocument,
    competitor_document,
    prospect_document,
)
from sfs_intelligence.reports.export import export_report
from sfs_intelligence.research.fetch import HttpPageFetcher, PageCollector
from sfs_intelligence.research.follow_up import FollowUpResearch
from sfs_intelligence.research.search import SearchError, search_provider_from_env
from sfs_intelligence.storage.pages import page_store_from_env
from sfs_intelligence.storage.private import private_store_from_env
from sfs_intelligence.workflows.competitor import (
    CompetitorProvider,
    LLMCompetitorProvider,
    SearchCompetitorProvider,
    run_competitor_report,
)
from sfs_intelligence.workflows.prospect import (
    CRM_NOT_IMPORTED,
    CrmProspectProvider,
    LLMProspectProvider,
    PlaceholderProspectProvider,
    ProspectProvider,
    SearchProspectProvider,
    run_prospect_profile,
)


def display_header(title: str) -> None:
    print()
    print("=" * 50)
    print(title.center(50))
    print("=" * 50)
    print()


def pause() -> None:
    input("\nPress Enter to continue...")


class ProgressLine:
    """Show one company's current step on a single line, rewritten as it changes."""

    def __init__(self, label: str) -> None:
        self._label = label
        self._width = 0
        # Rewriting a line only works on a terminal; elsewhere just the outcome is printed.
        self._live = sys.stdout.isatty()

    def update(self, step: str) -> None:
        if self._live:
            self._write(f"{self._label}  {step}...")

    def finish(self, outcome: str) -> None:
        line = f"{self._label}  {outcome}"
        if self._live:
            self._write(line)
            print()
        else:
            print(line)

    def _write(self, text: str) -> None:
        # A line wider than the terminal wraps, and "\r" would then only rewrite its end.
        text = text[: shutil.get_terminal_size().columns - 1]
        print("\r" + text.ljust(self._width), end="", flush=True)
        self._width = len(text)


def wrapped(text: str, indent: str = "") -> None:
    print(textwrap.fill(text, 78, initial_indent=indent, subsequent_indent=" " * len(indent)))


def outcome(document: ReportDocument) -> str:
    return document.verdict or "Analyzed"


def source_counts(document: ReportDocument) -> str:
    web = sum(source.kind is SourceKind.PUBLIC for source in document.sources)
    internal = " and SFS internal data" if document.is_internal else ""
    return f"Based on {web} web source{'' if web == 1 else 's'}{internal}."


def display_briefing(document: ReportDocument) -> None:
    print(f"{document.subject}\n")
    print(document.headline.upper())
    wrapped(document.reason)
    for heading, items in (("Top actions", document.top_actions), ("Key warnings", document.warnings)):
        if items:
            print(f"\n{heading}:")
            for item in items:
                wrapped(item, "- ")
    print(f"\n{source_counts(document)}")


def display_full_report(document: ReportDocument) -> None:
    display_header(document.title)
    print(f"{document.subject}")
    print(f"Provider: {document.provider}\n")
    wrapped(f"Summary: {document.summary}")
    if document.crm_record:
        print("\nCRM record:")
        for line in document.crm_record:
            print(f"  {line}")
    for section in document.sections:
        if section.items:
            print(f"\n{section.heading}:")
            for item in section.items:
                wrapped(item, "- ")
    print("\nSources:")
    for source in document.sources:
        # Web text can contain escape codes that would control the terminal.
        date = f" ({source.published})" if source.published else ""
        where = urlsplit(str(source.url)).netloc.removeprefix("www.") if source.url else "SFS internal"
        print(f"[{source.id}] {clean_text(source.title)}{date}, {where}")
    print("\nLimitations:")
    for limitation in document.limitations:
        wrapped(limitation, "- ")


def save(documents: Sequence[ReportDocument], file_format: str) -> None:
    for document in documents:
        for path in export_report(document, file_format):
            print(f"Saved: {path}")


def review_report(document: ReportDocument) -> None:
    """Show the briefing, then the full report or a saved copy on request."""
    display_briefing(document)
    while True:
        print("\n1. Full report   2. Save full report   3. Save PDF brief   4. Done")
        match input("Selection: ").strip():
            case "1":
                display_full_report(document)
            case "2":
                save([document], "md")
            case "3":
                save([document], "pdf")
            case "4" | "":
                return
            case _:
                print("Invalid selection.")


def review_reports(documents: Sequence[ReportDocument], failures: dict[str, str]) -> None:
    """Summarize several reports in a table; open or save them on request."""
    rows = [
        (str(number), document.subject, outcome(document), str(len(document.sources)))
        for number, document in enumerate(documents, start=1)
    ]
    rows += [("-", name, f"Failed: {error}", "") for name, error in failures.items()]
    header = ("#", "Company", "Result", "Sources")
    widths = [max(len(row[column]) for row in (header, *rows)) for column in range(len(header))]
    print()
    for row in (header, *rows):
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())
    if not documents:
        return
    while True:
        print("\nEnter a report number to open it, F to save all full reports,")
        selection = input("P to save all PDF briefs, or press Enter when done: ").strip().lower()
        if not selection:
            return
        if selection == "f":
            save(documents, "md")
        elif selection == "p":
            save(documents, "pdf")
        elif selection.isdecimal() and 1 <= int(selection) <= len(documents):
            display_full_report(documents[int(selection) - 1])
        else:
            print("Invalid selection.")


def select_llm() -> GuardedLLM | None:
    """Return the configured model, or None to show sources without analysis."""
    if not os.environ.get(PROVIDER_ENV_VAR, "").strip():
        return None
    try:
        return llm_from_env()
    except LLMError as error:
        print(f"\nAI analysis unavailable ({error}) Showing search results only.")
        return None


# Prospect Profiling

def select_prospect_provider() -> ProspectProvider:
    """Combine whatever is configured. The CRM is local, so it is always included."""
    web: ProspectProvider
    follow_up = None
    try:
        search = search_provider_from_env()
        pages = PageCollector(HttpPageFetcher(), page_store_from_env())
        web = SearchProspectProvider(search, pages=pages)
        follow_up = FollowUpResearch(search, pages)
    except SearchError as error:
        print(f"\nWeb search unavailable ({error}) Using CRM data only.")
        web = PlaceholderProspectProvider()
    research = CrmProspectProvider(web, private_store_from_env())
    llm = select_llm()
    return LLMProspectProvider(research, llm, follow_up) if llm else research


def prospect_profiling_menu() -> None:
    while True:
        display_header("Prospect Profiling")

        if not private_store_from_env().counts()["crm_accounts"]:
            print(f"Note: {CRM_NOT_IMPORTED}\n")
        print("1. Generate Prospect Profile")
        print("2. Back to Main Menu")
        print()

        selection = input("Selection: ").strip()

        match selection:
            case "1":
                company_name = input("\nEnter company name: ").strip()

                provider = select_prospect_provider()
                print()
                line = ProgressLine(company_name)
                try:
                    with progress.reporting_to(line.update):
                        profile = run_prospect_profile(company_name, provider)
                except (ValueError, SearchError) as error:
                    line.finish(f"failed: {error}")
                    pause()
                    continue

                document = prospect_document(profile)
                line.finish(f"done: {outcome(document)}")
                display_header("Prospect Profile")
                review_report(document)

            case "2":
                return

            case _:
                print("\nInvalid selection. Please enter 1 or 2.")
                pause()


# Key Account Monitoring

def key_account_monitoring_menu() -> None:
    while True:
        display_header("Key Account Monitoring")

        print("1. Run Key Account Monitoring")
        print("2. Back to Main Menu")
        print()

        selection = input("Selection: ").strip()

        match selection:
            case "1":
                print("\nRunning key account monitoring...")
                # Future:
                # run_key_account_monitoring()

                pause()

            case "2":
                return

            case _:
                print("\nInvalid selection. Please enter 1 or 2.")
                pause()


# Competitor Monitoring

def select_competitor_provider() -> CompetitorProvider | None:
    try:
        search = search_provider_from_env()
    except SearchError as error:
        print(f"\nCompetitor monitoring needs web search ({error})")
        return None
    pages = PageCollector(HttpPageFetcher(), page_store_from_env())
    research = SearchCompetitorProvider(search, pages=pages)
    llm = select_llm()
    if llm is None:
        return research
    return LLMCompetitorProvider(research, llm, FollowUpResearch(search, pages))


def select_competitors() -> list[Competitor]:
    """Choose from the imported list; competitors are never typed in."""
    competitors = private_store_from_env().competitors()
    if not competitors:
        print("\nNo competitors imported. Use Data Management > Import Competitor List.")
        return []

    print()
    for number, competitor in enumerate(competitors, start=1):
        unit = f" ({competitor.business_unit})" if competitor.business_unit else ""
        print(f"{number}. {competitor.name}{unit}")
    print("A. All competitors (takes several minutes)")

    selection = input("\nSelection: ").strip().lower()
    if selection == "a":
        return competitors
    if selection.isdecimal() and 1 <= int(selection) <= len(competitors):
        return [competitors[int(selection) - 1]]
    print("\nInvalid selection.")
    return []


def run_competitors(
    competitors: Sequence[Competitor], provider: CompetitorProvider
) -> tuple[list[ReportDocument], dict[str, str]]:
    """Research each competitor on its own progress line; one failure doesn't stop the rest."""
    documents: list[ReportDocument] = []
    failures: dict[str, str] = {}
    name_width = max(len(competitor.name) for competitor in competitors)
    for number, competitor in enumerate(competitors, start=1):
        counter = f"[{number}/{len(competitors)}] " if len(competitors) > 1 else ""
        line = ProgressLine(f"{counter}{competitor.name.ljust(name_width)}")
        try:
            with progress.reporting_to(line.update):
                report = run_competitor_report(competitor, provider)
        except SearchError as error:
            failures[competitor.name] = str(error)
            line.finish(f"failed: {error}")
            continue
        document = competitor_document(report)
        documents.append(document)
        line.finish(f"done: {outcome(document)}")
    return documents, failures


def competitor_monitoring_menu() -> None:
    while True:
        display_header("Competitor Monitoring")

        print("1. Run Competitor Monitoring")
        print("2. Back to Main Menu")
        print()

        selection = input("Selection: ").strip()

        match selection:
            case "1":
                competitors = select_competitors()
                provider = select_competitor_provider() if competitors else None
                if provider is not None:
                    print()
                    documents, failures = run_competitors(competitors, provider)
                    if len(competitors) == 1 and documents:
                        display_header("Competitor Report")
                        review_report(documents[0])
                        continue
                    review_reports(documents, failures)

                pause()

            case "2":
                return

            case _:
                print("\nInvalid selection. Please enter 1 or 2.")
                pause()


# Data Management

def import_file(label: str, importer: Callable[..., int]) -> None:
    path = input(f"\nPath to {label} file (.xlsx or .csv): ").strip().strip("'\"")
    try:
        count = importer(path, private_store_from_env())
    except ImportFileError as error:
        print(f"\n{error}")
        return
    print(f"\nImported {count:,} {label} rows.")


def data_management_menu() -> None:
    while True:
        display_header("Data Management")

        print("1. Import CRM Data")
        print("2. Import Product Data")
        print("3. Import Key Account List")
        print("4. Import Competitor List")
        print("5. View Loaded Data Status")
        print("6. Back to Main Menu")
        print()

        selection = input("Selection: ").strip()

        match selection:
            case "1":
                import_file("CRM", import_crm)

                pause()

            case "2":
                print("\nImport Product Data selected.")
                # Future:
                # import_product_data()

                pause()

            case "3":
                print("\nImport Key Account List selected.")
                # Future:
                # import_account_list()

                pause()

            case "4":
                import_file("competitor", import_competitors)

                pause()

            case "5":
                print("\nLoaded Data Status")
                print("------------------")
                counts = private_store_from_env().counts()
                print(f"CRM accounts: {counts['crm_accounts']:,}")
                print(f"Competitors: {counts['competitors']:,}")

                pause()

            case "6":
                return

            case _:
                print("\nInvalid selection. Please enter a number from 1 to 6.")
                pause()

# Main Menu

def main_menu() -> None:
    while True:
        display_header("SFS Customer Intelligence Prototype")

        print("Please select an option:")
        print()
        print("1. Prospect Profiling")
        print("2. Key Account Monitoring")
        print("3. Competitor Monitoring")
        print("4. Data Management")
        print("5. Exit")
        print()

        selection = input("Selection: ").strip()

        match selection:
            case "1":
                prospect_profiling_menu()

            case "2":
                key_account_monitoring_menu()

            case "3":
                competitor_monitoring_menu()

            case "4":
                data_management_menu()

            case "5":
                print("\nExiting SFS Customer Intelligence Prototype.")
                return

            case _:
                print("\nInvalid selection. Please enter a number from 1 to 5.")
                pause()


def main() -> None:
    load_dotenv()
    main_menu()


if __name__ == "__main__":
    main()

import os
import textwrap
from collections.abc import Callable, Sequence

from dotenv import load_dotenv

from sfs_intelligence.llm.base import PROVIDER_ENV_VAR, LLMError
from sfs_intelligence.ingestion.competitors import import_competitors
from sfs_intelligence.ingestion.crm import import_crm
from sfs_intelligence.ingestion.tables import ImportFileError
from sfs_intelligence.llm.guard import GuardedLLM, clean_text, llm_from_env
from sfs_intelligence.models.competitor import CompetitorReport
from sfs_intelligence.models.crm import Competitor
from sfs_intelligence.models.prospect import Finding, Source
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


def display_sources(sources: tuple[Source, ...]) -> None:
    print("\nSources:")
    category = None
    for source in sources:
        if source.category and source.category != category:
            category = source.category
            print(f"\n  {category}")
        # Web text can contain escape codes that would control the terminal.
        date = f" ({source.published})" if source.published else ""
        print(f"[{source.id}] {clean_text(source.title)}{date} {source.url or '(internal)'}")
        if source.snippet:
            print(f"     {clean_text(source.snippet)}")
        if source.url is None and source.page_text:
            for line in clean_text(source.page_text).splitlines():
                print(f"     {line}")
        elif source.page_text:
            print(f"     (Page text: {len(source.page_text):,} characters)")


def display_findings(title: str, findings: tuple[Finding, ...]) -> None:
    if findings:
        print(f"\n{title}:")
        for finding in findings:
            print(f"- {finding.statement} [{', '.join(finding.source_ids)}]")


def display_briefing(document: ReportDocument) -> None:
    def wrapped(text: str, indent: str = "") -> None:
        print(textwrap.fill(text, 78, initial_indent=indent, subsequent_indent=" " * len(indent)))

    print(document.headline.upper())
    wrapped(document.reason)
    for heading, items in (("Top actions", document.top_actions), ("Key warnings", document.warnings)):
        if items:
            print(f"\n{heading}:")
            for item in items:
                wrapped(item, "- ")
    print("\n" + "-" * 50 + "\nFull report\n" + "-" * 50)


def offer_export(documents: Sequence[ReportDocument]) -> None:
    if not documents:
        return
    print("\nExport: 1. Markdown   2. PDF   3. No")
    match input("Selection: ").strip():
        case "1":
            file_format = "md"
        case "2":
            file_format = "pdf"
        case _:
            return
    for document in documents:
        print(f"Saved: {export_report(document, file_format)}")


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

        print("1. Generate Prospect Profile")
        print("2. Back to Main Menu")
        print()

        selection = input("Selection: ").strip()

        match selection:
            case "1":
                company_name = input("\nEnter company name: ").strip()

                provider = select_prospect_provider()
                print("\nResearching. This can take a minute...")

                try:
                    profile = run_prospect_profile(company_name, provider)
                except (ValueError, SearchError) as error:
                    print(f"\n{error}")
                    pause()
                    continue

                display_header("Prospect Profile")
                document = prospect_document(profile)
                print(f"Company: {profile.company_name}\n")
                display_briefing(document)
                print(f"Provider: {profile.provider}")
                print(f"\nSummary: {profile.summary}")
                display_findings("Recent developments", profile.developments)
                display_findings("Pricing signals", profile.pricing_signals)
                display_findings("Customers, suppliers & partners", profile.relationships)
                print("\nPotential opportunities:")
                for opportunity in profile.opportunities:
                    print(f"- {opportunity.description} [{', '.join(opportunity.source_ids)}]")
                    print(f"  Why: {opportunity.rationale}")
                if not profile.opportunities:
                    print("- None assessed.")
                if profile.related_customers:
                    print("\nMost similar existing customers:")
                    for customer in profile.related_customers:
                        print(f"- {customer.name}: {customer.similarity_reason}")
                if profile.sources:
                    display_sources(profile.sources)
                print("\nLimitations:")
                for limitation in profile.limitations:
                    print(f"- {limitation}")

                offer_export([document])
                pause()

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


def display_competitor_report(report: CompetitorReport) -> ReportDocument:
    display_header("Competitor Report")
    document = competitor_document(report)
    print(f"Competitor: {report.competitor}")
    if report.business_unit:
        print(f"Business unit: {report.business_unit}")
    print()
    display_briefing(document)
    print(f"Provider: {report.provider}")
    print(f"\nSummary: {report.summary}")
    display_findings("Recent developments", report.developments)
    display_findings("Pricing signals", report.pricing_signals)
    display_findings("Customers, suppliers & partners", report.relationships)
    display_findings("What this means for SFS", report.implications)
    display_sources(report.sources)
    print("\nLimitations:")
    for limitation in report.limitations:
        print(f"- {limitation}")
    return document


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
                    print("\nSearching the web and reading result pages. This can take a minute...")
                    documents = []
                    for competitor in competitors:
                        try:
                            report = run_competitor_report(competitor, provider)
                        except SearchError as error:
                            print(f"\n{competitor.name}: {error}")
                            continue
                        documents.append(display_competitor_report(report))
                    offer_export(documents)

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

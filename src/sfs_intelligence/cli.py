from dotenv import load_dotenv

from sfs_intelligence.research.search import SearchError, search_provider_from_env
from sfs_intelligence.workflows.prospect import (
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


# Prospect Profiling

def select_prospect_provider() -> ProspectProvider | None:
    """Use web search when configured; otherwise fall back to the offline placeholder."""
    try:
        return SearchProspectProvider(search_provider_from_env())
    except SearchError as error:
        print(f"\nWeb search unavailable ({error}) Using offline placeholder.")
        return None


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

                try:
                    profile = run_prospect_profile(company_name, select_prospect_provider())
                except (ValueError, SearchError) as error:
                    print(f"\n{error}")
                    pause()
                    continue

                display_header("Prospect Profile")
                print(f"Company: {profile.company_name}")
                print(f"Provider: {profile.provider}")
                print(f"\nSummary: {profile.summary}")
                print("\nPotential opportunities:")
                for opportunity in profile.opportunities:
                    print(f"- {opportunity.description} [{', '.join(opportunity.source_ids)}]")
                if not profile.opportunities:
                    print("- None assessed.")
                if profile.sources:
                    print("\nSources:")
                    for source in profile.sources:
                        print(f"[{source.id}] {source.title} {source.url or '(internal)'}")
                        if source.snippet:
                            print(f"     {source.snippet}")
                print("\nLimitations:")
                for limitation in profile.limitations:
                    print(f"- {limitation}")

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

def competitor_monitoring_menu() -> None:
    while True:
        display_header("Competitor Monitoring")

        print("1. Run Competitor Monitoring")
        print("2. Back to Main Menu")
        print()

        selection = input("Selection: ").strip()

        match selection:
            case "1":
                print("\nRunning competitor monitoring...")
                # Future:
                # run_competitor_monitoring()

                pause()

            case "2":
                return

            case _:
                print("\nInvalid selection. Please enter 1 or 2.")
                pause()


# Data Management

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
                print("\nImport CRM Data selected.")
                # Future:
                # import_crm_data()

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
                print("\nImport Competitor List selected.")
                # Future:
                # import_competitor_list()

                pause()

            case "5":
                print("\nLoaded Data Status")
                print("------------------")
                # Future:
                # display_data_status()

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

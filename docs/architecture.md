# Architecture

## Layout

```
src/sfs_intelligence/
├── cli.py          # Menu-driven entry point (`sfs-intelligence`)
├── models/         # Validated, provider-independent output data
├── workflows/      # One entry function per feature; input validation, no I/O
├── research/       # Web search (SearchProvider, Brave, categories) and page fetching
├── llm/            # (planned) language model adapters
├── ingestion/      # (planned) CRM, product, and list imports
├── matching/       # (planned) prospect-to-customer/product matching
├── analysis/       # (planned) monitoring and change detection
├── reports/        # (planned) report output
└── storage/        # Persistence behind interfaces (DuckDB page cache)
```

## Feature pattern

Each feature (Prospect Profiling today; Key Account Monitoring and Competitor
Monitoring next) follows the same shape, using
`workflows/prospect.py` as the reference:

1. **Model** (`models/`): a frozen pydantic model describing the feature's
   output. It does not depend on where the data came from.
2. **Provider interface** (`workflows/`): a `Protocol` with a single method
   that produces the model, e.g. `ProspectProvider.generate_profile()`.
3. **Workflow function** (`workflows/`): validates input, picks a provider
   (defaulting to an offline placeholder), and returns the model, e.g.
   `run_prospect_profile()`.
4. **CLI** (`cli.py`): collects input, calls the workflow function, and
   displays the result.

Real research or AI services are added as new provider classes. The CLI and
models do not change when a provider is swapped.

## Configuration

Providers are chosen at runtime from environment variables, loaded from `.env`
at startup (see `.env.example`). Code depends only on the interfaces, so
switching services is a configuration change plus, at most, one new adapter.

| Variable | Purpose |
| --- | --- |
| `SEARCH_PROVIDER` | Web search service (supported: `brave`) |
| `SEARCH_API_KEY` | API key for the search service |
| `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY` | Language model service (planned) |

To add a search service, implement `SearchProvider` in `research/` (return
results as public `Source` objects) and register it in
`search_provider_from_env()`.

### Search categories

`research/search_categories.toml` defines the information categories searched
for each prospect (one query per category) and how many results each returns.
`{company}` in a query is replaced with the quoted company name. Edit this file
to add categories or restrict a category to approved sites with `site:`; keep
to operators shared by major search engines (quotes, `OR`, `site:`) so queries
stay provider-independent. Results are tagged with their category label and
deduplicated by URL.

## Page fetching

After the category searches, `research/fetch.py` fetches each result page and
extracts its main text with trafilatura, so later analysis can read whole
pages rather than search snippets. `PageCollector` reuses cached pages fetched
within the last 7 days (failed fetches are retried), fetches different sites in
parallel but one page at a time per site, and saves results to the page cache.

To stay within the project's scope, the fetcher:

- identifies itself with an honest user agent and respects `robots.txt`;
- records pages behind logins or access controls (HTTP 401/402/403/407) and
  bot checks (CAPTCHAs) as `blocked` and does not work around them;
- fetches HTML only (no PDFs yet), with a timeout and a size limit.

Sources without page text keep their search snippet, and the profile lists
them under its limitations.

## Storage

Data is stored with DuckDB, behind interfaces (e.g. `PageStore`) so a backend
can be swapped without changing features. Public and private data are kept in
separate database files:

| Store | Default path | Contents |
| --- | --- | --- |
| Page cache | `data/cache.duckdb` (`CACHE_DB_PATH`) | Fetched page text, keyed by URL. Safe to delete; it is rebuilt as pages are fetched again. |
| Private data | `data/private/` (planned) | Imported CRM and product data. Never sent to external services without approval. |

DuckDB allows only one process to hold a database file open for writing, so
stores open a connection per operation and close it immediately. Avoid holding
connections open across user interactions.

Each fetched page records its status (`ok`, `failed`, or `blocked` for pages
deliberately skipped, such as those behind a login or CAPTCHA), the extracted
text, and a SHA-256 hash of that text for detecting changes between fetches.

## Placeholder providers

Until a real provider exists, each feature ships an offline placeholder that
returns an explicitly labelled result with no invented facts. This keeps the
workflow runnable and testable end to end without network access or data.

Prospect Profiling currently uses `SearchProspectProvider` when search is
configured: it runs the category searches and attaches the results as sources
but performs no analysis. The CLI falls back to the offline placeholder if search is not
configured. An LLM-backed provider will replace it by analyzing the same
sources and citing them in findings and opportunities.

## Source citation rule

Every claim in an output model must cite at least one source:

- Findings, opportunities, and related customers carry `source_ids`.
- Each ID must match an entry in the model's `sources` list; IDs must be unique.
- Public sources require a URL; internal sources (CRM, product data) do not.

Validation fails if any of these rules are broken, so unsupported AI-generated
conclusions are rejected before they reach the user. New output models should
enforce the same rule.

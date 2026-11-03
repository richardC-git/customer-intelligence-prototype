# Architecture

## Layout

```
src/sfs_intelligence/
├── cli.py          # Menu-driven entry point (`sfs-intelligence`)
├── models/         # Validated, provider-independent output data
├── workflows/      # One entry function per feature; input validation, no I/O
├── research/       # Web search (SearchProvider, Brave, categories), page fetching,
│                   # and model-directed follow-up searches
├── llm/            # Language model adapters and the safety layer around them
├── ingestion/      # CRM and competitor list imports (product data planned)
├── matching/       # Prospect-to-customer matching
├── analysis/       # (planned) monitoring and change detection
├── reports/        # Briefings and Markdown/PDF export
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
| `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY` | Language model service (see [Language models](#language-models)) |
| `LLM_BASE_URL` | Endpoint override; required for `azure` |
| `LLM_ALLOW_INTERNAL_DATA` | `true` allows CRM and field sales data to reach the model service. Approved for this project and set in `.env.example`; without it, analysis is skipped whenever such data is involved |
| `PRIVATE_DB_PATH` | Imported data (default `data/private/sfs.duckdb`) |

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

## Research agent

With a model configured, research runs in two rounds, so the model decides what
else to look for:

```
1. Code runs the fixed category searches
2. Model reads titles and snippets, and asks for up to 3 follow-up searches
3. Code checks each request and runs it through Brave
4. Model reads everything, plus internal data, and writes the cited analysis
```

The model asks through its JSON output (`ResearchPlan`) rather than through
provider tools, so this works the same on every provider. It never holds the
search key or calls Brave. This matters because a search query leaves the system:
if a model could search freely after reading CRM data, a poisoned page could have
it put that data in a query. So in `research/follow_up.py`:

- Round 2 sees public titles and snippets only, never internal data.
- A request is a topic from a fixed list plus a few plain words. The company name
  comes from the code, and quotes, links, and operators such as `site:` are refused.
- Round 2 happens once, with a fixed number of searches, so it cannot run on.
- Refused requests are disclosed. Failures keep the sources found so far.

Follow-up results go ahead of general web results so size limits drop those first.

Search results carry their publication date where the service gives one. Dates
reach the model and the sources list, and a missing date is shown as unknown
rather than treated as recent: public price notices can be years old.

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
| Private data | `data/private/sfs.duckdb` (`PRIVATE_DB_PATH`) | Imported CRM accounts and competitors. Only the records named below reach a model service, and street addresses never do. |

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

Prospect Profiling uses `SearchProspectProvider` when search is configured: it
runs the category searches and attaches the results as sources but performs no
analysis. When a model is also configured, `LLMProspectProvider` analyzes those
sources and cites them in findings and opportunities. If analysis fails or is
rejected, the user still gets the sources with a note explaining why. The CLI
falls back to the offline placeholder if search is not configured.

## Internal data

**CRM.** `CrmProspectProvider` wraps the search provider. It finds the typed name
in the CRM and adds two internal sources: the company's combined record (`I1`) and
up to five similar customers (`I2`). Internal sources come first so size limits
cannot drop them.

- Lookups match one company by name, after removing case, punctuation, and legal
  suffixes, and allow small typos. They never match partial names, so typing
  `customers` returns nothing. An inexact match is flagged in the limitations.
- Similar customers are ranked by `matching/crm.py`, not by a model: customers
  with turnover, sharing the sales office, then the region, then by turnover. This
  keeps the result repeatable and stops a model inventing a relationship. They use
  the CRM's fields only, so companies not in the CRM get no similar customers.
- The CRM's units are unconfirmed: `Potential` and `Turnover 12 months` are shown
  without a currency. Columns the importer does not name, such as `USAP`, are
  discarded and never stored.
- The CRM needs no API keys. Without search, a profile still shows the company's
  record and similar customers.

**Field sales.** Competitor Monitoring works from the imported list. Users choose
from it, so there is no free-text input. The source PDF has no prices: its
announcement cells are empty and its attachments are links to an internal wiki.
Pricing comes only from public record, through web search. New internal documents
can be added as `UntrustedDocument(internal=True)`.

## Reports

`reports/document.py` turns a profile or competitor report into one
`ReportDocument`, which the CLI and both exports use. It leads with a briefing: the
verdict and its cited reason, up to three top actions, and up to four key warnings.
The model chooses the verdict from a fixed list. The warning for old or undated
pricing is calculated from source dates, so it does not depend on the model. Without
AI analysis the verdict reads "Not assessed".

`reports/export.py` saves reports to `data/private/reports/`, which git ignores,
because reports can hold CRM data. Reports using internal data are marked as
internal. Web text is escaped in Markdown so a page title cannot become a remote
image or link.

The PDF (`reports/pdf.py`) is a one-page brief on US Letter paper, and the full
Markdown report is always saved next to it:

- Header: the verdict as a coloured badge, with its cited reason.
- Left column: summary, opportunities (or implications for competitors), and
  developments. Right column: key warnings, the CRM record, similar customers,
  pricing signals, and relationships. Sections are placed by `Section.side`.
- Footer: only the sources the page cites, as site, date, and title, plus the top
  three limitations and the AI notice.

The page is measured before it is drawn. If it does not fit, the text shrinks from
9 pt to 8 pt, then the last item of the longest section is cut until it fits, and
each section notes "+N more in the Markdown report". Summaries, warnings, and CRM
facts are never cut, and long items keep their citations.

Noto Sans (bundled in `reports/fonts/`, SIL Open Font License) covers Latin, Greek,
and Cyrillic, with Noto Sans JP as the fallback for Japanese and Chinese characters.
Only the characters used are embedded, so PDFs stay small.

## Language models

Features call `GuardedLLM.analyze()` with a fixed task, a list of documents, and
a pydantic output model. They never touch an adapter, so switching services is
a change to `.env`:

```
Feature -> GuardedLLM (guard.py) -> LLMProvider adapter -> model service
```

To add a service, implement `LLMProvider` in `llm/` (one `complete()` method) and
register it in `provider_from_env()`. To add a task, such as Competitor
Monitoring, add a prompt file to `llm/prompts/` and an output model that defines
only the fields a model may return; `ProspectAnalysis` is the reference. Prompts
ship inside the package so they work however it is installed.

Adapters call each service's HTTP API with the standard library, like the Brave
adapter, so no vendor SDK is needed. Each asks for JSON matching a schema, and
`schema.py` reduces that schema to the keywords all providers accept. Size limits
become text in the schema's descriptions so the model knows them. Output is
always validated here too, because not every service enforces the schema.

An invalid response gets one retry that says what was wrong, in fixed wording
that never repeats the model's text. Extra `limitations` beyond the first five are
dropped rather than rejected, since losing a caveat from a capped list is harmless.

| Provider | API | Notes |
| --- | --- | --- |
| `anthropic` | Messages | Limited to `claude-haiku-5-5` by `ALLOWED_MODELS` in `base.py`, to protect the project's credit |
| `openai` | Chat Completions | `LLM_BASE_URL` reaches other compatible services |
| `azure` | Azure OpenAI v1 | `LLM_MODEL` is the deployment name |
| `gemini` | `generateContent` | |
| `copilot` | GitHub Copilot SDK | Optional install. Needs a Copilot subscription |
| `m365-copilot` | Graph beta Chat API | Preview; see below |

GitHub Models is not an option: GitHub retired it on July 30, 2026.

Both Copilot options are constrained by what their APIs allow:

- **`copilot`**: the SDK drives a coding agent, so it could otherwise run commands
  and edit files. The adapter starts it in empty mode with no tools, rejects every
  permission request, uses an empty temporary directory, and discards the
  response if a tool starts anyway.
- **`m365-copilot`**: needs a Microsoft 365 Copilot license and a delegated user
  token, which expires, so it suits demos rather than unattended use. Microsoft
  does not support `/beta` APIs in production. It has no schema enforcement or
  separate system prompt, and web grounding can be turned off but searching the
  user's company data cannot. A reply built on company documents or carrying a
  sensitivity label is discarded, so if Copilot grounds often, this provider
  will often fall back to the plain sources.

## LLM security

**No model can be made impossible to manipulate**, and web pages are attacker-
controlled text by nature. So the design goal is that a fully manipulated model
still cannot do harm. `tests/test_llm_guard.py` tests exactly that, using a
stand-in model that returns whatever an attacker wants.

| Threat | Defence |
| --- | --- |
| Model takes actions | `LLMRequest` has no way to offer tools, so a model can only return text. No memory between calls |
| Data leaking through searches | The model only requests searches, from public text. See [Research agent](#research-agent) |
| Used as a general chatbot | No free-form prompts: tasks are fixed files. Names that read as requests are rejected, and nothing is called without sources |
| Injection through web pages | Invisible characters removed; pages that address an AI are withheld and disclosed to the user; data is fenced with a random per-request boundary |
| Malicious or off-task output | Output must match the task's model and is otherwise discarded. Links, markup, code, commands, credentials, and control characters are rejected, as are fields not defined by the model |
| Made-up or steered citations | Citations must be documents the model was actually sent, so a withheld page cannot be cited |
| Answers unrelated to the data | Each field must mostly use words found in the supplied documents and task, or the response is discarded. What a model writes about a non-company is never shown |
| CRM data sent externally | Only one company's record and its similar customers are sent, without addresses, and only when `LLM_ALLOW_INTERNAL_DATA=true`. There is no bulk lookup, so a model cannot be given or asked for the whole CRM |
| API key exposure | HTTPS required (except localhost), redirects never followed, keys sent in headers and removed from errors |
| Runaway cost | Capped document size, total input, output tokens, retries, and timeouts |

### What this does not prevent

- **Subtle bias from a page.** A page can state falsehoods in plain language
  ("Acme is the market leader") with nothing to detect. Output is labelled
  AI-generated with every claim cited so that people can check it.
- **Evading the pattern checks.** They catch common attacks, not all of them
  (for example, look-alike letters from other alphabets). They are one layer; a
  page that gets past them still meets the output checks.
- **Topics that are in the data.** Answers are held to what was supplied, and for
  prospects that includes what the web returned for the typed text. Someone who
  types `pizza recipes` as a company gets a web search. A working model reports
  that it is not a company and only fixed text is shown; a manipulated one could
  summarize those pages, briefly and with citations, but not go beyond them.
  Competitor Monitoring has no such route because it has no free text.
- **Grounding is a word check, not a fact check.** It stops unrelated text, not a
  false claim made from words in the documents. It is calibrated on examples
  rather than real model output, and English analysis of foreign-language pages
  may be rejected, which falls back to showing the sources.
- **Pages quoting attacks.** An article about prompt injection may be withheld
  by mistake. This is the safe direction, and the profile says so.
- **The provider's own handling of data.** Approval covers using this data, but
  check that the chosen provider's account does not train on or retain it.

Run `pytest -m live` with each provider before relying on it: the live tests
check how the real model behaves when a poisoned page reaches it.

## Source citation rule

Every claim in an output model must cite at least one source:

- Findings, opportunities, and related customers carry `source_ids`.
- Each ID must match an entry in the model's `sources` list; IDs must be unique.
- Public sources require a URL; internal sources (CRM, product data) do not.

Validation fails if any of these rules are broken, so unsupported AI-generated
conclusions are rejected before they reach the user. New output models should
enforce the same rule.

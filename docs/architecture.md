# Architecture

## Layout

```
src/sfs_intelligence/
├── cli.py          # Menu-driven entry point (`sfs-intelligence`)
├── models/         # Validated, provider-independent output data
├── workflows/      # One entry function per feature; input validation, no I/O
├── research/       # Web search: SearchProvider interface + Brave adapter
├── llm/            # (planned) language model adapters
├── ingestion/      # (planned) CRM, product, and list imports
├── matching/       # (planned) prospect-to-customer/product matching
├── analysis/       # (planned) monitoring and change detection
└── reports/        # (planned) report output
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

## Placeholder providers

Until a real provider exists, each feature ships an offline placeholder that
returns an explicitly labelled result with no invented facts. This keeps the
workflow runnable and testable end to end without network access or data.

Prospect Profiling currently uses `SearchProspectProvider` when search is
configured: it attaches web search results as sources but performs no
analysis. The CLI falls back to the offline placeholder if search is not
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

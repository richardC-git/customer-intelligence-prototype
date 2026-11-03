# SFS Customer Intelligence Prototype

Prototype customer intelligence tool for SFS Group: prospect profiling, key
account monitoring, and competitor monitoring. Built as a BCIT ISSP project.

> **Status:** early prototype. Prospect Profiling runs one search per
> information category (company website, news, social media, hiring, patents and
> trademarks, leadership, industry media, pricing, and partners), reads each
> result page, adds the company's CRM record and most similar
> customers, and, when a language model is configured, analyzes them into a
> cited profile. Competitor Monitoring does the same for the field sales
> competitor list. Key Account Monitoring and product data are not built yet.

## Requirements

- Python 3.12 or newer

## Setup

From the repository root, create a virtual environment and install the project:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

`-e` installs in editable mode, so code changes take effect without
reinstalling. `[dev]` adds the test tools. Re-run the install only after
changing `pyproject.toml`.

Then copy `.env.example` to `.env` and fill in the provider settings and API
keys. `.env` is ignored by git; never commit it.

```bash
cp .env.example .env
```

### What works with which keys

| Configured | What you get |
| --- | --- |
| Nothing | Imported CRM records and similar customers |
| Search key | Also web sources with page text, and Competitor Monitoring (no analysis) |
| Search key and a model | Also AI-directed follow-up searches, then cited summaries, opportunities, pricing signals, and who the company works with |

### Language model

AI analysis works with any of these. Set `LLM_PROVIDER`, `LLM_MODEL`, and
`LLM_API_KEY` in `.env`; nothing else changes.

| `LLM_PROVIDER` | Service | `LLM_API_KEY` |
| --- | --- | --- |
| `anthropic` | Claude (`claude-haiku-5-5` only, to protect the credit) | Anthropic API key |
| `openai` | OpenAI, or any compatible service via `LLM_BASE_URL` | OpenAI API key |
| `gemini` | Google Gemini | Gemini API key |
| `azure` | Azure OpenAI (also needs `LLM_BASE_URL`) | Azure key |
| `copilot` | GitHub Copilot (needs `pip install -e ".[copilot]"`) | GitHub token with Copilot access |
| `m365-copilot` | Microsoft 365 Copilot (preview) | Graph access token for a licensed user |

A model running on your own computer also works, with no paid key and no data
leaving the machine, if it offers an OpenAI-compatible API: use `openai`, set
`LLM_BASE_URL` to its local address, and put any text in `LLM_API_KEY`. This has
not yet been tested against a real local model.

See [docs/architecture.md](docs/architecture.md#language-models) for details,
including the limits of the Microsoft 365 option.

### Importing data

In **Data Management**, use Import CRM Data and Import Competitor List, then give
the path to the file. Both accept `.xlsx` or `.csv`, and importing replaces what
was stored. Imported data stays in `data/private/`, which git ignores.

- The CRM file needs the columns of the SFS export (see
  `data/sample/crm_sample.csv`).
- The competitor file needs `Competitor`, `Website`, and `Bus Unit` columns (see
  `data/sample/competitors_sample.csv`). The field sales PDF shows most names as
  logos, so its list is kept as a table rather than read from the PDF.

Product data has no importer yet. SFS Construction and Tegra Medical publish
their product lines on their websites rather than as a file, so
`data/sample/sfs_construction_product_lines.csv` and
`data/sample/tegra_medical_product_lines.csv` collect them from those public
pages, with a source link per row. `Bus Unit` maps SFS categories to the CRM
sales office codes (RC, FR, RS, TW); that mapping is ours and not yet confirmed
by SFS.

## Launching

With the virtual environment active:

```bash
sfs-intelligence
```

Alternatively, without installing:

```bash
PYTHONPATH=src python -m sfs_intelligence
```

Running `src/sfs_intelligence/cli.py` directly does not work, because the
package imports itself by name.

## Reports

While research runs, one line per company shows its current step. Each report then
opens with a briefing: a verdict, top actions, and key warnings. From there, open the
full report with its sources, or save it as Markdown or PDF to `data/private/reports/`.
When several competitors are researched at once, a table lists each result, including
any that failed, and each report can be opened or all of them saved. That folder is
ignored by git because reports can contain CRM data. The PDF is a one-page brief;
the full report is always saved next to it as Markdown.

## Running tests

```bash
pytest
```

The tests need no API keys or network. To check a real model service, fill in
the `LLM_*` settings and run the live tests, which make a few small paid calls:

```bash
pytest -m live
```

## Architecture

See [docs/architecture.md](docs/architecture.md).

## Data

Do not commit confidential, proprietary, or SFS-provided production/sample data
unless it has been explicitly approved for inclusion in the repository.

- Private project data is stored locally under `data/private/` (ignored by git).
- Sanitized development fixtures that are safe for source control may be placed
  under `data/sample/`.

Documents (`.docx`, `.pdf`, `.pptx`, etc.) and spreadsheets are ignored by git.
To commit one intentionally, use `git add -f <file>`.

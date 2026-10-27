# SFS Customer Intelligence Prototype

BCIT ISSP prototype customer intelligence system for SFS Group.

> **Status:** early prototype. Prospect Profiling runs end to end but returns a
> clearly labelled placeholder; no research, CRM, or product data is used yet.
> Key Account Monitoring, Competitor Monitoring, and Data Management are menu
> stubs.

## Requirements

- Python 3.12 or newer

## Setup

From the repository root, create a virtual environment and install the project:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
```

`-e` installs in editable mode, so code changes take effect without
reinstalling. Re-run `pip install -e .` only after changing `pyproject.toml`.

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

## Running tests

```bash
pip install pytest
pytest
```

## Data

Do not commit confidential, proprietary, or SFS-provided production/sample data
unless it has been explicitly approved for inclusion in the repository.

- Private project data is stored locally under `data/private/` (ignored by git).
- Sanitized development fixtures that are safe for source control may be placed
  under `data/sample/`.

Documents (`.docx`, `.pdf`, `.pptx`, etc.) and spreadsheets are ignored by git.
To commit one intentionally, use `git add -f <file>`.

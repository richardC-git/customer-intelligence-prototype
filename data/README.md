# Data Directory

Do not commit confidential, proprietary, or SFS-provided data unless it has
been explicitly approved for inclusion in the repository.

## Directories

- `sample/` — Sanitized or synthetic data safe for source control.
- `private/` — Local-only project data. This directory is ignored by Git.

Place any SFS-provided CRM exports, product lists, account lists, or other
restricted data under `data/private/`.
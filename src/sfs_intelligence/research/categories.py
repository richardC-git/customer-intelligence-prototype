"""Search categories that turn a company name into one query per information type."""

import tomllib
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

DEFAULT_CATEGORIES_PATH = Path(__file__).with_name("search_categories.toml")
COMPANY_PLACEHOLDER = "{company}"

NonEmptyStr = Annotated[str, Field(min_length=1)]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class SearchCategory(_FrozenModel):
    id: NonEmptyStr
    label: NonEmptyStr
    query: NonEmptyStr

    @field_validator("query")
    @classmethod
    def _query_names_company(cls, query: str) -> str:
        if COMPANY_PLACEHOLDER not in query:
            raise ValueError(f"query must contain {COMPANY_PLACEHOLDER}")
        return query

    def build_query(self, company_name: str) -> str:
        """Insert the company name as an exact phrase, dropping quotes that would break it."""
        name = " ".join(company_name.replace('"', " ").split())
        if not name:
            raise ValueError("A company name is required.")
        return self.query.replace(COMPANY_PLACEHOLDER, f'"{name}"')


class SearchCategoryConfig(_FrozenModel):
    results_per_category: int = Field(default=5, ge=1, le=20)
    categories: tuple[SearchCategory, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> "SearchCategoryConfig":
        ids = [category.id for category in self.categories]
        duplicates = {category_id for category_id in ids if ids.count(category_id) > 1}
        if duplicates:
            raise ValueError(f"Duplicate category ids: {sorted(duplicates)}")
        return self


def load_search_categories(path: Path = DEFAULT_CATEGORIES_PATH) -> SearchCategoryConfig:
    """Read and validate a search category TOML file."""
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
        return SearchCategoryConfig.model_validate(data)
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as error:
        raise ValueError(f"Invalid search category config {path}: {error}") from None

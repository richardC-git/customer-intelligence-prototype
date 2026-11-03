"""Internal SFS data imported from CRM exports and field sales lists."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

NonEmptyStr = Annotated[str, Field(min_length=1)]

# Source id of the matched company's CRM record in a profile.
CRM_RECORD_ID = "I1"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class CrmAccount(_FrozenModel):
    """One CRM row. A company with several locations has several accounts."""

    company_no: NonEmptyStr
    company: NonEmptyStr
    name_2: str = ""
    street: str = ""
    postal_code: str = ""
    city: str = ""
    country: str = ""
    region: str = ""
    account_group: str = ""
    sales_office: str = ""
    potential: float = 0.0
    turnover_12_months: float = 0.0

    @property
    def is_customer(self) -> bool:
        return self.account_group.startswith("Customer")


class Competitor(_FrozenModel):
    """An entry from the field sales competitor list."""

    name: NonEmptyStr
    website: str = ""
    business_unit: str = ""

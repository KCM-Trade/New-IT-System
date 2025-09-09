from __future__ import annotations

from typing import List, Literal, Union
from pydantic import BaseModel, Field


# Request rule models (discriminated by `type`)
class RuleCustomerIds(BaseModel):
    type: Literal["customer_ids"]
    ids: List[int] = Field(default_factory=list)
    include: bool = True


class RuleAccountIds(BaseModel):
    type: Literal["account_ids"]
    ids: List[str] = Field(default_factory=list)
    include: bool = True


class RuleCustomerTags(BaseModel):
    type: Literal["customer_tags"]
    # source is kept for forward-compat, but not used in backend logic now
    source: Literal["local", "crm"] | None = None
    tags: List[str] = Field(default_factory=list)
    operator: Literal["ANY", "ALL"] = "ANY"
    include: bool = True


Rule = Union[RuleCustomerIds, RuleAccountIds, RuleCustomerTags]


class AudiencePreviewRequest(BaseModel):
    rules: List[Rule] = Field(default_factory=list)


class AudiencePreviewItem(BaseModel):
    account_id: str
    client_id: int | None = None
    name: str | None = None
    group: str | None = None
    reg_date: str | None = None
    balance: float | None = None
    equity: float | None = None
    tags: List[str] = Field(default_factory=list)


class AudiencePreviewResponse(BaseModel):
    total: int
    items: List[AudiencePreviewItem]



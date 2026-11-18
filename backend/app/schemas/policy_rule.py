from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

MAX_RULE_NAME_LEN = 100


def _clean_rule_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("rule_name must not be empty")
    if len(value) > MAX_RULE_NAME_LEN:
        raise ValueError(f"rule_name must be at most {MAX_RULE_NAME_LEN} characters")
    return value


class PolicyRuleCreate(BaseModel):
    rule_name: str
    rule_type: str
    severity: Literal["low", "medium", "high"]
    parameters: dict = Field(default_factory=dict)
    is_active: bool = True

    @field_validator("rule_name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _clean_rule_name(v)


class PolicyRuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_name: str | None = None
    severity: Literal["low", "medium", "high"] | None = None
    parameters: dict | None = None
    is_active: bool | None = None

    @field_validator("rule_name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return None if v is None else _clean_rule_name(v)

    @model_validator(mode="after")
    def _not_empty(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        if any(getattr(self, f) is None for f in self.model_fields_set):
            raise ValueError("Fields to update must not be null")
        return self


class PolicyRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_name: str
    rule_type: str
    severity: str
    parameters: dict
    is_active: bool
    created_at: datetime

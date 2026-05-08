"""
Shared data models.

Lives in its own module so source-specific clients (`opsgenie`, `jsm`, and
any future ones) can import the exchange model without circular imports
through `main`.
"""

from datetime import datetime

from dateutil import parser
from pydantic import BaseModel, EmailStr, field_validator


class OnCallShift(BaseModel):
    """One on-call shift, normalized across all data sources."""
    start: datetime
    end: datetime
    hours: float
    user: EmailStr

    @field_validator('start', 'end', mode='before')
    def parse_datetime(cls, v):
        if isinstance(v, str):
            return parser.parse(v)
        return v

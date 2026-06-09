from __future__ import annotations

from enum import Enum

from pydantic import Field
from pydantic.types import Annotated


class Severity(str, Enum):
    major = "major"
    minor = "minor"


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class Verdict(str, Enum):
    reject = "reject"
    weak_reject = "weak reject"
    borderline = "borderline"
    weak_accept = "weak accept"
    accept = "accept"


class PlotType(str, Enum):
    line = "line"
    bar = "bar"
    scatter = "scatter"
    table = "table"


class DocKind(str, Enum):
    book = "book"
    paper = "paper"


RidStr = Annotated[str, Field(pattern=r"^RID:[^\s]+$")]
CiteKeyStr = Annotated[str, Field(pattern=r"^[A-Za-z0-9_:\-\.]+$", max_length=128)]
NodeKeyStr = Annotated[str, Field(pattern=r"^(book|paper|\d+(?:-\d+)*)$")]

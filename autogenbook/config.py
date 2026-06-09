from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AppConfig:
    """
    Placeholder for future shared configuration.
    """

    mode: str = "book"
    out_dir: Optional[str] = None

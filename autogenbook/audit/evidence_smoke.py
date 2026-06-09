from __future__ import annotations

from .evidence_rules import EvidenceWindowConfig, numeric_claim_has_evidence


SUPPORTED = r"""
We report a 12.5% improvement over baseline.\footnote{Source: RID:run:exp1:metrics.json}
"""

UNSUPPORTED = r"""
We report a 12.5% improvement over baseline without any citation.
"""


def _span_for_number(text: str) -> dict:
    idx = text.find("12.5%")
    return {"start": idx, "end": idx + len("12.5%")}


def main() -> None:
    config = EvidenceWindowConfig()
    supported = numeric_claim_has_evidence(SUPPORTED, _span_for_number(SUPPORTED), config)
    unsupported = numeric_claim_has_evidence(UNSUPPORTED, _span_for_number(UNSUPPORTED), config)
    print("supported:", supported)
    print("unsupported:", unsupported)


if __name__ == "__main__":
    main()

from __future__ import annotations

from autogenbook.prompts.book_loader import DEFAULT_BOOK_PROMPT_FILES, load_book_prompts
from autogenbook.prompts.paper_loader import DEFAULT_PAPER_PROMPT_FILES, load_paper_prompts
from autogenbook.prompts.presentation_loader import (
    DEFAULT_PRESENTATION_PROMPT_FILES,
    load_presentation_prompts,
)
from autogenbook.prompts.proposal_loader import (
    DEFAULT_PROPOSAL_PROMPT_FILES,
    load_proposal_prompts,
)
from autogenbook.prompts.scientist_loader import (
    DEFAULT_SCIENTIST_PROMPT_FILES,
    load_scientist_prompts,
)


def _check_pack(label: str, prompts: dict, expected: dict) -> None:
    missing = [key for key in expected if not prompts.get(key) or not prompts[key].strip()]
    if missing:
        raise SystemExit(f"Missing {label} prompts: {', '.join(missing)}")
    print(f"{label.capitalize()} prompt pack OK.")


def main() -> int:
    _check_pack("book", load_book_prompts(), DEFAULT_BOOK_PROMPT_FILES)
    _check_pack("paper", load_paper_prompts(), DEFAULT_PAPER_PROMPT_FILES)
    _check_pack("presentation", load_presentation_prompts(), DEFAULT_PRESENTATION_PROMPT_FILES)
    _check_pack("scientist", load_scientist_prompts(), DEFAULT_SCIENTIST_PROMPT_FILES)
    _check_pack("proposal", load_proposal_prompts(), DEFAULT_PROPOSAL_PROMPT_FILES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from main import parse_args
from autogenbook.prompts.proposal_loader import load_proposal_prompts
from autogenbook.pipelines.proposal_pipeline import (
    _apply_language,
    _append_user_inputs,
    _parse_user_inputs,
)
from autogenbook.schemas.proposal import (
    ProposalLLM1Output,
    ProposalOutlineOutput,
    ProposalReviewOutput,
)
from autogenbook.schemas.validate import validate_or_raise
from autogenbook.retrieval import mcp_papers


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def _test_cli_parsing() -> None:
    args = parse_args(
        [
            "--mode",
            "proposal",
            "--kb1-dir",
            "kb1",
            "--kb2-dir",
            "kb2",
            "--proposal-input",
            "proposal_input.txt",
            "--max-iters",
            "2",
            "--min-section-citations",
            "2",
            "--proposal-llm1-model",
            "openai/gpt-5-mini",
            "--proposal-llm2-model",
            "openai/gpt-5-mini",
            "--proposal-llm3-model",
            "openai/gpt-5-mini",
            "--proposal-llm4-model",
            "openai/gpt-5-mini",
            "--proposal-llm5-model",
            "openai/gpt-5-mini",
            "--resume",
            "--out-dir",
            "out_dir",
        ]
    )
    _assert(args.mode == "proposal", "Expected mode=proposal")
    _assert(args.kb1_dir == "kb1", "Expected kb1-dir to parse")
    _assert(args.kb2_dir == "kb2", "Expected kb2-dir to parse")
    _assert(args.proposal_input == "proposal_input.txt", "Expected proposal-input to parse")
    _assert(args.max_iters == 2, "Expected max-iters to parse")
    _assert(args.min_section_citations == 2, "Expected min-section-citations to parse")
    _assert(args.proposal_llm1_model == "openai/gpt-5-mini", "Expected proposal-llm1-model to parse")
    _assert(args.proposal_llm2_model == "openai/gpt-5-mini", "Expected proposal-llm2-model to parse")
    _assert(args.proposal_llm3_model == "openai/gpt-5-mini", "Expected proposal-llm3-model to parse")
    _assert(args.proposal_llm4_model == "openai/gpt-5-mini", "Expected proposal-llm4-model to parse")
    _assert(args.proposal_llm5_model == "openai/gpt-5-mini", "Expected proposal-llm5-model to parse")
    _assert(args.resume is True, "Expected resume to parse")
    _assert(args.out_dir == "out_dir", "Expected out-dir to parse")


def _test_prompt_loader_language() -> None:
    prompts = load_proposal_prompts()
    rendered = _apply_language(prompts, "English")
    for key, text in rendered.items():
        if "{{LANGUAGE}}" in prompts.get(key, ""):
            _assert("{{LANGUAGE}}" not in text, f"Placeholder not replaced in {key}")


def _test_schema_validation() -> None:
    llm1 = {
        "language": "English",
        "kb1_requirement_digest": [
            {
                "id": "REQ-001",
                "title": "Mandatory sections",
                "type": "section",
                "requirement_text": "Include objectives and methods.",
                "priority": "must",
                "evidence": [
                    {
                        "source_id": "KB1-001",
                        "file": "requirements.pdf",
                        "chunk_id": "RID:kb:reqs:chunk:1",
                        "quote": "Objectives and methods are required.",
                    }
                ],
            }
        ],
        "format_spec": {
            "required_output_formats": ["markdown"],
            "citation_standard": "ISO690",
            "language": "English",
            "limits": {"page_limit": None, "word_limit": None, "char_limit": None},
        },
        "meta_prompt_for_llm2": "Draft the outline according to requirements.",
        "unknown_or_ambiguous": [],
    }
    validate_or_raise(ProposalLLM1Output, llm1)

    llm2 = {
        "language": "English",
        "project_metadata": {
            "title": "Sample Proposal",
            "author": "Author Name",
            "keywords": ["AI", "RAG"],
            "grant_call": "Call 2025",
        },
        "outline": [
            {
                "id": "SEC-001",
                "title": "Objectives",
                "purpose": "Define project objectives.",
                "what_to_write": ["List main objectives and success criteria."],
                "compliance_mapping": ["REQ-001"],
                "expected_evidence": [{"source": "KB1", "note": "Use requirement wording."}],
                "subsections": [],
                "target_length": {"words": 200, "pages": None},
            }
        ],
        "annexes": [],
        "open_questions_for_opponent": [],
    }
    validate_or_raise(ProposalOutlineOutput, llm2)

    llm3 = {
        "language": "English",
        "compliance_assessment": {
            "is_compliant": True,
            "missing_requirements": [],
            "other_issues": [],
        },
        "scientific_assessment": {
            "is_thematically_aligned": True,
            "gaps": [],
            "innovation_score_0_10": 6,
            "feasibility_score_0_10": 7,
        },
        "user_questions": [],
        "instructions_to_llm1": [],
        "instructions_to_llm2": [],
        "should_iterate": False,
        "stop_reason": "compliant",
    }
    validate_or_raise(ProposalReviewOutput, llm3)


def _test_missing_info_flow() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "proposal_input.txt"
        path.write_text("Title: Test Proposal\n", encoding="utf-8")

        _append_user_inputs(path, ["Q-001: Answer 1", "Q-002_REFUSAL: REFUSED"])
        answered, refused = _parse_user_inputs(path.read_text(encoding="utf-8"))
        _assert("Q-001" in answered, "Expected Q-001 to be marked answered")
        _assert("Q-002" in answered, "Expected Q-002 to be marked answered")
        _assert("Q-002" in refused, "Expected Q-002 to be marked refused")

        _append_user_inputs(path, ["Q-001: Answer 2"])
        answered2, refused2 = _parse_user_inputs(path.read_text(encoding="utf-8"))
        _assert("Q-001" in answered2, "Expected Q-001 to stay answered")
        _assert("Q-002" in refused2, "Expected Q-002 to stay refused")


def _test_mcp_cache_helpers() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cache_dir = Path(tmp_dir)
        # Write 3 cache files, then prune to 2.
        for i in range(3):
            path = cache_dir / f"tool_{i}.json"
            payload = {"value": i}
            mcp_papers._save_cached_payload(path, payload)
            time.sleep(0.01)
        mcp_papers._prune_cache(cache_dir, max_files=2)
        files = list(cache_dir.glob("*.json"))
        _assert(len(files) == 2, "Expected cache prune to keep 2 files")

        # TTL expiration removes stale payloads.
        stale_path = cache_dir / "stale.json"
        stale_record = {"timestamp": time.time() - 3600, "payload": {"value": "stale"}}
        stale_path.write_text(json.dumps(stale_record), encoding="utf-8")
        payload = mcp_papers._load_cached_payload(stale_path, ttl_s=1)
        _assert(payload is None, "Expected stale payload to expire")


def main() -> int:
    _test_cli_parsing()
    _test_prompt_loader_language()
    _test_schema_validation()
    _test_missing_info_flow()
    _test_mcp_cache_helpers()
    print("Proposal smoke checks OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

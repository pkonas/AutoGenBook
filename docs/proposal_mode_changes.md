# Proposal Mode Changes

This document summarizes updates for the proposal mode pipeline and related tooling.

## Added
- `autogenbook/smoke_proposal.py` smoke checks (CLI parsing, prompt language replacement, schema validation, missing-info flow).
- `docs/proposal_mode_changes.md` (this file).

## Updated
- `autogenbook/pipelines/proposal_pipeline.py` final review loop (LLM5), citation enforcement, bibliography re-application, and final conversions.
- `autogenbook/pipelines/proposal_pipeline.py` citation normalization and ISO 690 bibliography formatting.
- `README.md` proposal mode CLI example snippet.

## Notes
- Smoke checks are self-contained and do not call external APIs.

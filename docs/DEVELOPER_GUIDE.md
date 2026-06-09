# Developer Guide

## Dev environment setup

- Python 3.10+ is required by dependency constraints. (`requirements.txt`)
- Install dependencies:

```bash
pip install -r requirements.txt
```

Source: `requirements.txt`

## Running tests

- Unit tests: `python -m unittest`. (`tests/test_reviewer_mode.py`)
- Prompt pack smoke tests: `python -m autogenbook.smoke_prompts`. (`autogenbook/smoke_prompts.py`)
- Schema smoke tests: `python -m autogenbook.schemas.smoke`. (`autogenbook/schemas/smoke.py`)
- End-to-end smoke (requires API key unless fast mode): `python -m autogenbook.smoke_test`. (`autogenbook/smoke_test.py`)

## Repo conventions

- Pipelines live under `autogenbook/pipelines/`. (`autogenbook/pipelines/*`)
- Prompt packs are stored in `prompts/<mode>/` and loaded by `autogenbook/prompts/*_loader.py`. (`autogenbook/prompts/*_loader.py`)
- Agent schemas are in `autogenbook/schemas/` and validated in `autogenbook/agents/base.py`. (`autogenbook/schemas/*`, `autogenbook/agents/base.py:BaseAgent`)
- Long-form book generation logic lives in `book_builder.py`. (`book_builder.py`)

## Adding a new feature or module

### Add a new pipeline mode

1. Create a pipeline module under `autogenbook/pipelines/` with `run_<mode>()`. (`autogenbook/pipelines/*`)
2. Register it in `autogenbook/orchestrator.py:run`. (`autogenbook/orchestrator.py:run`)
3. Add CLI flags in `main.py:parse_args`. (`main.py:parse_args`)
4. Add prompt files in `prompts/<mode>/` and a loader in `autogenbook/prompts/<mode>_loader.py`. (`autogenbook/prompts/*_loader.py`)

### Add a new agent

1. Implement the agent class under `autogenbook/agents/`. (`autogenbook/agents/*`)
2. Define or reuse a Pydantic schema under `autogenbook/schemas/`. (`autogenbook/schemas/*`)
3. Wire the agent into a pipeline and prompt pack. (`autogenbook/agents/base.py:BaseAgent`, `autogenbook/prompts/agent_prompts.py`)

### Add a new CLI flag

1. Update `main.py:parse_args` with the new flag. (`main.py:parse_args`)
2. Consume the flag in the appropriate pipeline. (`autogenbook/pipelines/*`)

## Linting and formatting

No lint/format configuration is referenced by scripts or runtime code paths. (`scripts/check_latex_log.py`, `autogenbook/*`). (Needs confirmation for repo-wide config files.)

## Release / versioning

No release process or versioning scheme is referenced by runtime code paths. (`main.py`, `autogenbook/*`). (Needs confirmation.)

## Contribution guidelines (lightweight)

- Keep changes mode-scoped and update the relevant prompt pack. (`autogenbook/prompts/*_loader.py`)
- Add or update tests when changing pipeline behavior. (`tests/test_reviewer_mode.py`)
- Document new flags or features in `docs/API_REFERENCE.md` and `docs/CONFIGURATION.md`. (`docs/API_REFERENCE.md`, `docs/CONFIGURATION.md`)

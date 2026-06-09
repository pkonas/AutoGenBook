# Operations

## Install / build

Install Python dependencies from `requirements.txt`. (`requirements.txt`)

```bash
pip install -r requirements.txt
```

## Run locally

Run via the CLI entrypoint `main.py`. (`main.py:main`)

```bash
python main.py --mode book --input input/book/book_input.txt --out-dir output/book/out_book
```

The run context writes artifacts to `--out-dir` and stores metadata in `run_meta.json`. (`autogenbook/state.py:RunContext`, `autogenbook/llm_usage.py:write_run_meta`)

## Run in production

- Ensure `OPENROUTER_API_KEY` is set for OpenRouter or set `AUTOGENBOOK_LLM_BASE_URL` for local endpoints. (`openrouter_llm.py:OpenRouterLLM.__init__`)
- Install LuaLaTeX if you need PDF output. (`book_builder.py:compile_pdf`)
- Use `--audit-mode strict` for CI-style gating when audits are enabled. (`autogenbook/audit/latex_auditor.py:audit_latex`, `autogenbook/pipelines/paper_pipeline.py:run_paper`)

Deployment is not codified in runtime code paths; no container/orchestration logic is referenced by the CLI. (`main.py`, `autogenbook/orchestrator.py`). (Needs confirmation for repo-wide manifests.)

## Logging and observability

- Console and file logs go to `out_dir/logs/run.log`. (`autogenbook/logging.py:get_logger`, `autogenbook/paths.py:default_run_paths`)
- LLM usage is appended to `out_dir/llm_usage.jsonl`. (`autogenbook/llm_usage.py:log_usage`)
- Audits write `audit_report.json` when enabled. (`autogenbook/audit/latex_auditor.py:audit_latex`)

## Scaling guidance

Runs are single-process and synchronous; scale by running multiple independent CLI processes, each with its own `--out-dir`. (`autogenbook/pipelines/*`, `book_builder.py:generate_contents`)

## Backup / restore

- Preserve `--out-dir` to keep section files, structure graphs, and logs. (`autogenbook/state.py:RunContext`, `book_builder.py:generate_contents`)
- Preserve `.kb_cache` inside `--out-dir` to avoid rebuilding KBs. (`rag_kb.py:KnowledgeBase.build_from_directory`)

## Security hardening checklist

- Keep API keys in environment variables, not in files. (`openrouter_llm.py:OpenRouterLLM.__init__`, `mcp_gateway.py:MCPGatewayClient.__init__`)
- Disable MCP gateway when not needed: `MCP_GATEWAY_ENABLE=0`. (`mcp_gateway.py:MCPGatewayClient.__init__`)
- Use local KB sources you trust; retrieval sanitizes some prompt-injection patterns but does not guarantee safety. (`autogenbook/retrieval/sanitize.py:sanitize_context_text`)
- Scientist mode patching enforces strict diff safety checks. (`autogenbook/runner/patch_apply.py:apply_unified_diff`)

# Security

## Secrets and credentials

- LLM API keys are read from environment variables (e.g., `OPENROUTER_API_KEY`, `AUTOGENBOOK_LLM_API_KEY`, `OPENAI_API_KEY`) and never from files. (`openrouter_llm.py:OpenRouterLLM.__init__`)
- MCP gateway credentials are read from environment variables. (`mcp_gateway.py:MCPGatewayClient.__init__`)
- Tavily API key is read from `TAVILY_API_KEY`. (`autogenbook/retrieval/tavily.py:search_web`)

## Prompt-injection mitigation

- Retrieved context is sanitized to drop common prompt-injection patterns. (`autogenbook/retrieval/sanitize.py:sanitize_context_text`)

## Safe code patching (scientist mode)

- Patch application rejects forbidden imports and suspicious strings. (`autogenbook/runner/patch_apply.py:_safety_scan_diff`)
- Patches are constrained to the experiment workdir and disallow path traversal. (`autogenbook/runner/patch_apply.py:_sanitize_patch_path`)

## Data handling

- KB files are read locally and cached to `.kb_cache` under the output directory. (`rag_kb.py:KnowledgeBase.build_from_directory`)
- Run artifacts, logs, and metadata are stored under `--out-dir`. (`autogenbook/state.py:RunContext`, `autogenbook/llm_usage.py:write_run_meta`)

## Network surface

- LLM requests go to the configured base URL (default OpenRouter; override via `AUTOGENBOOK_LLM_BASE_URL`). (`openrouter_llm.py:OpenRouterLLM.__init__`)
- Optional web retrieval calls MCP gateway tools or Tavily search. (`autogenbook/retrieval/mcp_papers.py:MCPPaperRetriever`, `autogenbook/retrieval/tavily.py:search_web`, `mcp_gateway.py:MCPGatewayClient`)

# Configuration

## Configuration sources and precedence

1. CLI flags (highest priority). (`main.py:parse_args`)
2. Environment variables used by specific subsystems. (`openrouter_llm.py`, `rag_kb.py`, `mcp_gateway.py`, `autogenbook/pipelines/*`)
3. Defaults embedded in code (lowest priority). (`main.py:parse_args`, `openrouter_llm.py:LLMConfig`, `rag_kb.py:KnowledgeBase.build_from_directory`)

Model override precedence for proposal/reviewer: CLI flag first, then environment variable. (`autogenbook/pipelines/proposal_pipeline.py:_resolve_model_override`, `autogenbook/pipelines/reviewer_pipeline.py:_resolve_model_override`)

## CLI flags

See `docs/API_REFERENCE.md` for the full CLI reference. (`docs/API_REFERENCE.md`)

## Config files

| File | Purpose | Source |
| --- | --- | --- |
| `book_structure.json` | Default structure JSON for book mode. | `main.py:parse_args` |
| `paper_structure.json` | Default structure JSON for paper mode (auto-selected). | `main.py:main` |
| `presentation_structure.json` | Default structure JSON for presentation mode (auto-selected). | `main.py:main` |
| `input/book/book_input.txt` | Sample book input. | `input/book/book_input.txt` |
| `input/paper/paper_input.txt` | Sample paper input. | `input/paper/paper_input.txt` |
| `input/presentation/presentation_input.txt` | Sample presentation input. | `input/presentation/presentation_input.txt` |
| `input/proposal/proposal_input.txt` | Sample proposal input (requires language line). | `autogenbook/pipelines/proposal_pipeline.py:run_proposal`, `input/proposal/proposal_input.txt` |

## Environment variables

### OpenRouter

| Variable | Required | Default / Behavior | Source |
| --- | --- | --- | --- |
| `OPENROUTER_API_KEY` | Yes (OpenRouter) | Required for OpenRouter; optional for local endpoints. | `openrouter_llm.py:OpenRouterLLM.__init__` |
| `OPENROUTER_HTTP_REFERER` | No | Optional header for OpenRouter requests. | `openrouter_llm.py:OpenRouterLLM.__init__` |
| `OPENROUTER_X_TITLE` | No | Optional header for OpenRouter requests. | `openrouter_llm.py:OpenRouterLLM.__init__` |
| `OPENROUTER_INPUT_COST_PER_M` | No | Used for cost estimation if set. | `openrouter_llm.py:OpenRouterLLM.__init__`, `main.py` |
| `OPENROUTER_OUTPUT_COST_PER_M` | No | Used for cost estimation if set. | `openrouter_llm.py:OpenRouterLLM.__init__`, `main.py` |
| `OPENROUTER_MAX_RETRIES` | No | Default `3` retries for transient errors. | `openrouter_llm.py:_chat_once` |
| `OPENAI_API_KEY` | No | Optional fallback API key for OpenAI-compatible servers. | `openrouter_llm.py:OpenRouterLLM.__init__` |

### AutoGenBook general

| Variable | Required | Default / Behavior | Source |
| --- | --- | --- | --- |
| `AUTOGENBOOK_LLM_BASE_URL` | No | Override OpenAI-compatible base URL for all LLM calls. | `openrouter_llm.py:OpenRouterLLM.__init__` |
| `AUTOGENBOOK_LLM_API_KEY` | No | Optional API key override for non-OpenRouter endpoints. | `openrouter_llm.py:OpenRouterLLM.__init__` |
| `AUTOGENBOOK_FORCE_MINI_MODEL` | No | Forces `openai/gpt-5-mini` for all runs. | `openrouter_llm.py:OpenRouterLLM.__init__` |
| `AUTOGENBOOK_FAIL_FAST_SCHEMA` | No | Fail immediately on schema validation errors. | `main.py:main`, `autogenbook/agents/base.py:BaseAgent._validate_with_repair` |
| `AUTOGENBOOK_NONINTERACTIVE` | No | Skips interactive prompts in book/proposal flows. | `autogenbook/pipelines/book_pipeline.py:_ask_choice`, `autogenbook/pipelines/proposal_pipeline.py:_is_noninteractive` |
| `AUTOGENBOOK_ASSUME_YES` | No | Auto-accepts yes/no prompts in book flow. | `autogenbook/pipelines/book_pipeline.py:_ask_yes_no` |
| `AUTOGENBOOK_SMOKE_FAST` | No | Fast smoke mode (skips full LLM runs). | `autogenbook/smoke_test.py:main` |

### Knowledge base (KB)

| Variable | Required | Default / Behavior | Source |
| --- | --- | --- | --- |
| `AUTOGENBOOK_KB_OCR` | No | Enable OCR for PDFs if true. | `rag_kb.py:KnowledgeBase.build_from_directory` |
| `AUTOGENBOOK_KB_OCR_LANG` | No | OCR language (default `eng`). | `rag_kb.py:KnowledgeBase.build_from_directory` |
| `AUTOGENBOOK_KB_HEADING_CHUNKS` | No | Chunk Markdown by headings if true. | `rag_kb.py:KnowledgeBase.build_from_directory` |

### MCP retrieval cache

| Variable | Required | Default / Behavior | Source |
| --- | --- | --- | --- |
| `AUTOGENBOOK_MCP_CACHE_DIR` | No | Cache directory for MCP tool results. | `autogenbook/retrieval/mcp_papers.py:MCPPaperRetriever.__post_init__` |
| `AUTOGENBOOK_MCP_CACHE_TTL_S` | No | Cache TTL (seconds). | `autogenbook/retrieval/mcp_papers.py:MCPPaperRetriever.__post_init__` |
| `AUTOGENBOOK_MCP_CACHE_MAX_FILES` | No | Cache size limit. | `autogenbook/retrieval/mcp_papers.py:MCPPaperRetriever.__post_init__` |

### Proposal-specific

| Variable | Required | Default / Behavior | Source |
| --- | --- | --- | --- |
| `AUTOGENBOOK_PROPOSAL_LLM1_MODEL` .. `AUTOGENBOOK_PROPOSAL_LLM5_MODEL` | No | Model overrides for proposal pipeline roles. | `autogenbook/pipelines/proposal_pipeline.py:_resolve_model_override` |
| `AUTOGENBOOK_PROPOSAL_LLM1_BASE_URL` .. `AUTOGENBOOK_PROPOSAL_LLM5_BASE_URL` | No | Per-role base URL overrides. | `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `AUTOGENBOOK_PROPOSAL_PREV_SECTIONS` | No | Number of previous sections kept for context. | `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `AUTOGENBOOK_LLM1_MAX_ROUNDS` | No | Max LLM1 rounds in proposal outline loop. | `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `AUTOGENBOOK_PROPOSAL_SECTION_CHUNK_CHARS` | No | Chunk size for section prompts. | `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `AUTOGENBOOK_PROPOSAL_SECTION_MAX_CHUNKS` | No | Max chunks per section prompt. | `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `AUTOGENBOOK_PROPOSAL_REVIEW_CHUNK_CHARS` | No | Chunk size for final review prompts. | `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `AUTOGENBOOK_PROPOSAL_FINAL_REVIEW_REPAIRS` | No | Final review repair attempts. | `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |

### Reviewer-specific

| Variable | Required | Default / Behavior | Source |
| --- | --- | --- | --- |
| `AUTOGENBOOK_REVIEWER_LLM1_MODEL` | No | Reviewer LLM1 model override. | `autogenbook/pipelines/reviewer_pipeline.py:_resolve_model_override` |
| `AUTOGENBOOK_REVIEWER_LLM2_MODEL` | No | Reviewer LLM2 model override. | `autogenbook/pipelines/reviewer_pipeline.py:_resolve_model_override` |
| `AUTOGENBOOK_REVIEWER_LLM1_BASE_URL` | No | Reviewer LLM1 base URL override. | `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer` |
| `AUTOGENBOOK_REVIEWER_LLM2_BASE_URL` | No | Reviewer LLM2 base URL override. | `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer` |

### Tavily

| Variable | Required | Default / Behavior | Source |
| --- | --- | --- | --- |
| `TAVILY_API_KEY` | No | Enables Tavily web search. | `autogenbook/retrieval/tavily.py:search_web` |

### MCP gateway

| Variable | Required | Default / Behavior | Source |
| --- | --- | --- | --- |
| `MCP_GATEWAY_ENABLE` | No | Enable/disable MCP gateway. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_URL` | No | Base URL for the gateway. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_TRANSPORT` | No | Transport (`sse` or `streaming`). | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_RPC_PATH` | No | RPC path (default `/mcp`). | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_SSE_PATH` | No | SSE path (default `/sse`). | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_MESSAGE_PATH` | No | Message path (default `/message`). | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_RPC_URL` | No | Override full RPC URL. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_SSE_URL` | No | Override full SSE URL. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_MESSAGE_URL` | No | Override full message URL. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_TIMEOUT` | No | Gateway timeout seconds. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_CACHE_TTL` | No | Tool list cache TTL seconds. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_SSE_TIMEOUT` | No | SSE stream timeout. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_SSE_ENDPOINT_TIMEOUT` | No | SSE endpoint discovery timeout. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_API_KEY` | No | Gateway API key. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_API_HEADER` | No | Header name for API key. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_API_PREFIX` | No | Prefix for API key header. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_PROMPT_ECHO` | No | Echo API key prompt. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_PROTOCOL_VERSION_SSE` | No | MCP protocol version for SSE. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_PROTOCOL_VERSION_HTTP` | No | MCP protocol version for streamable HTTP. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_CLIENT_NAME` | No | MCP client name. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_CLIENT_VERSION` | No | MCP client version. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_CLIENT_CAPABILITIES_JSON` | No | JSON for MCP client capabilities. | `mcp_gateway.py:MCPGatewayClient._client_capabilities` |

### Python runtime

| Variable | Required | Default / Behavior | Source |
| --- | --- | --- | --- |
| `PYTHONUTF8` | No | Set to `1` at startup to enforce UTF-8. | `main.py` |
| `PYTHONIOENCODING` | No | Set in smoke tests for UTF-8 output. | `autogenbook/smoke_test.py:_run` |

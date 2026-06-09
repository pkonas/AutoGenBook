# Troubleshooting

## Symptom -> cause -> fix

| Symptom | Likely cause | Fix | Source |
| --- | --- | --- | --- |
| `OPENROUTER_API_KEY` missing error at startup | Using OpenRouter without an API key | Export `OPENROUTER_API_KEY` or set `AUTOGENBOOK_LLM_BASE_URL` to a local endpoint | `openrouter_llm.py:OpenRouterLLM.__init__` |
| `LuaLaTeX not available` error | `lualatex` binary not found | Install LuaLaTeX or pass `--no-pdf` | `book_builder.py:compile_pdf`, `main.py:parse_args` |
| Book/paper input file not found | `--input` path is wrong | Fix the path or use the sample input | `autogenbook/pipelines/book_pipeline.py:run_book`, `autogenbook/pipelines/paper_pipeline.py:run_paper` |
| Proposal mode fails with MCP tools unavailable | MCP gateway not running or missing tools | Enable MCP gateway and required paper tools, rerun with `--enable-web-rag` | `autogenbook/pipelines/proposal_pipeline.py:run_proposal`, `mcp_gateway.py:MCPGatewayClient` |
| Proposal mode fails due to missing KB1/KB2 | Required directories not provided or empty | Provide valid `--kb1-dir` and `--kb2-dir` | `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| Audit fails in strict mode (exit code 4) | Missing citations, figures, or evidence for numeric claims | Review `audit_report.json` and fix inputs/prompts or use `--audit-mode warn` | `autogenbook/audit/latex_auditor.py:audit_latex`, `autogenbook/pipelines/*:run_*` |
| Proposal mode reports missing citations after drafting | Section citations do not match allowed MCP source keys | Ensure MCP tools are available and citations are present in section outputs | `autogenbook/pipelines/proposal_pipeline.py:run_proposal`, `autogenbook/citations/markdown.py:extract_markdown_citation_keys` |
| Reviewer mode returns exit code 2 | KB2 missing or empty | Provide valid `--kb2-dir` with supported files | `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`, `tests/test_reviewer_mode.py` |
| Pandoc conversion skipped | `pandoc` not installed | Install pandoc or accept Markdown-only outputs | `autogenbook/pipelines/proposal_pipeline.py:_convert_with_pandoc`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer` |

## Debugging tips

- Check `out_dir/logs/run.log` for run-level logs. (`autogenbook/logging.py:get_logger`)
- Inspect `out_dir/agent_logs` for agent input/output payloads. (`autogenbook/agents/io_log.py:write_agent_io`)
- Review `out_dir/llm_usage.jsonl` for per-call usage and cost. (`autogenbook/llm_usage.py:log_usage`)
- Look at `structure_graph.json` to confirm the section graph and leaf nodes. (`autogenbook/graph/doc_graph.py:save_graph_json`)
- Use `AUTOGENBOOK_NONINTERACTIVE=1` to avoid blocking prompts in book/proposal flows. (`autogenbook/pipelines/book_pipeline.py:_ask_choice`, `autogenbook/pipelines/proposal_pipeline.py:_is_noninteractive`)

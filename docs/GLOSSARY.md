# Glossary

- **Agent**: An LLM-driven component that renders prompts and validates outputs against schemas. (`autogenbook/agents/base.py:BaseAgent`)
- **RunContext**: Stores run identifiers and output paths used across pipelines. (`autogenbook/state.py:RunContext`)
- **Document graph**: A directed graph of sections with titles, summaries, and page budgets. (`book_builder.py:build_graph_from_book_json`, `autogenbook/graph/doc_graph.py`)
- **Leaf node**: A graph node with no children that is written as a section. (`autogenbook/graph/doc_graph.py:leaf_nodes_in_order`)
- **Knowledge base (KB)**: Local BM25 index over chunked documents for retrieval. (`rag_kb.py:KnowledgeBase`)
- **Chunk**: A text fragment derived from KB documents with `rid` and `cite_key`. (`rag_kb.py:Chunk`, `rag_kb.py:KnowledgeBase._ensure_chunk_ids`)
- **RID**: A stable retrieval identifier for KB, web, or run artifacts. (`rag_kb.py:_make_rid`, `autogenbook/retrieval/types.py:RetrievalItem`)
- **cite_key**: A citation key used in LaTeX or Markdown outputs. (`rag_kb.py:_make_cite_key`, `autogenbook/citations/ledger.py:CitationLedger`)
- **RAG**: Retrieval-augmented generation using KB and optional web retrieval. (`autogenbook/retrieval/manager.py:RetrievalManager`)
- **MCP gateway**: Tool gateway for paper search and retrieval. (`mcp_gateway.py:MCPGatewayClient`)
- **Audit report**: JSON report produced by LaTeX auditing. (`autogenbook/audit/report.py:AuditReport`)

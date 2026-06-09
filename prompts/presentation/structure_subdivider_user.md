TASK
Subdivide the given parent slide group into subparts.

DOCUMENT CONTEXT
- document_kind: {doc_kind}  ("presentation")
- Title: {doc_title}
- Summary: {doc_summary}
- Target audience: {target_audience}

PARENT NODE
- title: {parent_title}
- summary: {parent_summary}
- allocated_slides: {n_pages}
- max_output_slides_per_leaf: {max_output_pages}

RETRIEVED EXCERPTS:
{retrieved_context}

OUTPUT CONTRACT (JSON ARRAY ONLY)
[
  {
    "title": "...",
    "summary": "...",
    "n_pages": 0.0,
    "needsSubdivision": true|false
  }
]

RULES
- Same language as document.
- Titles MUST NOT be numbered.
- n_pages is slide count; use 1.0 per slide and sum approx to {n_pages} (+/- 0.2).
- needsSubdivision=true if still too broad or n_pages > {max_output_pages}.
- Avoid redundancy: each child must cover distinct content.
- If KB suggests required topics, incorporate them.

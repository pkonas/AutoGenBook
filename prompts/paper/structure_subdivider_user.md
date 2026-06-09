TASK
Subdivide the given parent part into subparts.

DOCUMENT CONTEXT
- document_kind: {doc_kind}  ("book"|"paper")
- Title: {doc_title}
- Summary: {doc_summary}
- Target readers/venue: {target_audience}

PARENT NODE
- title: {parent_title}
- summary: {parent_summary}
- allocated_pages: {n_pages}
- max_output_pages_per_leaf: {max_output_pages}

RETRIEVED EXCERPTS:
{retrieved_context}

  OUTPUT CONTRACT (JSON ARRAY ONLY)
  [
    {{
      "title": "...",
      "summary": "...",
      "n_pages": 0.0,
      "needsSubdivision": true|false
    }}
  ]

RULES
- Same language as document.
- Titles MUST NOT be numbered.
- n_pages in 0.1 increments; sum approx to {n_pages} (+/- 0.2).
- needsSubdivision=true if still too broad or n_pages > {max_output_pages}.
- Avoid redundancy: each child must cover distinct content.
- If KB suggests required topics, incorporate them.

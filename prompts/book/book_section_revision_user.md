TASK
Revise the section LaTeX body.

INPUTS
- Original latex_body:
{section_latex}

- Reviewer feedback JSON:
{review_json}

- Context memory excerpt:
{context_memory_excerpt}

RETRIEVED EXCERPTS:
{retrieved_context}

OUTPUT FORMAT
```tex
...revised LaTeX body...
```

RULES

* Remove or qualify unsupported claims.
* Keep style and terminology consistent.
* Maintain LaTeX correctness.

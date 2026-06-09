You will receive a paper specification written by the user in plain text.
Your task: convert it into a single JSON object that contains ALL parameters needed to generate the paper.

IMPORTANT:
- Output MUST be valid JSON (no trailing comments).
- Output MUST be ONLY the JSON object (no extra text).
- Use the SAME language as the user's specification.

The JSON schema you MUST produce:

{{
  "title": string,
  "abstract": string,                 // 150-300 words
  "keywords": [string, ...],          // 3-8 items
  "target_venue": string,
  "contributions": [string, ...],     // 2-6 items
  "citation_style": "bibtex" | "footnote",
  "max_depth": integer,               // default 3
  "max_output_pages": number,         // default 1.5
  "sections": [
    {{
      "title": string,
      "summary": string,
      "n_pages": number,
      "needsSubdivision": boolean
    }}
  ]
}}

If some parameters are not explicitly specified, choose sensible defaults.
Prefer an IMRaD structure (Introduction, Related Work, Methods, Results, Discussion, Conclusion) unless the user specifies otherwise.

Knowledge Base Excerpts (highest priority, may be empty):
{kb_context}

User paper specification (plain text):
{txt_spec}

You will receive a book specification written by the user in plain text.
Your task: convert it into a single JSON object that contains ALL parameters needed to generate the book.

IMPORTANT:
- Output MUST be valid JSON (no trailing comments).
- Output MUST be ONLY the JSON object (no extra text).
- Use the SAME language as the user's specification.
- Do NOT number chapter titles (no "Chapter 1: ...").
- If the user already provided an explicit outline with headings/subheadings, preserve that structure instead of collapsing it into broader generic chapters.

If some parameters are not explicitly specified, choose sensible defaults.

The JSON schema you MUST produce:

{{
  "title": string,                    // book title
  "summary": string,                  // 5-10 detailed sentences
  "n_pages": number,                  // total approximate pages (float or int)
  "target_readers": string,           // may be empty
  "equation_frequency_level": integer,// 1..5
  "do_consider_outline": boolean,
  "do_consider_previous_sections": boolean,
  "additional_requirements": string,  // may be empty
  "max_depth": integer,               // default 5
  "max_output_pages": number,         // default 1.5
  "childs": [                         // chapters
    {{
      "title": string,
      "summary": string,
      "n_pages": number,              // allocate pages; sum approx equals book n_pages (+/-0.5)
      "needsSubdivision": boolean,
      "childs": [                     // optional recursive subparts when the input already defines them
        {{
          "title": string,
          "summary": string,
          "n_pages": number,
          "needsSubdivision": boolean
        }}
      ]
    }}
  ]
}}

Preserve explicit structure:
- When the input contains headings such as chapters/subchapters, keep the same hierarchy and ordering.
- Keep page allocations from the input whenever they are explicitly provided.
- Do not merge several explicitly listed subchapters into one broader synthetic section.

You also have access to an OPTIONAL Knowledge Base excerpt block.
If provided, treat it as highest-priority reference material to shape the outline and summaries.

Knowledge Base Excerpts (highest priority, may be empty):
{kb_context}

User book specification (plain text):
{txt_spec}

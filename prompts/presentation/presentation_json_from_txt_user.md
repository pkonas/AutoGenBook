You will receive a presentation specification written by the user in plain text.
Your task: convert it into a single JSON object that contains ALL parameters needed to generate the presentation.

IMPORTANT:
- Output MUST be valid JSON (no trailing comments).
- Output MUST be ONLY the JSON object (no extra text).
- Use the SAME language as the user's specification.
- Do NOT number slide titles (no "Slide 1: ...").

If some parameters are not explicitly specified, choose sensible defaults.

The JSON schema you MUST produce:

{
  "title": string,                    // presentation title
  "summary": string,                  // 3-6 sentences
  "audience": string,                 // intended audience (may be empty)
  "duration_minutes": number,         // approximate talk duration
  "style_guidance": string,           // tone/visual style (may be empty)
  "theme": string,                    // beamer theme (default: "Madrid")
  "paginate": boolean,                // show slide numbers (default: false)
  "outline": boolean,                 // include outline slide (default: true)
  "author": string,                   // presenter name (may be empty)
  "header": string,                   // optional header text (may be empty)
  "footer": string,                   // optional footer text (may be empty)
  "max_depth": integer,               // default 3
  "max_output_pages": number,         // max slides per leaf (default 1.0)
  "slides": [
    {
      "title": string,
      "summary": string,
      "n_pages": number,              // number of slides (use 1.0 per slide)
      "needsSubdivision": boolean
    }
  ]
}

Notes:
- n_pages represents slide count, not paper pages.
- Sum of slide n_pages should approximately equal total slide count.
- If a slide group needs multiple slides, set n_pages > 1.0 and needsSubdivision = true.

You also have access to an OPTIONAL Knowledge Base excerpt block.
If provided, treat it as highest-priority reference material to shape the outline and summaries.

Knowledge Base Excerpts (highest priority, may be empty):
{kb_context}

User presentation specification (plain text):
{txt_spec}

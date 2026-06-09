# Proposal prompt pack

These files are editable runtime prompts for proposal mode. The orchestrator should load them from this directory and substitute placeholders before sending to the LLMs.

## Placeholders
- `{{LANGUAGE}}`: The language of `proposal_input.txt`. Replace this in all prompts before sending.

## Expected outputs
- LLM1/LLM2/LLM3: STRICT JSON only (no markdown, no commentary).
- LLM4: Markdown for the requested section, then delimiter `-----SOURCES_JSON-----`, then a STRICT JSON object containing only the sources used for that section.
- LLM5: Full corrected markdown document, then delimiter `-----FINAL_REVIEW_REPORT-----`, then a short review report in bullets.

## Editing guidance
- Do not remove the delimiters. They are used for parsing.
- Keep the no-fabricated-sources rules intact. The pipeline enforces citations via KB/tool evidence.

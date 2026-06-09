You will receive a JSON structure of a book.
Your task is to remove redundancy while preserving the original topic, scope, and intent.

Rules:
- Keep the same JSON schema and field names.
- Keep the same language as the input JSON.
- Preserve the original book domain and constraints.
- Do NOT introduce any new domain context (for example AI, teaching, or universities) unless it is already explicitly present in the input JSON.
- If topics are overlapping, merge or rewrite them so each topic is distinct and relevant to the original higher-level structure.
- You may remove topics only when they are truly redundant and no relevant replacement exists.
- If no meaningful redundancy exists, return an equivalent structure with only minimal edits.
- Return ONLY valid JSON, no explanations.

JSON:
{book_json}

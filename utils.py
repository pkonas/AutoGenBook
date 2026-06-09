
from __future__ import annotations

import json
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


def extract_first_json_object(text: str) -> Dict[str, Any]:
    """
    Extract the first JSON object found in text.
    Accepts raw JSON or JSON embedded in markdown fences.
    """
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("No JSON object found.")


def extract_first_json_array(text: str) -> List[Dict[str, Any]]:
    """
    Extract the first JSON array found in text.
    Accepts raw JSON array or JSON embedded in markdown fences.
    """
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch != "[":
            continue
        try:
            value, _ = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            return value
    raise ValueError("No JSON array found.")


def extract_tex_fence(text: str) -> str:
    m = re.search(r"```tex\s*(.*?)\s*```", text, flags=re.DOTALL)
    if not m:
        # sometimes model returns plain tex
        return text.strip()
    return m.group(1).strip()


def extract_markdown_fence(text: str) -> str:
    m = re.search(r"```(?:markdown|md)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if not m:
        return text.strip()
    return m.group(1).strip()


def build_retrieval_query_from_tex(
    tex: str,
    max_chars: int = 1200,
    max_terms: int = 18,
) -> str:
    if not tex:
        return ""
    text = tex
    text = re.sub(
        r"\\begin\{(?:lstlisting|verbatim|minted)\}.*?\\end\{(?:lstlisting|verbatim|minted)\}",
        " ",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"\\begin\{(?:equation\*?|align\*?|gather\*?|multline\*?)\}.*?\\end\{(?:equation\*?|align\*?|gather\*?|multline\*?)\}",
        " ",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"\\\[(.|\n)*?\\\]", " ", text)
    text = re.sub(r"\\\((.|\n)*?\\\)", " ", text)
    text = re.sub(r"\$[^$]*\$", " ", text)
    text = re.sub(r"\\cite[a-zA-Z]*\{[^}]*\}", " ", text)
    text = re.sub(r"\\footnote\{[^}]*\}", " ", text)
    text = re.sub(r"\\begin\{[^}]+\}", " ", text)
    text = re.sub(r"\\end\{[^}]+\}", " ", text)
    text = re.sub(r"\\[A-Za-z@]+\\*?(?:\\[[^\\]]*\\])?", " ", text)
    text = text.replace("{", " ").replace("}", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text)
    if not tokens:
        return text[-max_chars:] if max_chars > 0 else text
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "over",
        "under",
        "between",
        "about",
        "using",
        "used",
        "use",
        "our",
        "their",
        "they",
        "them",
        "these",
        "those",
        "will",
        "can",
        "may",
        "might",
        "should",
        "could",
        "also",
        "such",
        "more",
        "most",
        "less",
        "many",
        "some",
        "each",
        "other",
        "we",
        "are",
        "is",
        "was",
        "were",
        "be",
        "been",
        "being",
        "as",
        "at",
        "by",
        "in",
        "of",
        "on",
        "to",
        "a",
        "an",
    }
    meta: Dict[str, Tuple[int, int]] = {}
    for idx, raw in enumerate(tokens):
        token = raw.lower()
        if token in stopwords:
            continue
        freq, first = meta.get(token, (0, idx))
        meta[token] = (freq + 1, first)
    if not meta:
        return text[-max_chars:] if max_chars > 0 else text
    ranked = sorted(meta.items(), key=lambda kv: (-kv[1][0], kv[1][1]))
    chosen = [token for token, _ in ranked[:max_terms]]
    query = " ".join(chosen)
    if max_chars > 0 and len(query) > max_chars:
        return query[:max_chars]
    return query


def safe_filename(name: str) -> str:
    # Replace spaces and forbidden characters for filenames
    name = name.strip()
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"[^A-Za-z0-9_.\-À-ž]+", "_", name)
    return name or "book"


def ensure_robustness_preamble(out_dir: Path) -> None:
    src = Path(__file__).resolve().parent / "preamble" / "robustness.tex"
    if not src.exists():
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / "robustness.tex"
    try:
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        # Best-effort copy; build should continue even if this fails.
        pass


def parse_node_key(node_key: str) -> List[int]:
    # "1-2-10" -> [1,2,10]
    if node_key == "book":
        return []
    return [int(x) for x in node_key.split("-") if x.isdigit()]


def sort_node_keys(keys: Sequence[str]) -> List[str]:
    return sorted(keys, key=lambda k: parse_node_key(k))


def get_depth(node_key: str) -> int:
    return 0 if node_key == "book" else len(node_key.split("-"))


def ensure_bool(v: Any, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        if v.lower() in {"true", "yes", "1"}:
            return True
        if v.lower() in {"false", "no", "0"}:
            return False
    return default


def ensure_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default


def ensure_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except Exception:
        return default


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def get_equation_frequency_prompt(level: int) -> str:
    level = int(level)
    if level <= 1:
        return (
            "Avoid using equations whenever possible; use them only when absolutely necessary and keep it to a minimum."
        )
    if level == 2:
        return (
            "Use equations sparingly, focusing primarily on explanations in prose. Use simple equations only if necessary."
        )
    if level == 3:
        return (
            "Combine equations and prose in a balanced way. Use equations to illustrate key concepts and prose to explain."
        )
    if level == 4:
        return (
            "Use equations actively to convey concepts precisely. Important explanations should also be supplemented with prose."
        )
    return "Use equations extensively. Express as many concepts and relationships as possible through equations."


def generate_outline_text(tree: Dict[str, Dict[str, Any]], root: str = "book") -> str:
    """
    tree: {node_key: {"title":..., "summary":..., "n_pages":..., "children":[...]}}

    Returns a compact table-of-contents-like text with summaries for prompt conditioning.
    """
    lines: List[str] = []

    def rec(node: str, prefix: str, depth: int) -> None:
        n = tree[node]
        title = n.get("title", "")
        summary = n.get("summary", "")
        pages = n.get("n_pages", "")
        indent = "  " * depth
        head = f"{prefix}{title} (pages: {pages})"
        lines.append(f"{indent}{head}")
        if summary:
            # keep summary short in outline
            s = re.sub(r"\s+", " ", summary).strip()
            if len(s) > 240:
                s = s[:240].rstrip() + "…"
            lines.append(f"{indent}  - {s}")
        for i, ch in enumerate(n.get("children", []), start=1):
            child_prefix = f"{prefix}{i}."
            rec(ch, child_prefix, depth + 1)

    rec(root, "", 0)
    return "\n".join(lines)


def clean_markdown_content(content: str) -> str:
    # Remove the part before the first heading
    if "#" in content:
        content = content.split("#", 1)[1]
        content = "#" + content

    # Remove % only when it has whitespace before or after
    content = re.sub(r"(?<=\s)%|%(?=\s)", "", content)

    # Remove % at end of line
    content = re.sub(r"%\s*$", "", content, flags=re.MULTILINE)

    # Normalize blank lines
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip() + "\n"


def normalize_markdown_paragraphs(content: str) -> str:
    """
    Join hard-wrapped prose lines into normal Markdown paragraphs while
    preserving headings, lists, blockquotes, and fenced code blocks.
    """
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: List[str] = []
    paragraph: List[str] = []
    in_code = False

    def flush_paragraph() -> None:
        if not paragraph:
            return
        text = " ".join(part.strip() for part in paragraph if part.strip())
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        if text:
            out.append(text)
        paragraph.clear()

    for line in lines:
        raw = line.rstrip()
        stripped = raw.strip()

        if stripped.startswith("```"):
            flush_paragraph()
            out.append(raw)
            in_code = not in_code
            continue
        if in_code:
            out.append(raw)
            continue
        if not stripped:
            flush_paragraph()
            if out and out[-1] != "":
                out.append("")
            continue

        is_heading = stripped.startswith("#")
        is_blockquote = stripped.startswith(">")
        is_list = bool(re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", raw))

        if is_heading or is_blockquote or is_list:
            flush_paragraph()
            out.append(raw)
            continue

        if out and re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", out[-1]) and raw.startswith("  "):
            out[-1] = out[-1].rstrip() + " " + stripped
            continue

        paragraph.append(stripped)

    flush_paragraph()
    return "\n".join(out).strip() + "\n"


def convert_lstlisting_to_markdown(content: str) -> str:
    # Convert lstlisting blocks into Markdown fenced code blocks
    def repl(match: re.Match) -> str:
        options = match.group(1) or ""
        code = match.group(2) or ""
        lang_match = re.search(r"language=([a-zA-Z]+)", options)
        lang = lang_match.group(1) if lang_match else ""
        return f"```{lang}\n{code.strip()}\n```"

    content = re.sub(
        r"\\begin\{lstlisting\}\[(.*?)\](.*?)\\end\{lstlisting\}",
        repl,
        content,
        flags=re.DOTALL,
    )
    content = re.sub(
        r"\\begin\{lstlisting\}(.*?)\\end\{lstlisting\}",
        lambda m: f"```\n{(m.group(1) or '').strip()}\n```",
        content,
        flags=re.DOTALL,
    )
    return content


def latex_math_to_katex_markdown(content: str) -> str:
    """
    Keep LaTeX math as-is; most markdown renderers with KaTeX/MathJax can handle it.
    This function currently just returns content; kept for future extensions.
    """
    return content

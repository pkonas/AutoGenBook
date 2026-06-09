from __future__ import annotations

from dataclasses import dataclass, field
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, Optional

from autogenbook.retrieval.kb_citations import kb_source_label
from autogenbook.retrieval.types import RetrievalItem


@dataclass
class BibEntry:
    cite_key: str
    entry_type: str
    fields: Dict[str, str] = field(default_factory=dict)

    @staticmethod
    def _to_ascii(value: str) -> str:
        if not value:
            return value
        # BibTeX is brittle with Unicode; normalize to ASCII to avoid failures.
        value = value.replace("\u2013", "-").replace("\u2014", "-")
        value = value.replace("\u2018", "'").replace("\u2019", "'")
        value = value.replace("\u201c", "\"").replace("\u201d", "\"")
        normalized = unicodedata.normalize("NFKD", value)
        return normalized.encode("ascii", "ignore").decode("ascii")

    @staticmethod
    def _escape_latex(value: str) -> str:
        if not value:
            return value
        value = value.replace("\r", " ").replace("\n", " ")
        replacements = {
            "\\": r"\textbackslash{}",
            "&": r"\&",
            "%": r"\%",
            "$": r"\$",
            "#": r"\#",
            "_": r"\_",
            "{": r"\{",
            "}": r"\}",
            "~": r"\textasciitilde{}",
            "^": r"\textasciicircum{}",
        }
        return "".join(replacements.get(ch, ch) for ch in value)

    @classmethod
    def _sanitize_field(cls, value: str) -> str:
        return cls._escape_latex(cls._to_ascii(value))

    def to_bibtex(self) -> str:
        ordered = [
            "title",
            "author",
            "year",
            "journal",
            "doi",
            "url",
            "howpublished",
            "note",
        ]
        lines = [f"@{self.entry_type}{{{self.cite_key},"]
        for key in ordered:
            if key in self.fields and self.fields[key]:
                value = self._sanitize_field(self.fields[key])
                lines.append(f"  {key}={{{value}}},")
        lines.append("}")
        return "\n".join(lines)


class CitationLedger:
    def __init__(self) -> None:
        self._entries: Dict[str, BibEntry] = {}

    def add_entry(self, entry: BibEntry) -> None:
        if entry.cite_key in self._entries:
            return
        self._entries[entry.cite_key] = entry

    def add_from_retrieval_item(self, item: RetrievalItem) -> str:
        cite_key = item.cite_key or _fallback_cite_key(item.rid)
        if cite_key in self._entries:
            return cite_key

        if item.kind == "kb":
            title = kb_source_label(item.source, item.loc) if item.source else "KB Source"
            note = f"RID={item.rid}; loc={item.loc}"
            entry = BibEntry(
                cite_key=cite_key,
                entry_type="misc",
                fields={
                    "title": f"Source File: {title}",
                    "howpublished": "Local Source File",
                    "note": note,
                },
            )
            self.add_entry(entry)
            return cite_key

        if item.kind == "web":
            authors = " and ".join(item.authors) if item.authors else "Unknown"
            title = item.title or "Untitled"
            note = f"RID={item.rid}"
            entry_type = "article" if item.venue or item.doi or item.year else "misc"
            fields = {
                "title": title,
                "author": authors,
                "note": note,
            }
            if item.year:
                fields["year"] = str(item.year)
            if item.venue:
                fields["journal"] = item.venue
            if item.doi:
                fields["doi"] = item.doi
            if item.url:
                fields["url"] = item.url
            if entry_type == "misc":
                fields["howpublished"] = item.source or "Tavily Search"
            entry = BibEntry(cite_key=cite_key, entry_type=entry_type, fields=fields)
            self.add_entry(entry)
            return cite_key

        title = item.title or item.source or "Run Artifact"
        note = f"RID={item.rid}; loc={item.loc}"
        entry = BibEntry(
            cite_key=cite_key,
            entry_type="misc",
            fields={
                "title": title,
                "howpublished": item.source or "Local Run",
                "note": note,
            },
        )
        self.add_entry(entry)
        return cite_key

    def add_run_artifact(self, cite_key: str, description: str, rid: Optional[str] = None) -> None:
        note = f"RID={rid}" if rid else ""
        entry = BibEntry(
            cite_key=cite_key,
            entry_type="misc",
            fields={
                "title": description,
                "howpublished": "Local Run",
                "note": note,
            },
        )
        self.add_entry(entry)

    def add_placeholder(self, cite_key: str, note: Optional[str] = None) -> None:
        if not cite_key or cite_key in self._entries:
            return
        entry = BibEntry(
            cite_key=cite_key,
            entry_type="misc",
            fields={
                "title": f"Missing citation: {cite_key}",
                "howpublished": "Unresolved citation",
                "note": note or "Auto-generated placeholder for unresolved citation.",
            },
        )
        self.add_entry(entry)

    def entries(self) -> Iterable[BibEntry]:
        return self._entries.values()

    def write_bib(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        ordered = [self._entries[k] for k in sorted(self._entries.keys())]
        content = "\n\n".join(entry.to_bibtex() for entry in ordered) + "\n"
        path.write_text(content, encoding="utf-8")
        return path


def _fallback_cite_key(rid: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in rid.lower())
    cleaned = cleaned.strip("_") or "ref"
    return cleaned

from __future__ import annotations

from pathlib import Path

from autogenbook.citations.ledger import CitationLedger
from autogenbook.retrieval.types import RetrievalItem


def main() -> int:
    out_dir = Path("out_citations_smoke")
    out_dir.mkdir(parents=True, exist_ok=True)

    ledger = CitationLedger()

    kb_item = RetrievalItem(
        rid="RID:kb:doc.pdf:page 1:1",
        kind="kb",
        source="doc.pdf",
        loc="page 1, chunk 1",
        score=12.3,
        cite_key="kb_doc_pdf_page_1_1",
        text="Example excerpt.",
        title="doc.pdf",
    )
    web_item = RetrievalItem(
        rid="RID:web:tavily:abcdef",
        kind="web",
        source="Tavily Search",
        loc="Journal 2021",
        score=1.0,
        cite_key="web_tavily_example",
        text="Abstract snippet.",
        url="https://example.org/paper",
        title="Example Paper",
        authors=["Alex Smith"],
        year=2021,
        venue="Journal of Examples",
        doi="10.1234/example",
    )

    ledger.add_from_retrieval_item(kb_item)
    ledger.add_from_retrieval_item(web_item)
    ledger.add_run_artifact("run_metrics", "Run metrics summary", rid="RID:run:exp1:metrics.json")

    bib_path = ledger.write_bib(out_dir / "refs.bib")
    if not bib_path.exists():
        raise RuntimeError("refs.bib was not created")

    print(f"Created {bib_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

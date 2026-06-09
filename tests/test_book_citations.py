import unittest
from pathlib import Path

from book_builder import _apply_iso690_citations, _apply_iso690_citations_v3


class BookCitationTests(unittest.TestCase):
    def test_apply_iso690_alias_points_to_v3(self):
        self.assertIs(_apply_iso690_citations, _apply_iso690_citations_v3)

    def test_apply_iso690_citations_normalizes_broken_book_keys(self):
        out_dir = Path("tests") / "_tmp_book_citations"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "kb_sources.json").write_text(
            """
{
  "chunks": [
    {
      "source_path": "docs/pr-08.docx",
      "loc": "chunk 8",
      "rid": "r1",
      "cite_key": "kb_pr_08_metoda_singularit_pro_tenk_profily_docx_4425a50f_chunk_8",
      "excerpt": "docx"
    },
    {
      "source_path": "docs/09.pptx",
      "loc": "slide 2 chunk 1",
      "rid": "r2",
      "cite_key": "kb_09_v_rov_vlakna_biott_sawart_pptx_19d4be5e_slide_2_1",
      "excerpt": "pptx 09"
    },
    {
      "source_path": "docs/10.pptx",
      "loc": "slide 4 chunk 1",
      "rid": "r3",
      "cite_key": "kb_10_mezn_vrstva_pptx_04246716_slide_4_1",
      "excerpt": "pptx 10"
    },
    {
      "source_path": "docs/11.pptx",
      "loc": "slide 4 chunk 1",
      "rid": "r4",
      "cite_key": "kb_11_metoda_singularit_pptx_396ad930_slide_4_1",
      "excerpt": "pptx 11"
    }
  ]
}
""".strip(),
            encoding="utf-8",
        )
        text = r"""
\begin{document}
A \cite{kb_pr_08_metoda_singularit_pro_tenk_profily_docx_4425a50f}.
B \cite{pr-08 _Metoda singularit pro tenke profily.docx_chunk_8}.
C \cite{kb_09_v_rov_vlakna_biott_sawart_pptx_19d4be5e_slide_2:1}.
D \cite{kb_10_mezn_vrstva_pptx_04246716_slide_4:1}.
E \cite{kb_pr_11_metoda_singularit_pptx_396ad930_slide_4_1}.
\end{document}
""".strip()

        rendered = _apply_iso690_citations_v3(text, kb=None, out_dir=out_dir)

        self.assertNotIn("kb_pr_08_metoda_singularit_pro_tenk_profily_docx_4425a50f", rendered)
        self.assertNotIn("pr-08 _Metoda singularit pro tenke profily.docx_chunk_8", rendered)
        self.assertNotIn("kb_09_v_rov_vlakna_biott_sawart_pptx_19d4be5e_slide_2:1", rendered)
        self.assertNotIn("kb_10_mezn_vrstva_pptx_04246716_slide_4:1", rendered)
        self.assertNotIn("kb_pr_11_metoda_singularit_pptx_396ad930_slide_4_1", rendered)
        self.assertIn("[1]", rendered)
        self.assertIn("[2]", rendered)
        self.assertIn("[3]", rendered)
        self.assertIn("[4]", rendered)

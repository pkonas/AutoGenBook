import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import networkx as nx

from autogenbook.agents.base import AgentContext
from autogenbook.agents.book_section_writer import BookSectionWriterAgent
from autogenbook.prompts.book_loader import load_book_prompts
from autogenbook.prompts.registry import set_prompt_registry
from book_builder import AppConfig, build_markdown_document
from utils import normalize_markdown_paragraphs


class DummyLLM:
    def __init__(self, response: str):
        self.response = response
        self.config = SimpleNamespace(model="dummy-model", temperature=0.0)

    def chat(self, messages, allow_tools=False):
        return self.response

    def get_last_usage(self):
        return {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    def get_last_cost_usd(self):
        return None

    def get_total_cost_usd(self):
        return None

    def get_total_tokens(self):
        return 2


class BookMarkdownQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        set_prompt_registry("book", load_book_prompts(content_format="markdown"))

    def test_normalize_markdown_paragraphs_unwraps_prose_and_preserves_list_items(self):
        raw = (
            "Scaled-resolving simulations are hybrid formulations that retain\n"
            "RANS modelling near the wall and resolve larger structures.\n\n"
            "- First bullet line\n"
            "  continuation of the same bullet\n"
            "- Second bullet\n"
        )
        normalized = normalize_markdown_paragraphs(raw)
        self.assertIn(
            "Scaled-resolving simulations are hybrid formulations that retain RANS modelling near the wall and resolve larger structures.",
            normalized,
        )
        self.assertIn("- First bullet line continuation of the same bullet", normalized)
        self.assertIn("- Second bullet", normalized)

    def test_book_section_writer_uses_markdown_extraction_and_cleanup(self):
        llm = DummyLLM(
            "```md\n"
            "First paragraph line one\n"
            "continues on line two.\n\n"
            "- Bullet item\n"
            "  continued detail\n"
            "```"
        )
        agent = BookSectionWriterAgent(llm)
        with tempfile.TemporaryDirectory() as tmp:
            ctx = AgentContext(run_id="test", out_dir=Path(tmp), llm=llm, mode="book")
            body = agent.run(
                {
                    "book_title": "Book",
                    "book_summary": "Summary",
                    "target_readers": "Researchers",
                    "additional_requirements": "(none)",
                    "equation_frequency": "Use equations sparingly.",
                    "toc_and_summary": "(outline)",
                    "previous_sections": "(none)",
                    "context_memory_excerpt": "(none)",
                    "retrieved_context": "(none)",
                    "node_key": "1",
                    "section_title": "Introduction",
                    "section_summary": "Intro",
                    "n_pages": 1.0,
                    "section_draft": "",
                },
                ctx,
            )
        self.assertEqual(
            body,
            "First paragraph line one continues on line two.\n\n- Bullet item continued detail",
        )

    def test_build_markdown_document_omits_leaf_summaries_from_final_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            leaf = tmp_path / "1.md"
            leaf.write_text("Continuous final prose.", encoding="utf-8")

            g = nx.DiGraph()
            g.graph["author"] = "Author"
            g.add_node("book", title="Book", summary="", n_pages=1.0, needsSubdivision=True)
            g.add_node(
                "1",
                title="Leaf section",
                summary="This summary should not be printed in the final manuscript.",
                n_pages=1.0,
                needsSubdivision=False,
                content_file_path=str(leaf),
            )
            g.add_edge("book", "1")

            path = build_markdown_document(g, out_dir=tmp_path)
            rendered = path.read_text(encoding="utf-8")

        self.assertIn("## Leaf section", rendered)
        self.assertIn("Continuous final prose.", rendered)
        self.assertNotIn("This summary should not be printed", rendered)

    def test_book_app_config_enables_single_revision_pass_by_default(self):
        cfg = AppConfig()
        self.assertEqual(cfg.section_revision_passes, 1)


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

import book_builder
from autogenbook.prompts.book_loader import load_book_prompts
from autogenbook.prompts.registry import set_prompt_registry


class DummyLLM:
    def chat(self, messages, allow_tools=False):
        return """
        {
          "title": "Fallback title",
          "summary": "Fallback summary.",
          "n_pages": 40,
          "target_readers": "",
          "equation_frequency_level": 1,
          "do_consider_outline": true,
          "do_consider_previous_sections": true,
          "additional_requirements": "",
          "max_depth": 5,
          "max_output_pages": 1.5,
          "childs": [
            {
              "title": "Collapsed chapter",
              "summary": "This should be replaced by the explicit outline.",
              "n_pages": 40,
              "needsSubdivision": true
            }
          ]
        }
        """

    def get_last_usage(self):
        return None


class _FailingSubdivider:
    def run(self, *_args, **_kwargs):
        raise AssertionError("Explicit outline nodes should not be auto-subdivided.")


class BookOutlinePreservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        set_prompt_registry("book", load_book_prompts(content_format="markdown"))

    def test_generate_book_json_preserves_explicit_outline(self):
        txt_spec = """
Title: Numerical simulations

## Chapter 1: Introduction (4 pages)
**Content:**
- Main motivation
- Fatigue and lifespan

### 1.1 Flow regimes (2 pages)
**Content:**
- Speed-no-load
- Low-load operation

## Chapter 2: Discussion (1 page)
- Final synthesis

**OVERAL RANGE OF PAGES: ~30 pages**
"""
        result = book_builder.generate_book_json_from_txt(DummyLLM(), txt_spec, kb=None)

        self.assertEqual(result["n_pages"], 30.0)
        self.assertEqual([item["title"] for item in result["childs"]], ["Introduction", "Discussion"])
        self.assertTrue(result["childs"][0]["structure_locked"])
        self.assertIn("childs", result["childs"][0])
        self.assertEqual(result["childs"][0]["childs"][0]["title"], "Flow regimes")
        self.assertTrue(result["childs"][0]["childs"][0]["structure_locked"])
        self.assertFalse(result["childs"][1]["needsSubdivision"])

    def test_subdivide_graph_skips_locked_outline_nodes(self):
        book_json = {
            "title": "Locked outline",
            "summary": "Summary",
            "n_pages": 10,
            "target_readers": "",
            "equation_frequency_level": 1,
            "do_consider_outline": True,
            "do_consider_previous_sections": True,
            "additional_requirements": "",
            "max_depth": 5,
            "max_output_pages": 1.5,
            "childs": [
                {
                    "title": "Introduction",
                    "summary": "Explicit chapter that should remain intact.",
                    "n_pages": 4.0,
                    "needsSubdivision": False,
                    "structure_locked": True,
                },
                {
                    "title": "Methods",
                    "summary": "Parent node with explicit subchapter.",
                    "n_pages": 3.0,
                    "needsSubdivision": True,
                    "structure_locked": True,
                    "childs": [
                        {
                            "title": "Experimental setup",
                            "summary": "Explicit leaf subsection.",
                            "n_pages": 3.0,
                            "needsSubdivision": False,
                            "structure_locked": True,
                        }
                    ],
                },
            ],
        }
        graph = book_builder.build_graph_from_book_json(book_json)

        with patch.object(book_builder, "StructureSubdividerAgent", lambda: _FailingSubdivider()):
            book_builder.subdivide_graph(
                llm=object(),
                g=graph,
                kb=None,
                rag_top_k=2,
                rag_max_chars_total=1000,
            )

        self.assertEqual(set(graph.nodes), {"book", "1", "2", "2-1"})
        self.assertEqual(graph.nodes["1"]["title"], "Introduction")
        self.assertEqual(graph.nodes["2-1"]["title"], "Experimental setup")


if __name__ == "__main__":
    unittest.main()

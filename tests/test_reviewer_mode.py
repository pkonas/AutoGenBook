import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile

from autogenbook.pipelines import reviewer_pipeline


class DummyLLM:
    def __init__(self, *args, **kwargs):
        self.calls = []

    def chat(self, messages, allow_tools=False, model=None):
        self.calls.append(messages)
        system_text = messages[0].get("content", "") if messages else ""
        if "LLM1" in system_text or "architect" in system_text.lower():
            return "GENERATED_PROMPT"
        return "REVIEW_OUTPUT"


def _make_args(out_dir, kb1_dir=None, kb2_dir=None):
    return SimpleNamespace(
        out_dir=str(out_dir),
        kb1_dir=str(kb1_dir) if kb1_dir else None,
        kb2_dir=str(kb2_dir) if kb2_dir else None,
        rebuild_kb=False,
        no_pdf=True,
        no_tex=True,
    )


class ReviewerModeTests(unittest.TestCase):
    def test_missing_kb2_dir_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            args = _make_args(out_dir, kb1_dir=None, kb2_dir=None)
            code = reviewer_pipeline.run_reviewer(args, None, None)
            self.assertEqual(code, 2)

    def test_empty_kb2_dir_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            kb2_dir = Path(tmp) / "kb2"
            kb2_dir.mkdir(parents=True, exist_ok=True)
            args = _make_args(out_dir, kb1_dir=None, kb2_dir=kb2_dir)
            code = reviewer_pipeline.run_reviewer(args, None, None)
            self.assertEqual(code, 2)

    def test_missing_kb1_uses_default_prompt1(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            kb2_dir = Path(tmp) / "kb2"
            kb2_dir.mkdir(parents=True, exist_ok=True)
            (kb2_dir / "thesis.txt").write_text("test", encoding="utf-8")
            args = _make_args(out_dir, kb1_dir=None, kb2_dir=kb2_dir)

            dummy_instance = DummyLLM()
            def _dummy_ctor(*_args, **_kwargs):
                return dummy_instance

            with patch.object(reviewer_pipeline, "OpenRouterLLM", _dummy_ctor):
                code = reviewer_pipeline.run_reviewer(args, None, None)
            self.assertEqual(code, 0)
            review_path = out_dir / "review_final.md"
            self.assertTrue(review_path.exists())
            generated_path = out_dir / "reviewer_prompt1_generated.md"
            self.assertFalse(generated_path.exists())
            self.assertEqual(review_path.read_text(encoding="utf-8").strip(), "REVIEW_OUTPUT")

    def test_kb1_present_uses_generated_prompt1(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            kb2_dir = Path(tmp) / "kb2"
            kb1_dir = Path(tmp) / "kb1"
            kb2_dir.mkdir(parents=True, exist_ok=True)
            kb1_dir.mkdir(parents=True, exist_ok=True)
            (kb2_dir / "thesis.txt").write_text("test", encoding="utf-8")
            (kb1_dir / "norms.txt").write_text("norms", encoding="utf-8")
            args = _make_args(out_dir, kb1_dir=kb1_dir, kb2_dir=kb2_dir)

            dummy_instance = DummyLLM()
            def _dummy_ctor(*_args, **_kwargs):
                return dummy_instance

            with patch.object(reviewer_pipeline, "OpenRouterLLM", _dummy_ctor):
                code = reviewer_pipeline.run_reviewer(args, None, None)
            self.assertEqual(code, 0)

            generated_path = out_dir / "reviewer_prompt1_generated.md"
            self.assertTrue(generated_path.exists())
            self.assertEqual(generated_path.read_text(encoding="utf-8").strip(), "GENERATED_PROMPT")

            # LLM2 call should use the generated prompt as system prompt
            self.assertGreaterEqual(len(dummy_instance.calls), 2)
            llm2_system = dummy_instance.calls[-1][0]["content"]
            self.assertEqual(llm2_system.strip(), "GENERATED_PROMPT")
            review_path = out_dir / "review_final.md"
            self.assertTrue(review_path.exists())
            self.assertEqual(review_path.read_text(encoding="utf-8").strip(), "REVIEW_OUTPUT")


if __name__ == "__main__":
    unittest.main()

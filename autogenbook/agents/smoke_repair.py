from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from autogenbook.agents.base import AgentContext, BaseAgent
from autogenbook.schemas.idea import IdeaAgentOutput


class DummyLLM:
    def __init__(self) -> None:
        self.calls = 0
        self.config = SimpleNamespace(model="dummy", temperature=0.0)
        self._last_usage = {}

    def chat_json_object(self, messages, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return {"ideas": []}
        return {
            "ideas": [
                {
                    "idea_id": "I1",
                    "title": "Toy",
                    "one_liner": "Line",
                    "hypothesis": "Hypothesis",
                    "core_mechanism": "Mechanism",
                    "what_is_new": "Novelty",
                    "why_it_might_work": "Rationale",
                    "minimal_experiment": {
                        "design": "Design",
                        "expected_signal": "Signal",
                        "metric": "accuracy",
                        "success_criteria": "Improve",
                    },
                    "ablations": ["A1"],
                    "risks": ["R1"],
                    "safety_ethics": ["S1"],
                    "estimated_effort": {"time_minutes": 10, "complexity": "low"},
                    "retrieval_queries": ["query"],
                }
            ],
            "selection_rubric": {
                "novelty_weight": 0.25,
                "testability_weight": 0.25,
                "impact_weight": 0.25,
                "risk_weight": 0.25,
            },
        }

    def get_last_usage(self):
        return dict(self._last_usage)


def main() -> None:
    llm = DummyLLM()
    agent = BaseAgent(
        name="idea_test",
        system_prompt_template="test",
        user_prompt_template="test",
        output_type="object",
        llm=llm,
        output_model=IdeaAgentOutput,
        max_repair_attempts=1,
    )
    ctx = AgentContext(run_id="smoke", out_dir=Path.cwd())
    _ = agent.run({}, ctx)
    print("ok")


if __name__ == "__main__":
    main()

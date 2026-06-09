from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from autogenbook.agents import AgentContext, IdeaAgent, PlanAgent
from autogenbook.prompts.scientist_loader import load_scientist_prompts
from autogenbook.prompts.registry import set_prompt_registry
from autogenbook.logging import get_logger
from openrouter_llm import OpenRouterLLM


def main() -> int:
    out_dir = Path("out_smoke_agents").resolve()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    logger = get_logger("autogenbook.smoke_agents", out_dir=out_dir)

    llm = OpenRouterLLM()
    prompts = load_scientist_prompts()
    set_prompt_registry("scientist", prompts)
    ctx = AgentContext(run_id=run_id, out_dir=out_dir, logger=logger, llm=llm, mode="scientist")

    idea_agent = IdeaAgent()
    plan_agent = PlanAgent()

    idea_input = {
        "n_ideas": 3,
        "template_name": "toy_classification",
        "user_goal": "AI-assisted teaching workflows at universities",
        "compute_budget": "CPU-only, <5 minutes per run",
        "max_iters": 1,
        "allowed_libraries": ["scikit-learn", "numpy", "pandas"],
        "forbidden_actions": ["network", "filesystem outside workdir"],
        "metric_to_optimize": "accuracy",
        "baseline_desc": "",
    }
    ideas = idea_agent.run(idea_input, ctx)

    plan_input = {
        "ideas_json": json.dumps(ideas, ensure_ascii=False, indent=2),
        "literature_json": "{}",
        "compute_budget": "CPU-only, <5 minutes per run",
        "max_iters": 1,
        "metric_to_optimize": "accuracy",
        "template_name": "toy_classification",
    }
    plan = plan_agent.run(plan_input, ctx)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "smoke_result.json").write_text(
        json.dumps({"ideas": ideas, "plan": plan}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Smoke agents completed. Output: %s", out_dir / "smoke_result.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

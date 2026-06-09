from __future__ import annotations

import json
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Type

from autogenbook.prompts.agent_prompts import render
from autogenbook.prompts.registry import get_prompt_optional
from autogenbook.retrieval.manager import RetrievalManager
from autogenbook.schemas.base import StrictBaseModel
from autogenbook.schemas.validate import (
    format_validation_error,
    json_schema_snippet,
    validate_or_raise,
)
from autogenbook.llm_usage import log_usage
from pydantic import ValidationError
from openrouter_llm import OpenRouterLLM
from utils import extract_first_json_array, extract_first_json_object

from .io_log import write_agent_io


UsageHook = Callable[[Dict[str, int]], None]


@dataclass
class AgentContext:
    run_id: str
    out_dir: Path
    kb: Optional[Any] = None
    logger: Optional[Any] = None
    llm: Optional[OpenRouterLLM] = None
    retrieval_manager: Optional[RetrievalManager] = None
    mode: str = "book"
    model_map: Dict[str, str] = field(default_factory=dict)
    temperature_map: Dict[str, float] = field(default_factory=dict)
    on_usage: Optional[UsageHook] = None
    token_total: int = 0
    cost_total_usd: float = 0.0
    fail_fast_schema: bool = False

    def record_usage(
        self,
        usage: Dict[str, int],
        *,
        llm: Optional[OpenRouterLLM] = None,
        label: Optional[str] = None,
    ) -> None:
        total = int(usage.get("total_tokens", 0))
        self.token_total += total
        llm = llm or self.llm
        if isinstance(llm, OpenRouterLLM):
            cost = llm.estimate_cost_usd(usage)
            if cost is not None:
                self.cost_total_usd += float(cost)
            line = llm.format_usage_line(usage, label=label or "LLM")
            try:
                log_usage(self.out_dir, label or "LLM", llm, usage, {"mode": self.mode})
            except Exception:
                pass
        elif llm is not None and hasattr(llm, "get_total_tokens"):
            line = (
                f"[{label or 'LLM'}] Tokens: prompt={usage.get('prompt_tokens', 0)}, "
                f"completion={usage.get('completion_tokens', 0)}, total={usage.get('total_tokens', 0)}, "
                f"cumulative={getattr(llm, 'get_total_tokens')()} | Cost n/a (total n/a)"
            )
        else:
            line = (
                f"[{label or 'LLM'}] Tokens: prompt={usage.get('prompt_tokens', 0)}, "
                f"completion={usage.get('completion_tokens', 0)}, total={usage.get('total_tokens', 0)}, "
                f"cumulative={self.token_total} | Cost n/a (total n/a)"
            )
        if self.logger is not None:
            try:
                self.logger.info(line)
            except Exception:
                print(line)
        else:
            print(line)
        if self.on_usage is not None:
            self.on_usage(usage)


class BaseAgent:
    def __init__(
        self,
        name: str,
        system_prompt_template: str,
        user_prompt_template: str,
        output_type: str = "object",
        llm: Optional[OpenRouterLLM] = None,
        output_model: Optional[Type[StrictBaseModel]] = None,
        max_repair_attempts: int = 2,
    ) -> None:
        self.name = name
        self.system_prompt_template = system_prompt_template
        self.user_prompt_template = user_prompt_template
        self.output_type = output_type
        self.llm = llm
        self.output_model = output_model
        self.max_repair_attempts = max(0, int(max_repair_attempts))

    def run(self, input: Dict[str, Any], context: AgentContext) -> Any:
        llm = context.llm or self.llm
        if llm is None:
            raise RuntimeError(f"Agent '{self.name}' requires an LLM in AgentContext or agent constructor.")

        retrieved_context = input.get("retrieved_context")
        if retrieved_context is None:
            retrieved_context = ""
            if context.retrieval_manager is not None:
                query = _build_query(input)
                items = context.retrieval_manager.retrieve(
                    query, k=context.retrieval_manager.default_k, allow_web=False
                )
                retrieved_context = context.retrieval_manager.format_context(items)
        if not retrieved_context:
            retrieved_context = "(none)"

        prompt_args = dict(input)
        prompt_args.setdefault("retrieved_context", retrieved_context)
        system_prompt = render(self.system_prompt_template, **prompt_args)
        user_prompt = render(self.user_prompt_template, **prompt_args)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        model = context.model_map.get(self.name) if context.model_map else None
        temperature = context.temperature_map.get(self.name) if context.temperature_map else None

        if self.output_type == "array":
            output = self._chat_json_with_repair(
                llm,
                messages,
                expect="array",
                model=model,
                temperature=temperature,
            )
        elif self.output_type == "text":
            output = llm.chat(messages, model=model, temperature=temperature, allow_tools=False)
        else:
            output = self._chat_json_with_repair(
                llm,
                messages,
                expect="object",
                model=model,
                temperature=temperature,
            )

        if self.output_model is not None and self.output_type != "text":
            output = self._validate_with_repair(
                output,
                llm,
                model=model,
                temperature=temperature,
                original_messages=messages,
                input_payload=input,
                context=context,
            )

        usage = llm.get_last_usage()
        context.record_usage(usage, llm=llm, label=self.name)
        write_agent_io(
            context.out_dir,
            self.name,
            input,
            output,
            {
                "run_id": context.run_id,
                "usage": usage,
                "cost_usd": getattr(llm, "get_last_cost_usd", lambda: None)(),
                "total_cost_usd": getattr(llm, "get_total_cost_usd", lambda: None)(),
                "model": model or llm.config.model,
                "temperature": temperature if temperature is not None else llm.config.temperature,
            },
        )
        return output

    def _validate_with_repair(
        self,
        output: Any,
        llm: OpenRouterLLM,
        *,
        model: Optional[str],
        temperature: Optional[float],
        original_messages: list[dict[str, str]],
        input_payload: Dict[str, Any],
        context: AgentContext,
    ) -> Any:
        assert self.output_model is not None
        attempts = 0
        current = output
        while True:
            try:
                validated = self._validate_output(current)
                return validated
            except Exception as exc:
                if attempts >= self.max_repair_attempts:
                    if isinstance(exc, ValidationError):
                        summary = format_validation_error(exc)
                        env_fail_fast = os.environ.get("AUTOGENBOOK_FAIL_FAST_SCHEMA") == "1"
                        if context.fail_fast_schema or env_fail_fast:
                            raise ValueError(
                                f"Schema validation failed for {self.name}:\n{summary}"
                            ) from exc
                        raise ValueError(
                            "Schema validation failed for {name} after repairs.\n{summary}\n"
                            "Hint: reduce prompt complexity, increase retrieval context, or inspect agent_logs "
                            "for the failing output.".format(name=self.name, summary=summary)
                        ) from exc
                    raise
                attempts += 1
                summary = ""
                if isinstance(exc, ValidationError):
                    summary = format_validation_error(exc)
                current = self._attempt_repair(
                    llm,
                    current,
                    summary,
                    model=model,
                    temperature=temperature,
                )
                write_agent_io(
                    context.out_dir,
                    self.name,
                    {
                        "repair_attempt": attempts,
                        "error_summary": summary,
                        "input_snapshot": input_payload,
                    },
                    current,
                    {
                        "run_id": context.run_id,
                        "model": model or llm.config.model,
                        "temperature": temperature if temperature is not None else llm.config.temperature,
                        "repair_attempt": attempts,
                        "error_summary": summary,
                    },
                )

    def _validate_output(self, output: Any) -> Any:
        assert self.output_model is not None
        if self.output_type == "array" and isinstance(output, list):
            model_fields = getattr(self.output_model, "model_fields", {})
            if "items" in model_fields:
                model_instance = validate_or_raise(self.output_model, {"items": output})
                dumped = model_instance.model_dump()
                return dumped.get("items", output)
        model_instance = validate_or_raise(self.output_model, output)
        return model_instance.model_dump()

    def _attempt_repair(
        self,
        llm: OpenRouterLLM,
        output: Any,
        error_summary: str,
        *,
        model: Optional[str],
        temperature: Optional[float],
    ) -> Any:
        assert self.output_model is not None
        original_json = json.dumps(output, ensure_ascii=False, indent=2)
        schema = json_schema_snippet(self.output_model, max_chars=2000)
        system_prompt = (
            get_prompt_optional("json_repair_system")
            or "You are a careful JSON repair agent. Output only corrected JSON."
        )
        user_prompt = (
            "Original JSON:\n"
            f"{original_json}\n\n"
            "Validation errors:\n"
            f"{error_summary}\n\n"
            "JSON schema (truncated):\n"
            f"{schema}"
        )
        repair_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        if self.output_type == "array":
            return llm.chat_json_array(repair_messages, model=model, temperature=temperature)
        return llm.chat_json_object(repair_messages, model=model, temperature=temperature)

    def _chat_json_with_repair(
        self,
        llm: OpenRouterLLM,
        messages: list[dict[str, str]],
        *,
        expect: str,
        model: Optional[str],
        temperature: Optional[float],
        max_attempts: int = 3,
    ) -> Any:
        parser = extract_first_json_array if expect == "array" else extract_first_json_object
        last_err: Optional[Exception] = None
        attempt_msgs = list(messages)
        for _ in range(max_attempts):
            content = llm.chat(
                attempt_msgs,
                model=model,
                temperature=temperature,
                allow_tools=False,
            )
            try:
                return parser(content)
            except Exception as exc:
                last_err = exc
                schema = ""
                if self.output_model is not None:
                    schema = json_schema_snippet(self.output_model, max_chars=2000)
                system_prompt = (
                    get_prompt_optional("json_repair_system")
                    or "You are a careful JSON repair agent. Output only corrected JSON."
                )
                target = "a JSON array" if expect == "array" else "a JSON object"
                user_prompt = (
                    f"Convert the following content into valid {target} only.\n"
                    "Do not include any commentary or markdown.\n"
                )
                if schema:
                    user_prompt += f"\nJSON schema (truncated):\n{schema}\n"
                user_prompt += f"\nContent:\n{content}"
                attempt_msgs = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]
        raise ValueError(f"Failed to parse JSON {expect} after {max_attempts} attempts.") from last_err


def _build_query(input_data: Dict[str, Any]) -> str:
    if "query" in input_data and isinstance(input_data["query"], str):
        return input_data["query"]
    if "topic" in input_data and isinstance(input_data["topic"], str):
        return input_data["topic"]
    return json.dumps(input_data, ensure_ascii=False)

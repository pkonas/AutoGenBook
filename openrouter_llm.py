
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openai import OpenAI

from mcp_gateway import get_mcp_gateway
from autogenbook.prompts.registry import get_prompt_optional
from autogenbook.openrouter_pricing import get_model_token_pricing
from autogenbook.openrouter_usage import (
    extract_cost_breakdown,
    extract_usage_breakdown,
    merge_cost_totals,
    merge_usage_totals,
)


DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE_URL)
MCP_TOOL_SYSTEM_PROMPT = (
    "You can call MCP tools via the tool interface when external data is needed "
    "(web search, arXiv, paper search). Prefer paper tools such as search_papers, "
    "list_papers, read_paper, download_paper, and the paper-search suite "
    "(search_arxiv, search_semantic, search_pubmed, search_crossref, search_google_scholar, etc.). "
    "If tool use is required, call tools and then respond in the requested format."
)
JSON_ONLY_SYSTEM_PROMPT = "Return ONLY valid JSON."

_TRANSIENT_ERROR_TOKENS = (
    "rate limit",
    "timeout",
    "temporarily",
    "connection error",
    "internal server error",
    "server error",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "overloaded",
    "429",
    "500",
    "502",
    "503",
    "504",
)


def _is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, json.JSONDecodeError):
        return True
    name = exc.__class__.__name__.lower()
    if "ratelimit" in name or "timeout" in name or "connection" in name or "internalserver" in name:
        return True
    message = str(exc).lower()
    return any(token in message for token in _TRANSIENT_ERROR_TOKENS)


def _looks_like_openrouter(base_url: str) -> bool:
    return "openrouter.ai" in base_url.lower()


def _backoff_seconds(attempt: int, base: float = 0.6, cap: float = 8.0) -> float:
    delay = min(cap, base * (2 ** attempt))
    jitter = 0.2 * delay
    return delay + (jitter * (0.5 - (time.time() % 1)))


def _json_only_prompt() -> str:
    return get_prompt_optional("json_only_system") or JSON_ONLY_SYSTEM_PROMPT


@dataclass(frozen=True)
class LLMConfig:
    model: str = "openai/gpt-5-mini"
    temperature: float = 0.2
    max_tokens: Optional[int] = None
    input_cost_per_million: Optional[float] = None
    output_cost_per_million: Optional[float] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None


class OpenRouterLLM:
    """
    Minimal OpenAI-compatible chat client.

    - API key is read from OPENROUTER_API_KEY (or AUTOGENBOOK_LLM_API_KEY / OPENAI_API_KEY).
    - Base URL defaults to OpenRouter but can be overridden via AUTOGENBOOK_LLM_BASE_URL.
    """

    def __init__(self, config: Optional[LLMConfig] = None) -> None:
        self.config = config or LLMConfig()
        if os.environ.get("AUTOGENBOOK_FORCE_MINI_MODEL", "").strip().lower() in {"1", "true", "yes", "on"}:
            self.config = LLMConfig(
                model="openai/gpt-5-mini",
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                input_cost_per_million=self.config.input_cost_per_million,
                output_cost_per_million=self.config.output_cost_per_million,
                base_url=self.config.base_url,
                api_key=self.config.api_key,
            )
        self.total_tokens = 0
        self.total_cost_usd: float = 0.0
        self.last_usage: Dict[str, int] = {}
        self.last_cost_usd: Optional[float] = None
        self.last_usage_breakdown: Dict[str, Any] = {}
        self.last_cost_breakdown: Dict[str, Any] = {}
        self.total_usage_breakdown: Dict[str, Any] = {}
        self.total_cost_breakdown: Dict[str, Any] = {}
        self.input_cost_per_million = self.config.input_cost_per_million
        self.output_cost_per_million = self.config.output_cost_per_million
        self._mcp_gateway = get_mcp_gateway()
        self.last_tool_results: List[Dict[str, Any]] = []
        base_url = (
            (self.config.base_url or "").strip()
            or os.environ.get("AUTOGENBOOK_LLM_BASE_URL", "").strip()
            or OPENROUTER_BASE_URL
        )
        self.base_url = base_url
        api_key = (
            (self.config.api_key or "").strip()
            or os.environ.get("OPENROUTER_API_KEY", "").strip()
            or os.environ.get("AUTOGENBOOK_LLM_API_KEY", "").strip()
            or os.environ.get("OPENAI_API_KEY", "").strip()
        )
        if not api_key:
            if _looks_like_openrouter(base_url):
                raise RuntimeError(
                    "Missing OPENROUTER_API_KEY. "
                    "Set it before running (e.g. export OPENROUTER_API_KEY=...)."
                )
            api_key = "local"

        # Optional OpenRouter headers (recommended but not required).
        extra_headers: Dict[str, str] = {}
        if os.environ.get("OPENROUTER_HTTP_REFERER"):
            extra_headers["HTTP-Referer"] = os.environ["OPENROUTER_HTTP_REFERER"]
        if os.environ.get("OPENROUTER_X_TITLE"):
            extra_headers["X-Title"] = os.environ["OPENROUTER_X_TITLE"]

        if self.input_cost_per_million is None and os.environ.get("OPENROUTER_INPUT_COST_PER_M"):
            self.input_cost_per_million = float(os.environ["OPENROUTER_INPUT_COST_PER_M"])
        if self.output_cost_per_million is None and os.environ.get("OPENROUTER_OUTPUT_COST_PER_M"):
            self.output_cost_per_million = float(os.environ["OPENROUTER_OUTPUT_COST_PER_M"])

        pricing_disabled = os.environ.get("OPENROUTER_PRICING_DISABLE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if (
            not pricing_disabled
            and _looks_like_openrouter(base_url)
            and (self.input_cost_per_million is None or self.output_cost_per_million is None)
        ):
            allow_fuzzy = os.environ.get("OPENROUTER_PRICING_FUZZY", "").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            timeout_s = int(os.environ.get("OPENROUTER_PRICING_TIMEOUT", "30") or 30)
            try:
                pricing = get_model_token_pricing(
                    self.config.model,
                    api_key=api_key,
                    base_url=base_url,
                    timeout_s=timeout_s,
                    allow_fuzzy=allow_fuzzy,
                )
                if self.input_cost_per_million is None:
                    self.input_cost_per_million = pricing.get("prompt_price_per_million")
                if self.output_cost_per_million is None:
                    self.output_cost_per_million = pricing.get("completion_price_per_million")
            except Exception:
                pass

        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=extra_headers or None,
        )

    def _apply_usage(self, usage: Any, *, payload: Optional[Dict[str, Any]] = None, headers=None) -> None:
        if usage is None and payload is None and headers is None:
            return
        total_tokens = getattr(usage, "total_tokens", None)
        if total_tokens is None and isinstance(usage, dict):
            total_tokens = usage.get("total_tokens")
        if total_tokens is not None:
            self.total_tokens += int(total_tokens)
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        if isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
            completion_tokens = usage.get("completion_tokens", completion_tokens)
        if prompt_tokens is not None:
            self.last_usage["prompt_tokens"] = int(prompt_tokens)
        if completion_tokens is not None:
            self.last_usage["completion_tokens"] = int(completion_tokens)
        if total_tokens is not None:
            self.last_usage["total_tokens"] = int(total_tokens)
        payload = payload or {}
        if isinstance(payload, dict) and "usage" not in payload and usage is not None:
            if isinstance(usage, dict):
                payload["usage"] = usage
            elif hasattr(usage, "model_dump"):
                try:
                    payload["usage"] = usage.model_dump()
                except Exception:
                    pass
        usage_payload = payload if isinstance(payload, dict) else {}
        cost_breakdown = extract_cost_breakdown(usage_payload, headers)
        usage_breakdown = extract_usage_breakdown(usage_payload, headers)
        if total_tokens is None:
            inferred_total = usage_breakdown.get("total_tokens")
            if inferred_total is not None:
                self.total_tokens += int(inferred_total)

        self.last_usage_breakdown = usage_breakdown
        self.last_cost_breakdown = cost_breakdown
        merge_usage_totals(self.total_usage_breakdown, usage_breakdown)
        merge_cost_totals(self.total_cost_breakdown, cost_breakdown)

        actual_cost = cost_breakdown.get("total_cost_usd")
        if actual_cost is None:
            actual_cost = self.estimate_cost_usd(self.last_usage)
        self.last_cost_usd = actual_cost
        if actual_cost is not None:
            self.total_cost_usd += float(actual_cost)

    def _get_mcp_tools(self) -> List[Dict[str, Any]]:
        if self._mcp_gateway is None or not getattr(self._mcp_gateway, "enabled", True):
            return []
        try:
            return self._mcp_gateway.get_openai_tools()
        except Exception:
            return []

    def _inject_mcp_system_prompt(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not tools:
            return list(messages)
        system_prompt = get_prompt_optional("mcp_tools_system") or MCP_TOOL_SYSTEM_PROMPT
        return [{"role": "system", "content": system_prompt}, *list(messages)]

    def _normalize_tool_calls(self, tool_calls: Any) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        if not tool_calls:
            return normalized
        for idx, call in enumerate(tool_calls):
            if isinstance(call, dict):
                norm = dict(call)
                func = norm.get("function")
                if not isinstance(func, dict):
                    name = norm.get("name")
                    args = norm.get("arguments")
                    func = {"name": name, "arguments": args}
                    norm["function"] = func
                if not norm.get("type"):
                    norm["type"] = "function"
            else:
                func = getattr(call, "function", None)
                norm = {
                    "id": getattr(call, "id", None),
                    "type": getattr(call, "type", "function"),
                    "function": {
                        "name": getattr(func, "name", None),
                        "arguments": getattr(func, "arguments", None),
                    },
                }
            if not norm.get("id"):
                norm["id"] = f"call_{int(time.time() * 1000)}_{idx}"
            func = norm.get("function")
            if not isinstance(func, dict) or not func.get("name"):
                continue
            args = func.get("arguments")
            if args is not None and not isinstance(args, str):
                try:
                    func["arguments"] = json.dumps(args, ensure_ascii=True)
                except Exception:
                    func["arguments"] = str(args)
            normalized.append(norm)
        return normalized

    def _extract_tool_calls(self, message: Any) -> List[Dict[str, Any]]:
        tool_calls = None
        if isinstance(message, dict):
            tool_calls = message.get("tool_calls")
        else:
            tool_calls = getattr(message, "tool_calls", None)
        normalized = self._normalize_tool_calls(tool_calls)
        if normalized:
            return normalized
        function_call = None
        if isinstance(message, dict):
            function_call = message.get("function_call")
        else:
            function_call = getattr(message, "function_call", None)
        if function_call:
            if isinstance(function_call, dict):
                name = function_call.get("name")
                arguments = function_call.get("arguments")
            else:
                name = getattr(function_call, "name", None)
                arguments = getattr(function_call, "arguments", None)
            if name:
                return [
                    {
                        "id": f"call_{int(time.time() * 1000)}_0",
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    }
                ]
        return []

    def _parse_tool_arguments(self, raw: Any) -> Dict[str, Any]:
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return {"_raw": raw}
        return {"_raw": str(raw)}

    def _chat_once(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str],
        temperature: Optional[float],
        max_tokens: Optional[int],
        tools: Optional[List[Dict[str, Any]]],
        tool_choice: Optional[Any],
    ) -> Any:
        kwargs: Dict[str, Any] = {
            "model": model or self.config.model,
            "messages": messages,
            "temperature": self.config.temperature if temperature is None else temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        elif self.config.max_tokens is not None:
            kwargs["max_tokens"] = self.config.max_tokens
        if tools:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice

        resp = None
        last_err: Optional[Exception] = None
        raw_headers = None
        raw_payload: Optional[Dict[str, Any]] = None
        max_retries = int(os.environ.get("OPENROUTER_MAX_RETRIES", "3") or 3)
        for attempt in range(max_retries + 1):
            try:
                if hasattr(self._client.chat.completions, "with_raw_response"):
                    raw = self._client.chat.completions.with_raw_response.create(**kwargs)
                    raw_headers = dict(getattr(raw, "headers", {}) or {})
                    resp = raw.parse()
                    try:
                        raw_response = getattr(raw, "response", None)
                        if raw_response is not None and hasattr(raw_response, "json"):
                            raw_payload = raw_response.json()
                        else:
                            raw_payload = None
                    except Exception:
                        raw_payload = None
                else:
                    resp = self._client.chat.completions.create(**kwargs)
                last_err = None
                break
            except Exception as exc:
                last_err = exc
                if attempt >= max_retries or not _is_transient_error(exc):
                    if isinstance(exc, json.JSONDecodeError):
                        raise RuntimeError(
                            "OpenRouter returned a non-JSON response. "
                            "Retry or check network/proxy/rate limits."
                        ) from exc
                    raise
                time.sleep(_backoff_seconds(attempt))
        if last_err is not None:
            raise last_err
        usage = getattr(resp, "usage", None)
        payload = raw_payload
        if resp is not None and hasattr(resp, "model_dump"):
            try:
                if payload is None:
                    payload = resp.model_dump()
            except Exception:
                if payload is None:
                    payload = None
        self._apply_usage(usage, payload=payload, headers=raw_headers)
        return resp

    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        return_usage: bool = False,
        allow_tools: Optional[bool] = None,
        tool_choice: Optional[Any] = None,
        max_tool_rounds: Optional[int] = None,
    ) -> str | tuple[str, Dict[str, int]]:
        """
        messages: [{"role":"system"|"user"|"assistant", "content": "..."}]
        """
        self.last_tool_results = []
        allow_tools = True if allow_tools is None else bool(allow_tools)
        tools = self._get_mcp_tools() if allow_tools else []
        working_messages = self._inject_mcp_system_prompt(list(messages), tools)
        max_rounds = max_tool_rounds if max_tool_rounds is not None else 3

        if not tools:
            resp = self._chat_once(
                working_messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=None,
                tool_choice=None,
            )
            content = resp.choices[0].message.content or ""
            if return_usage:
                return content, dict(self.last_usage)
            return content

        resp = None
        for _ in range(max_rounds):
            resp = self._chat_once(
                working_messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                tool_choice=tool_choice,
            )
            message = resp.choices[0].message
            tool_calls = self._extract_tool_calls(message)
            if not tool_calls:
                content = message.content or ""
                if return_usage:
                    return content, dict(self.last_usage)
                return content

            for idx, call in enumerate(tool_calls):
                if not call.get("id"):
                    call["id"] = f"call_{int(time.time() * 1000)}_{idx}"
            assistant_content = message.content or ""
            working_messages.append(
                {"role": "assistant", "content": assistant_content, "tool_calls": tool_calls}
            )

            for call in tool_calls:
                tool_id = call.get("id")
                func = call.get("function", {})
                name = func.get("name") if isinstance(func, dict) else None
                if not name:
                    tool_output = "MCP tool error: missing tool name."
                else:
                    args = self._parse_tool_arguments(func.get("arguments") if isinstance(func, dict) else None)
                    try:
                        result = self._mcp_gateway.call_tool(name, args)
                        self.last_tool_results.append(
                            {"name": name, "arguments": args, "result": result}
                        )
                        tool_output = self._mcp_gateway.format_tool_result(result)
                    except Exception as exc:
                        tool_output = f"MCP tool error: {exc}"
                working_messages.append(
                    {"role": "tool", "tool_call_id": tool_id, "content": tool_output}
                )

        content = ""
        if resp is not None:
            content = resp.choices[0].message.content or ""
        if return_usage:
            return content, dict(self.last_usage)
        return content

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        expect: str = "object",
        retries: int = 2,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        allow_tools: Optional[bool] = None,
    ) -> Dict[str, Any] | List[Any]:
        from utils import extract_first_json_array, extract_first_json_object

        last_err: Optional[Exception] = None
        for attempt in range(retries + 1):
            content = self.chat(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                allow_tools=allow_tools,
            )
            try:
                if expect == "array":
                    return extract_first_json_array(content)
                return extract_first_json_object(content)
            except Exception as exc:
                last_err = exc
                messages = [
                    {"role": "system", "content": _json_only_prompt()},
                    *list(messages),
                ]
        raise ValueError(f"Failed to parse JSON after {retries + 1} attempts.") from last_err

    def chat_json_object(
        self,
        messages: List[Dict[str, str]],
        parser=None,
        max_attempts: int = 3,
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        allow_tools: Optional[bool] = None,
    ) -> Dict[str, Any]:
        from utils import extract_first_json_object

        parser = parser or extract_first_json_object
        last_err: Optional[Exception] = None
        attempt_msgs = list(messages)
        for _ in range(max_attempts):
            content = self.chat(
                attempt_msgs,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                allow_tools=allow_tools,
            )
            try:
                return parser(content)
            except Exception as exc:
                last_err = exc
                attempt_msgs = [
                    {"role": "system", "content": _json_only_prompt()},
                    *attempt_msgs,
                ]
        raise ValueError(f"Failed to parse JSON object after {max_attempts} attempts.") from last_err

    def chat_json_array(
        self,
        messages: List[Dict[str, str]],
        parser=None,
        max_attempts: int = 3,
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        allow_tools: Optional[bool] = None,
    ) -> List[Any]:
        from utils import extract_first_json_array

        parser = parser or extract_first_json_array
        last_err: Optional[Exception] = None
        attempt_msgs = list(messages)
        for _ in range(max_attempts):
            content = self.chat(
                attempt_msgs,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                allow_tools=allow_tools,
            )
            try:
                return parser(content)
            except Exception as exc:
                last_err = exc
                attempt_msgs = [
                    {"role": "system", "content": _json_only_prompt()},
                    *attempt_msgs,
                ]
        raise ValueError(f"Failed to parse JSON array after {max_attempts} attempts.") from last_err

    def get_total_tokens(self) -> int:
        return int(self.total_tokens)

    def get_last_usage(self) -> Dict[str, int]:
        return dict(self.last_usage)

    def get_last_usage_breakdown(self) -> Dict[str, Any]:
        return dict(self.last_usage_breakdown)

    def get_total_usage_breakdown(self) -> Dict[str, Any]:
        return dict(self.total_usage_breakdown)

    def get_total_cost_usd(self) -> Optional[float]:
        if self.total_cost_usd == 0.0:
            return None
        return float(self.total_cost_usd)

    def get_last_cost_usd(self) -> Optional[float]:
        return self.last_cost_usd

    def get_last_cost_breakdown(self) -> Dict[str, Any]:
        return dict(self.last_cost_breakdown)

    def get_total_cost_breakdown(self) -> Dict[str, Any]:
        return dict(self.total_cost_breakdown)

    def estimate_cost_usd(self, usage: Optional[Dict[str, int]] = None) -> Optional[float]:
        usage = usage or self.last_usage
        if not usage:
            return None
        input_rate = self.input_cost_per_million
        output_rate = self.output_cost_per_million
        if input_rate is None or output_rate is None:
            return None
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
        return (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000.0

    def format_usage_line(self, usage: Optional[Dict[str, int]] = None, label: Optional[str] = None) -> str:
        usage = usage or self.last_usage
        prompt_tokens = int(usage.get("prompt_tokens", 0)) if usage else 0
        completion_tokens = int(usage.get("completion_tokens", 0)) if usage else 0
        total_tokens = int(usage.get("total_tokens", 0)) if usage else 0
        cost = self.last_cost_usd
        if cost is None:
            cost = self.estimate_cost_usd(usage)
        total_cost = self.get_total_cost_usd()
        cost_breakdown = self.last_cost_breakdown or {}
        in_cost = cost_breakdown.get("input_cost_usd")
        out_cost = cost_breakdown.get("output_cost_usd")

        def _fmt_cost(value: Optional[float]) -> str:
            if value is None:
                return "n/a"
            return f"${value:.6f}"

        prefix = f"[{label}] " if label else ""
        cost_detail = ""
        if in_cost is not None or out_cost is not None:
            cost_detail = f" | Cost in={_fmt_cost(in_cost)} out={_fmt_cost(out_cost)}"
        return (
            f"{prefix}Tokens: prompt={prompt_tokens}, completion={completion_tokens}, "
            f"total={total_tokens}, cumulative={self.get_total_tokens()} | "
            f"Cost {_fmt_cost(cost)} (total {_fmt_cost(total_cost)}){cost_detail}"
        )

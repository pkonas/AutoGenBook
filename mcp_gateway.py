from __future__ import annotations

import json
import os
import time
from getpass import getpass
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib import request
from urllib.error import HTTPError, URLError

ARXIV_TOOL_NAMES = {
    "download_paper",
    "list_papers",
    "read_paper",
    "search_papers",
}

PAPER_SEARCH_TOOL_NAMES = {
    "download_arxiv",
    "download_biorxiv",
    "download_crossref",
    "download_iacr",
    "download_medrxiv",
    "download_paper",
    "download_pubmed",
    "download_semantic",
    "list_papers",
    "read_arxiv_paper",
    "read_biorxiv_paper",
    "read_crossref_paper",
    "read_iacr_paper",
    "read_medrxiv_paper",
    "read_paper",
    "read_pubmed_paper",
    "read_semantic_paper",
    "search_arxiv",
    "search_biorxiv",
    "search_crossref",
    "search_google_scholar",
    "search_iacr",
    "search_medrxiv",
    "search_papers",
    "search_pubmed",
    "search_semantic",
}


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return default


@dataclass(frozen=True)
class MCPGatewayStatus:
    enabled: bool
    available: bool
    url: str
    tools: List[Dict[str, Any]]
    error: Optional[str] = None


class MCPGatewayClient:
    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        rpc_url: Optional[str] = None,
        timeout_s: Optional[float] = None,
        cache_ttl_s: Optional[float] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        # Docker's MCP Gateway supports multiple transports. The two most common for HTTP clients are:
        #   - "sse" (legacy HTTP+SSE)
        #   - "streaming" / "streamable" (Streamable HTTP)
        # We accept a few aliases here to reduce configuration friction.
        raw_transport = (os.environ.get("MCP_GATEWAY_TRANSPORT", "sse") or "sse").strip().lower()
        self.transport = {
            "http": "streaming",
            "streamable": "streaming",
            "streamable_http": "streaming",
            "streamablehttp": "streaming",
        }.get(raw_transport, raw_transport)
        self.base_url = base_url or os.environ.get("MCP_GATEWAY_URL", "http://127.0.0.1:8811")
        self.rpc_path = os.environ.get("MCP_GATEWAY_RPC_PATH", "/mcp")
        self.sse_path = os.environ.get("MCP_GATEWAY_SSE_PATH", "/sse")
        # Docker's gateway (and many MCP servers) commonly use a *session-aware* POST endpoint
        # returned via the initial SSE "endpoint" event (e.g. "/sse?sessionid=...").
        # We keep /message as a legacy fallback only.
        self.message_path = os.environ.get("MCP_GATEWAY_MESSAGE_PATH", "/message")

        # Docker's gateway may need to start containers and/or perform network calls.
        # Very short timeouts cause flaky "gateway unavailable" results.
        self.timeout_s = timeout_s if timeout_s is not None else _env_float("MCP_GATEWAY_TIMEOUT", 30.0)
        self.cache_ttl_s = cache_ttl_s if cache_ttl_s is not None else _env_float("MCP_GATEWAY_CACHE_TTL", 10.0)
        self.sse_timeout_s = _env_float("MCP_GATEWAY_SSE_TIMEOUT", 120.0)
        self.sse_endpoint_timeout_s = _env_float("MCP_GATEWAY_SSE_ENDPOINT_TIMEOUT", 2.0)
        self.enabled = enabled if enabled is not None else _env_bool("MCP_GATEWAY_ENABLE", True)
        self.api_key = os.environ.get("MCP_GATEWAY_API_KEY")
        self.api_header = os.environ.get("MCP_GATEWAY_API_HEADER", "Authorization")
        self.api_prefix = os.environ.get("MCP_GATEWAY_API_PREFIX", "Bearer ")
        self.prompt_echo = _env_bool("MCP_GATEWAY_PROMPT_ECHO", True)
        self._prompt_for_api_key()
        self.rpc_url = rpc_url or os.environ.get("MCP_GATEWAY_RPC_URL") or self._resolve_rpc_url()
        self.sse_url = os.environ.get("MCP_GATEWAY_SSE_URL") or self._resolve_sse_url()
        self.message_url = os.environ.get("MCP_GATEWAY_MESSAGE_URL")

        # MCP protocol/lifecycle settings.
        # Defaults align with the typical transport used:
        #  - legacy HTTP+SSE: protocol revision 2024-11-05
        #  - streamable HTTP: protocol revision 2025-03-26
        self.protocol_version_sse = os.environ.get("MCP_PROTOCOL_VERSION_SSE", "2024-11-05")
        self.protocol_version_http = os.environ.get("MCP_PROTOCOL_VERSION_HTTP", "2025-03-26")
        self.client_name = os.environ.get("MCP_CLIENT_NAME", "mcp-gateway-python")
        self.client_version = os.environ.get("MCP_CLIENT_VERSION", "0.1.0")

        # Streamable-HTTP session state.
        self._http_session_id: Optional[str] = None
        self._http_initialized: bool = False
        self._http_negotiated_protocol: Optional[str] = None

        self._tools_cache: List[Dict[str, Any]] = []
        self._openai_tools_cache: List[Dict[str, Any]] = []
        self._cache_at = 0.0
        self._openai_cache_at = 0.0

    def _resolve_rpc_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/mcp") or base.endswith("/rpc") or base.endswith("/jsonrpc"):
            return base
        return f"{base}{self.rpc_path}"

    def _resolve_sse_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/sse"):
            return base
        path = self.sse_path or "/sse"
        if not path.startswith("/"):
            path = "/" + path
        return f"{base}{path}"

    def _resolve_message_url(self) -> str:
        base = self.base_url.rstrip("/")
        path = self.message_path or "/message"
        if not path.startswith("/"):
            path = "/" + path
        return f"{base}{path}"

    def _prompt_for_api_key(self) -> None:
        if self.api_key or not self.enabled:
            return
        try:
            if self.prompt_echo:
                entered = input("Enter MCP_GATEWAY_API_KEY (leave blank to skip): ")
            else:
                entered = getpass("Enter MCP_GATEWAY_API_KEY (leave blank to skip): ")
        except Exception:
            return
        entered = (entered or "").strip()
        if entered:
            self.api_key = entered

    def _cache_valid(self, at: float) -> bool:
        if self.cache_ttl_s <= 0:
            return False
        return (time.time() - at) < self.cache_ttl_s

    def _post_json_raw(
        self,
        url: str,
        payload: Dict[str, Any],
        *,
        extra_headers: Optional[Dict[str, str]] = None,
        accept: Optional[str] = None,
        timeout_s: Optional[float] = None,
    ) -> tuple[str, Dict[str, str], int]:
        """POST JSON, returning raw response body, headers, and status.

        NOTE: MCP's legacy HTTP+SSE transport often returns an empty body or plain text
        (e.g. "Accepted") for POSTs. So we always treat the raw body as a string and
        let callers decide whether parsing as JSON is required.
        """

        body = json.dumps(payload).encode("utf-8")
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if accept:
            headers["Accept"] = accept
        headers.update(self._auth_headers())
        if extra_headers:
            headers.update(extra_headers)

        req = request.Request(url, data=body, headers=headers)
        with request.urlopen(req, timeout=(timeout_s or self.timeout_s)) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", None) or resp.getcode()
            resp_headers = {k: v for (k, v) in resp.getheaders()}
        return raw, resp_headers, int(status)

    def _post_json_expect_json(
        self,
        url: str,
        payload: Dict[str, Any],
        *,
        extra_headers: Optional[Dict[str, str]] = None,
        accept: Optional[str] = None,
        timeout_s: Optional[float] = None,
    ) -> tuple[Dict[str, Any], Dict[str, str], int]:
        raw, headers, status = self._post_json_raw(
            url,
            payload,
            extra_headers=extra_headers,
            accept=accept,
            timeout_s=timeout_s,
        )
        raw_stripped = (raw or "").strip()
        if not raw_stripped:
            raise RuntimeError(f"Empty response body from {url} (status {status}).")
        try:
            parsed = json.loads(raw_stripped)
        except Exception as exc:
            raise RuntimeError(f"Non-JSON response from {url} (status {status}): {raw_stripped[:200]}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError(f"Unexpected JSON response from {url}: {type(parsed)}")
        return parsed, headers, status

    def _send_json(
        self,
        url: str,
        payload: Dict[str, Any],
        *,
        extra_headers: Optional[Dict[str, str]] = None,
        accept: Optional[str] = None,
        timeout_s: Optional[float] = None,
    ) -> None:
        # Fire-and-forget: ignore body, but still raise on HTTP errors.
        self._post_json_raw(
            url,
            payload,
            extra_headers=extra_headers,
            accept=accept,
            timeout_s=timeout_s,
        )

    def _get_json(self, url: str) -> Dict[str, Any]:
        headers = self._auth_headers()
        req = request.Request(url, headers=headers)
        with request.urlopen(req, timeout=self.timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw)

    def _auth_headers(self) -> Dict[str, str]:
        if not self.api_key:
            return {}
        header = self.api_header.strip() or "Authorization"
        value = f"{self.api_prefix}{self.api_key}"
        return {header: value}

    def _rpc(self, method: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if self.transport == "sse":
            return self._rpc_sse(method, params)
        return self._rpc_streamable_http(method, params)

    def _open_sse(self) -> Any:
        headers = self._auth_headers()
        headers["Accept"] = "text/event-stream"
        req = request.Request(self.sse_url, headers=headers)
        return request.urlopen(req, timeout=self.sse_timeout_s)

    def _read_sse_event(self, resp: Any, deadline: float) -> tuple[Optional[str], str]:
        event = None
        data_lines: List[str] = []
        while True:
            if time.time() > deadline:
                raise TimeoutError("MCP SSE response timed out.")
            raw = resp.readline()
            if not raw:
                raise TimeoutError("MCP SSE stream closed.")
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line == "":
                if not data_lines:
                    event = None
                    continue
                data = "\n".join(data_lines)
                return event, data
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
                continue
            if line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())

    def _normalize_endpoint(self, endpoint: str) -> str:
        cleaned = endpoint.strip()

        # Some implementations send JSON here, e.g. {"endpoint": "/sse?..."}.
        if cleaned.startswith("{"):
            try:
                parsed = json.loads(cleaned)
                if isinstance(parsed, dict):
                    for key in ("endpoint", "message_endpoint", "messageEndpoint", "url"):
                        if isinstance(parsed.get(key), str) and parsed.get(key):
                            cleaned = str(parsed[key])
                            break
            except Exception:
                pass

        cleaned = cleaned.strip().strip('"')
        if cleaned.startswith("http://") or cleaned.startswith("https://"):
            return cleaned
        if not cleaned.startswith("/"):
            cleaned = "/" + cleaned
        return f"{self.base_url.rstrip('/')}{cleaned}"

    def _client_capabilities(self) -> Dict[str, Any]:
        """Return the client capabilities advertised during initialize.

        By default we advertise an empty capability set. (Most gateways/servers accept this.)
        If you need roots/sampling/etc., you can provide JSON via MCP_CLIENT_CAPABILITIES_JSON.
        """

        raw = os.environ.get("MCP_CLIENT_CAPABILITIES_JSON")
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        return {}

    def _build_initialize_request(self, *, protocol_version: str, request_id: int) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "initialize",
            "params": {
                "protocolVersion": protocol_version,
                "capabilities": self._client_capabilities(),
                "clientInfo": {"name": self.client_name, "version": self.client_version},
            },
        }

    def _build_initialized_notification(self) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}

    def _http_headers(self) -> Dict[str, str]:
        """Headers used for Streamable HTTP requests."""

        headers: Dict[str, str] = {}
        # Many Streamable HTTP implementations require the dual Accept header.
        # See examples of incompatibilities when it's missing.
        headers["Accept"] = "application/json, text/event-stream"
        # Some servers may look at this header when they can't infer protocol version elsewhere.
        headers["MCP-Protocol-Version"] = self.protocol_version_http
        if self._http_session_id:
            headers["Mcp-Session-Id"] = self._http_session_id
        return headers

    def _maybe_update_http_session(self, resp_headers: Dict[str, str]) -> None:
        # Per MCP Streamable HTTP, servers may return Mcp-Session-Id in response headers.
        # urllib normalizes header casing; treat case-insensitively.
        for k, v in resp_headers.items():
            if k.lower() == "mcp-session-id" and v:
                self._http_session_id = v
                return

    def _ensure_http_initialized(self) -> None:
        if self._http_initialized:
            return

        init_id = int(time.time() * 1000)
        init_req = self._build_initialize_request(
            protocol_version=self.protocol_version_http,
            request_id=init_id,
        )

        # Initialize (request/response).
        response, headers, _status = self._post_json_expect_json(
            self.rpc_url,
            init_req,
            extra_headers=self._http_headers(),
            accept="application/json, text/event-stream",
            timeout_s=self.timeout_s,
        )
        self._maybe_update_http_session(headers)

        if response.get("error"):
            raise RuntimeError(f"MCP error calling initialize: {response.get('error')}")
        result = response.get("result")
        if isinstance(result, dict):
            negotiated = result.get("protocolVersion")
            if isinstance(negotiated, str) and negotiated:
                self._http_negotiated_protocol = negotiated

        # Send notifications/initialized.
        self._send_json(
            self.rpc_url,
            self._build_initialized_notification(),
            extra_headers=self._http_headers(),
            accept="application/json, text/event-stream",
            timeout_s=self.timeout_s,
        )

        self._http_initialized = True

    def _rpc_sse(self, method: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        # MCP lifecycle requirements: the first message in a session MUST be initialize,
        # and the client MUST send notifications/initialized after receiving the response.
        # See MCP lifecycle documentation.

        request_id = int(time.time() * 1000)
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params

        # If user explicitly calls initialize, don't auto-wrap it.
        needs_handshake = method not in {"initialize", "notifications/initialized"}
        init_id = int(time.time() * 1000) + 1 if needs_handshake else None
        init_req = (
            self._build_initialize_request(
                protocol_version=self.protocol_version_sse,
                request_id=int(init_id or 0),
            )
            if needs_handshake
            else None
        )

        message_url = self.message_url
        endpoint_deadline = time.time() + self.sse_endpoint_timeout_s
        read_deadline = time.time() + self.sse_timeout_s

        init_sent = False
        init_done = not needs_handshake
        request_sent = False

        with self._open_sse() as resp:
            # First, try to discover the POST endpoint from the initial SSE stream.
            while message_url is None and time.time() < endpoint_deadline:
                event, data = self._read_sse_event(resp, read_deadline)
                if event in {"endpoint", "message_endpoint"} and data:
                    message_url = self._normalize_endpoint(data)
                    break

            if message_url is None:
                message_url = self._resolve_message_url()

            # Send initialize first (if required).
            if needs_handshake and init_req is not None and not init_sent:
                self._send_json(message_url, init_req)
                init_sent = True

            # Main read loop.
            while True:
                event, data = self._read_sse_event(resp, read_deadline)

                # Docker MCP Gateway returns a session-aware endpoint like /sse?sessionid=...
                # on the initial stream (event: endpoint). If we see it later, update.
                if event in {"endpoint", "message_endpoint"} and data:
                    message_url = self._normalize_endpoint(data)
                    continue

                if not data:
                    continue

                try:
                    message = json.loads(data)
                except Exception:
                    continue

                if not isinstance(message, dict):
                    continue

                # Wait for initialize response, then send notifications/initialized.
                if needs_handshake and not init_done and init_id is not None:
                    if message.get("id") == init_id:
                        if message.get("error"):
                            raise RuntimeError(f"MCP error calling initialize: {message.get('error')}")
                        init_done = True
                        # Complete handshake.
                        self._send_json(message_url, self._build_initialized_notification())
                        # Now we can send the actual request.
                        if not request_sent:
                            self._send_json(message_url, payload)
                            request_sent = True
                        continue

                # If no handshake is needed, send request immediately.
                if init_done and not request_sent:
                    self._send_json(message_url, payload)
                    request_sent = True

                if request_sent and message.get("id") == request_id:
                    if message.get("error"):
                        raise RuntimeError(f"MCP error calling {method}: {message.get('error')}")
                    if "result" in message:
                        return message["result"]
                    return message

    def _rpc_streamable_http(self, method: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        # Ensure MCP lifecycle has been completed for this session.
        if method not in {"initialize", "notifications/initialized"}:
            self._ensure_http_initialized()

        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        extra_headers = self._http_headers()

        # Some gateways return the session id header on every response.
        response, headers, _status = self._post_json_expect_json(
            self.rpc_url,
            payload,
            extra_headers=extra_headers,
            accept="application/json, text/event-stream",
            timeout_s=self.timeout_s,
        )
        self._maybe_update_http_session(headers)

        if response.get("error"):
            raise RuntimeError(f"MCP error calling {method}: {response.get('error')}")
        if "result" in response:
            return response["result"]
        return response

    def _extract_tools(self, payload: Any) -> Optional[List[Dict[str, Any]]]:
        if payload is None:
            return None
        if isinstance(payload, list):
            return [t for t in payload if isinstance(t, dict)]
        if isinstance(payload, dict):
            tools = payload.get("tools")
            if isinstance(tools, list):
                return [t for t in tools if isinstance(t, dict)]
        return None

    def _normalize_openai_parameters(self, parameters: Any) -> Dict[str, Any]:
        if not isinstance(parameters, dict):
            return {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            }
        normalized = dict(parameters)
        schema_type = normalized.get("type")
        if not schema_type:
            normalized["type"] = "object"
            schema_type = "object"
        if schema_type == "object":
            properties = normalized.get("properties")
            if not isinstance(properties, dict):
                normalized["properties"] = {}
            if "additionalProperties" not in normalized:
                normalized["additionalProperties"] = True
        return normalized

    def _fallback_get_tools(self) -> Optional[List[Dict[str, Any]]]:
        base = self.base_url.rstrip("/")
        for path in ("/tools", "/v1/tools", "/mcp/tools", "/tools/list"):
            try:
                payload = self._get_json(f"{base}{path}")
            except Exception:
                continue
            tools = self._extract_tools(payload)
            if tools is not None:
                return tools
        return None

    def list_tools(self, *, refresh: bool = False) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        if not refresh and self._tools_cache and self._cache_valid(self._cache_at):
            return list(self._tools_cache)
        last_error: Optional[Exception] = None
        for method in ("tools/list", "list_tools"):
            try:
                payload = self._rpc(method, None)
                tools = self._extract_tools(payload)
                if tools is not None:
                    self._tools_cache = tools
                    self._cache_at = time.time()
                    self._openai_tools_cache = []
                    self._openai_cache_at = 0.0
                    return list(self._tools_cache)
            except Exception as exc:
                last_error = exc
        tools = self._fallback_get_tools()
        if tools is not None:
            self._tools_cache = tools
            self._cache_at = time.time()
            self._openai_tools_cache = []
            self._openai_cache_at = 0.0
            return list(self._tools_cache)
        if last_error is not None:
            raise last_error
        raise RuntimeError("Unable to list MCP tools.")

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        if not self.enabled:
            raise RuntimeError("MCP gateway disabled.")
        last_error: Optional[Exception] = None
        for method in ("tools/call", "call_tool"):
            try:
                return self._rpc(method, {"name": name, "arguments": arguments})
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("Unable to call MCP tool.")

    def get_openai_tools(self, *, refresh: bool = False) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        if (
            not refresh
            and self._openai_tools_cache
            and self._cache_valid(self._openai_cache_at)
        ):
            return list(self._openai_tools_cache)
        tools = self.list_tools(refresh=refresh)
        openai_tools: List[Dict[str, Any]] = []
        for tool in tools:
            name = tool.get("name") or tool.get("tool") or tool.get("id")
            if not name:
                continue
            description = tool.get("description") or ""
            parameters = (
                tool.get("inputSchema")
                or tool.get("input_schema")
                or tool.get("parameters")
                or tool.get("schema")
            )
            parameters = self._normalize_openai_parameters(parameters)
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": str(name),
                        "description": str(description),
                        "parameters": parameters,
                    },
                }
            )
        self._openai_tools_cache = openai_tools
        self._openai_cache_at = time.time()
        return list(self._openai_tools_cache)

    def format_tool_result(self, result: Any) -> str:
        if isinstance(result, dict):
            if result.get("isError") or result.get("error"):
                err = result.get("error") or result.get("message") or result
                return f"MCP tool error: {json.dumps(err, ensure_ascii=True)}"
            content = result.get("content")
            if isinstance(content, list):
                parts: List[str] = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text" and "text" in item:
                            parts.append(str(item.get("text") or ""))
                        else:
                            parts.append(json.dumps(item, ensure_ascii=True))
                    elif isinstance(item, str):
                        parts.append(item)
                text = "\n".join(p for p in parts if p)
                if text:
                    return text
            if isinstance(content, str):
                return content
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=True)

    def status(self, *, refresh: bool = False) -> MCPGatewayStatus:
        if not self.enabled:
            return MCPGatewayStatus(
                enabled=False,
                available=False,
                url=self.base_url,
                tools=[],
                error="disabled",
            )
        try:
            tools = self.list_tools(refresh=refresh)
            return MCPGatewayStatus(
                enabled=True,
                available=True,
                url=self.base_url,
                tools=tools,
            )
        except Exception as exc:
            return MCPGatewayStatus(
                enabled=True,
                available=False,
                url=self.base_url,
                tools=[],
                error=str(exc),
            )


_GATEWAY: Optional[MCPGatewayClient] = None


def get_mcp_gateway() -> MCPGatewayClient:
    global _GATEWAY
    if _GATEWAY is None:
        _GATEWAY = MCPGatewayClient()
    return _GATEWAY


def _tool_names(tools: List[Dict[str, Any]]) -> List[str]:
    names = []
    for tool in tools:
        name = tool.get("name") or tool.get("tool") or tool.get("id")
        if not name:
            continue
        names.append(str(name))
    seen = set()
    unique: List[str] = []
    for name in names:
        if name in seen:
            continue
        unique.append(name)
        seen.add(name)
    return unique


def _missing_tools(names: List[str], required: set[str]) -> List[str]:
    available = {n.lower() for n in names}
    missing = [name for name in sorted(required) if name.lower() not in available]
    return missing


def report_mcp_status(logger: Optional[Any] = None) -> MCPGatewayStatus:
    client = get_mcp_gateway()
    status = client.status()
    if not status.enabled:
        message = "[MCP] Gateway disabled. Set MCP_GATEWAY_ENABLE=1 to enable."
    elif not status.available:
        detail = f" ({status.error})" if status.error else ""
        message = f"[MCP] Gateway unavailable at {status.url}{detail}."
    else:
        names = _tool_names(status.tools)
        if not names:
            message = f"[MCP] Gateway available at {status.url}, but no tools reported."
        else:
            preview = ", ".join(names[:12])
            suffix = f" (+{len(names) - 12} more)" if len(names) > 12 else ""
            message = f"[MCP] Gateway available at {status.url}. Tools ({len(names)}): {preview}{suffix}"
            missing_arxiv = _missing_tools(names, ARXIV_TOOL_NAMES)
            missing_paper = _missing_tools(names, PAPER_SEARCH_TOOL_NAMES)
            if missing_arxiv:
                message += " | Missing arXiv tools: " + ", ".join(missing_arxiv)
            if missing_paper:
                message += (
                    f" | Missing Paper Search tools: {len(missing_paper)}/{len(PAPER_SEARCH_TOOL_NAMES)}"
                )
    if logger is not None:
        try:
            logger.info(message)
        except Exception:
            print(message)
    else:
        print(message)
    return status

"""REST API validator — query a serving endpoint and assert on the response.

This is the validator for "the team's model/agent answers correctly" tasks
(AI Gateway endpoints, Knowledge Assistants, custom agents). It deliberately
lives outside ``services.sdk_checks``: those are pure lookups, while this one
*invokes* a model (costs tokens), so it carries its own clamps.

Safety constraints (by construction, not bolt-ons):

- Packs supply a serving-endpoint **name**, never a URL/headers/auth — the only
  egress is the workspace API host via the app's own SDK identity. ``url``/
  ``headers``/``auth``/``token`` keys in config are rejected outright (here and
  in the linter).
- Prompts are **host-authored** (from the pack); the player's submission never
  reaches the model in v1 — mirroring the sql_assertion stance that statements
  come from the pack, not players.
- Clamps: ``max_tokens`` ≤ 512, prompt ≤ 4000 chars, timeout ≤ 60s; the response
  is truncated in evidence.

Outcome mapping mirrors the ``databricks_sdk`` validator:

- expectation met        → ``passed``
- expectation unmet      → ``failed``
- bad config             → :class:`ValidatorConfigError` (host-visible error)
- endpoint unreachable / SDK unavailable → ``manual`` (host review; a pilot is
  never hard-blocked by a model that won't answer)

With no ``expect`` block, any successful response passes ("the endpoint
answered") — the linter warns so authors opt into that explicitly.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Optional

from .base import (
    ValidationContext,
    ValidationOutcome,
    Validator,
    ValidatorConfigError,
)
from .sql_assertion import evaluate_expectation

logger = logging.getLogger("databricks-quest.validators.rest_api")

MAX_TOKENS_CAP = 512
DEFAULT_MAX_TOKENS = 256
TIMEOUT_CAP_SECONDS = 60
PROMPT_CHAR_CAP = 4000
EVIDENCE_EXCERPT_CHARS = 500

# Config keys that would turn this into arbitrary-egress HTTP. Never allowed.
FORBIDDEN_CONFIG_KEYS = ("url", "headers", "auth", "token", "bearer_token")

_SLOT_RE = re.compile(r"\$\{([A-Za-z0-9_.]+)\}")


def _resolve_template(value: str, variables: Dict[str, Any], field: str) -> str:
    """Resolve ``${name}`` slots strictly — an unresolved slot is a config/context
    bug for this validator (there is no optional-filter semantic here)."""

    def sub(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name in variables and variables[name] is not None:
            return str(variables[name])
        raise ValidatorConfigError(
            f"rest_api {field} references unresolved variable '${{{name}}}'"
        )

    return _SLOT_RE.sub(sub, value)


def _extract_text(response: Any) -> str:
    """Pull the assistant text out of a serving-endpoint response, defensively.

    Handles chat completions (``choices[].message.content``), legacy
    completions (``choices[].text``), ``predictions``, and the agent/Responses
    shape (``output[].content[].text`` — what KA/MAS endpoints return)."""
    output = getattr(response, "output", None)
    if output is None and isinstance(response, dict):
        output = response.get("output")
    if output:
        texts: List[str] = []
        for item in output if isinstance(output, (list, tuple)) else [output]:
            content = getattr(item, "content", None)
            if content is None and isinstance(item, dict):
                content = item.get("content")
            for part in content if isinstance(content, (list, tuple)) else [content]:
                text = getattr(part, "text", None)
                if text is None and isinstance(part, dict):
                    text = part.get("text")
                if text:
                    texts.append(str(text))
        if texts:
            return "\n".join(texts)
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")
    if choices:
        first = choices[0]
        message = getattr(first, "message", None)
        if message is None and isinstance(first, dict):
            message = first.get("message")
        if message is not None:
            content = getattr(message, "content", None)
            if content is None and isinstance(message, dict):
                content = message.get("content")
            if content:
                return str(content)
        text = getattr(first, "text", None)
        if text is None and isinstance(first, dict):
            text = first.get("text")
        if text:
            return str(text)
    predictions = getattr(response, "predictions", None)
    if predictions is None and isinstance(response, dict):
        predictions = response.get("predictions")
    if predictions:
        return str(predictions[0] if isinstance(predictions, (list, tuple)) else predictions)
    return str(response)


def _build_messages(prompt: str) -> List[Any]:
    """Build the messages payload; SDK dataclasses when available, dicts otherwise."""
    try:
        from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

        return [ChatMessage(role=ChatMessageRole.USER, content=prompt)]
    except Exception:  # noqa: BLE001 - SDK not installed (tests) → plain dicts
        return [{"role": "user", "content": prompt}]


class RestAPIValidator(Validator):
    """Query a named serving endpoint with a host-authored prompt."""

    type = "rest_api"

    def __init__(self, client_factory: Optional[Callable[[], Any]] = None):
        # Injectable for tests; production lazily builds a WorkspaceClient.
        self._client_factory = client_factory

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        from databricks.sdk import WorkspaceClient

        return WorkspaceClient()

    def _query(
        self, client: Any, endpoint: str, prompt: str, max_tokens: int, payload_format: str
    ) -> Any:
        """Query the endpoint, handling both chat (``messages``) and agent
        (Responses-style ``input``) payload formats.

        Agent endpoints (Knowledge Assistants / Multi-Agent Supervisors) reject
        ``messages`` and require ``input`` — verified live. ``auto`` tries chat
        first and falls back when the endpoint says so.
        """
        if payload_format in ("auto", "messages"):
            serving = getattr(client, "serving_endpoints", None)
            if serving is None or not hasattr(serving, "query"):
                raise RuntimeError("workspace client exposes no serving_endpoints query API")
            try:
                return serving.query(
                    name=endpoint,
                    messages=_build_messages(prompt),
                    max_tokens=max_tokens,
                )
            except Exception as exc:  # noqa: BLE001
                if payload_format == "messages" or "input" not in str(exc).lower():
                    raise
                logger.info("rest_api %s rejected 'messages'; retrying with 'input'", endpoint)
        api = getattr(client, "api_client", None)
        if api is None or not hasattr(api, "do"):
            raise RuntimeError("workspace client exposes no raw API client for agent endpoints")
        return api.do(
            "POST",
            f"/serving-endpoints/{endpoint}/invocations",
            body={"input": [{"role": "user", "content": prompt}]},
        )

    def validate(self, ctx: ValidationContext) -> ValidationOutcome:
        cfg = ctx.config or {}
        for key in FORBIDDEN_CONFIG_KEYS:
            if key in cfg:
                raise ValidatorConfigError(
                    f"rest_api config must not contain '{key}' — endpoints are "
                    "addressed by name through the workspace client only"
                )

        endpoint_raw = (cfg.get("endpoint") or "").strip()
        if not endpoint_raw:
            raise ValidatorConfigError("rest_api requires an 'endpoint' name")
        prompt_raw = cfg.get("prompt")
        if not prompt_raw or not str(prompt_raw).strip():
            raise ValidatorConfigError("rest_api requires a 'prompt'")

        endpoint = _resolve_template(endpoint_raw, ctx.variables, "endpoint")
        prompt = _resolve_template(str(prompt_raw), ctx.variables, "prompt")
        if len(prompt) > PROMPT_CHAR_CAP:
            raise ValidatorConfigError(
                f"rest_api prompt exceeds {PROMPT_CHAR_CAP} characters"
            )

        try:
            max_tokens = int(cfg.get("max_tokens") or DEFAULT_MAX_TOKENS)
        except (TypeError, ValueError):
            raise ValidatorConfigError("rest_api max_tokens must be an integer")
        max_tokens = max(1, min(max_tokens, MAX_TOKENS_CAP))
        timeout = max(1, min(int(ctx.timeout_seconds or 30), TIMEOUT_CAP_SECONDS))

        try:
            client = self._client()
        except Exception as exc:  # noqa: BLE001
            logger.warning("rest_api client unavailable: %s", exc)
            return ValidationOutcome.manual_pending(
                "This check will be confirmed by your host.",
                private_message=f"workspace client unavailable: {type(exc).__name__}: {exc}",
                evidence={"endpoint": endpoint, "stage": "client"},
            )

        payload_format = (cfg.get("payload_format") or "auto").strip().lower()
        if payload_format not in ("auto", "messages", "input"):
            raise ValidatorConfigError(
                "rest_api payload_format must be 'auto', 'messages', or 'input'"
            )

        try:
            response = self._query(client, endpoint, prompt, max_tokens, payload_format)
        except Exception as exc:  # noqa: BLE001 - runtime inability → host review
            logger.warning("rest_api query to %s failed: %s", endpoint, exc)
            return ValidationOutcome.manual_pending(
                "This check will be confirmed by your host.",
                private_message=(
                    f"endpoint {endpoint!r} could not be queried: "
                    f"{type(exc).__name__}: {exc}"
                ),
                evidence={"endpoint": endpoint, "stage": "query", "timeout_seconds": timeout},
            )

        text = _extract_text(response)
        del response  # full payloads stay out of evidence; excerpt only
        verdict = evaluate_expectation(ctx.expect, [{"value": text}])
        evidence = {
            "endpoint": endpoint,
            "response_excerpt": text[:EVIDENCE_EXCERPT_CHARS],
            "reason": verdict["reason"],
        }
        if verdict["passed"]:
            return ValidationOutcome.passed_with(
                "Your endpoint answered correctly.",
                private_message=verdict["reason"],
                evidence=evidence,
            )
        return ValidationOutcome.failed_with(
            "Your endpoint responded, but the answer didn't meet the check yet.",
            private_message=verdict["reason"],
            evidence=evidence,
        )

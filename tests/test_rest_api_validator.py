"""Executable ``rest_api`` validator (PR19).

DB- and SDK-free: a fake duck-typed WorkspaceClient is injected. Covers the
outcome quadrant plus the safety clamps that make this validator shippable:

- expectation met         → ``passed``
- expectation unmet       → ``failed``
- bad config / forbidden keys / unresolved variable → ``error`` (authoring bug)
- endpoint can't be queried → ``manual`` (host review, never a block)
"""

from types import SimpleNamespace

import pytest

from validators.base import ERROR, FAILED, MANUAL, PASSED, ValidationContext, ValidatorConfigError
from validators.rest_api import (
    FORBIDDEN_CONFIG_KEYS,
    MAX_TOKENS_CAP,
    RestAPIValidator,
)


class _Serving:
    def __init__(self, answer="The refund window is 30 days.", raise_exc=None):
        self.answer = answer
        self.raise_exc = raise_exc
        self.calls = []

    def query(self, name, messages, max_tokens):
        self.calls.append({"name": name, "messages": messages, "max_tokens": max_tokens})
        if self.raise_exc is not None:
            raise self.raise_exc
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.answer))]
        )


def _validator(serving=None, *, raise_client=None):
    client = SimpleNamespace(serving_endpoints=serving or _Serving())

    def factory():
        if raise_client is not None:
            raise raise_client
        return client

    return RestAPIValidator(client_factory=factory)


def _ctx(config=None, expect=None, variables=None, timeout=30):
    return ValidationContext(
        validator_id="v1",
        type="rest_api",
        config=config or {"endpoint": "team-red-ka", "prompt": "Refund window?"},
        expect=expect,
        timeout_seconds=timeout,
        variables=variables or {},
    )


# ── outcome quadrant ──────────────────────────────────────────────────────────


def test_passed_when_expectation_met():
    v = _validator()
    out = v.validate(_ctx(expect={"operator": "contains", "value": "30 days"}))
    assert out.status == PASSED
    assert "30 days" in out.evidence["response_excerpt"]


def test_failed_when_expectation_unmet():
    v = _validator(_Serving(answer="I don't know."))
    out = v.validate(_ctx(expect={"operator": "contains", "value": "30 days"}))
    assert out.status == FAILED
    # Player-safe message; raw reason stays host-side.
    assert "30 days" not in out.public_message


def test_no_expect_passes_on_any_response():
    v = _validator(_Serving(answer="anything"))
    out = v.validate(_ctx(expect=None))
    assert out.status == PASSED


def test_query_failure_routes_to_manual():
    v = _validator(_Serving(raise_exc=RuntimeError("endpoint scaling")))
    out = v.validate(_ctx(expect={"operator": "contains", "value": "x"}))
    assert out.status == MANUAL
    assert out.evidence["stage"] == "query"
    assert "endpoint scaling" in (out.private_message or "")
    assert "endpoint scaling" not in out.public_message


def test_client_unavailable_routes_to_manual():
    v = _validator(raise_client=RuntimeError("no creds"))
    out = v.validate(_ctx())
    assert out.status == MANUAL
    assert out.evidence["stage"] == "client"


def test_client_without_query_surface_routes_to_manual():
    v = RestAPIValidator(client_factory=lambda: SimpleNamespace())
    out = v.validate(_ctx())
    assert out.status == MANUAL


# ── config errors ─────────────────────────────────────────────────────────────


def test_missing_endpoint_is_config_error():
    with pytest.raises(ValidatorConfigError):
        _validator().validate(_ctx(config={"prompt": "hi"}))


def test_missing_prompt_is_config_error():
    with pytest.raises(ValidatorConfigError):
        _validator().validate(_ctx(config={"endpoint": "e"}))


@pytest.mark.parametrize("key", FORBIDDEN_CONFIG_KEYS)
def test_forbidden_keys_are_config_errors(key):
    cfg = {"endpoint": "e", "prompt": "p", key: "https://evil.example"}
    with pytest.raises(ValidatorConfigError):
        _validator().validate(_ctx(config=cfg))


def test_unresolved_variable_is_config_error():
    cfg = {"endpoint": "${team_slug}-ka", "prompt": "hi"}
    with pytest.raises(ValidatorConfigError):
        _validator().validate(_ctx(config=cfg, variables={}))


def test_prompt_length_cap_is_config_error():
    cfg = {"endpoint": "e", "prompt": "x" * 5000}
    with pytest.raises(ValidatorConfigError):
        _validator().validate(_ctx(config=cfg))


# ── template resolution + clamps ──────────────────────────────────────────────


def test_endpoint_and_prompt_slots_resolve_from_variables():
    serving = _Serving()
    v = _validator(serving)
    out = v.validate(
        _ctx(
            config={"endpoint": "${team_slug}-ka", "prompt": "Hello ${team_slug}"},
            variables={"team_slug": "team-red"},
        )
    )
    assert out.status == PASSED
    assert serving.calls[0]["name"] == "team-red-ka"


def test_max_tokens_clamped():
    serving = _Serving()
    v = _validator(serving)
    v.validate(_ctx(config={"endpoint": "e", "prompt": "p", "max_tokens": 99999}))
    assert serving.calls[0]["max_tokens"] == MAX_TOKENS_CAP


# ── agent (Responses-style) endpoints: input payload + output extraction ──────


class _AgentAPIClient:
    """Raw api_client fake for an agent endpoint that answers via 'input'."""

    def __init__(self, answer="The refund window is 30 days."):
        self.answer = answer
        self.calls = []

    def do(self, method, path, body=None):
        self.calls.append({"method": method, "path": path, "body": body})
        return {"output": [{"content": [{"text": self.answer, "type": "output_text"}]}]}


def test_auto_falls_back_to_input_for_agent_endpoints():
    # The live KA/MAS error message, verbatim shape.
    serving = _Serving(
        raise_exc=RuntimeError(
            "Invalid request: 'messages' field is not supported. Please use 'input' field instead."
        )
    )
    api = _AgentAPIClient()
    client = SimpleNamespace(serving_endpoints=serving, api_client=api)
    v = RestAPIValidator(client_factory=lambda: client)
    out = v.validate(_ctx(expect={"operator": "contains", "value": "30 days"}))
    assert out.status == PASSED
    assert api.calls[0]["path"] == "/serving-endpoints/team-red-ka/invocations"
    assert api.calls[0]["body"]["input"][0]["content"] == "Refund window?"


def test_payload_format_input_skips_chat_query():
    serving = _Serving(raise_exc=AssertionError("must not be called"))
    api = _AgentAPIClient()
    client = SimpleNamespace(serving_endpoints=serving, api_client=api)
    v = RestAPIValidator(client_factory=lambda: client)
    out = v.validate(
        _ctx(
            config={"endpoint": "team-red-ka", "prompt": "Refund window?", "payload_format": "input"},
            expect={"operator": "contains", "value": "30"},
        )
    )
    assert out.status == PASSED
    assert serving.calls == []


def test_payload_format_messages_does_not_fall_back():
    serving = _Serving(raise_exc=RuntimeError("use 'input' field instead"))
    client = SimpleNamespace(serving_endpoints=serving, api_client=_AgentAPIClient())
    v = RestAPIValidator(client_factory=lambda: client)
    out = v.validate(
        _ctx(config={"endpoint": "e", "prompt": "p", "payload_format": "messages"})
    )
    assert out.status == MANUAL


def test_invalid_payload_format_is_config_error():
    with pytest.raises(ValidatorConfigError):
        _validator().validate(
            _ctx(config={"endpoint": "e", "prompt": "p", "payload_format": "soap"})
        )


# ── engine registration + linter contract ─────────────────────────────────────


def test_engine_registers_rest_api():
    from services.validation_engine import ValidationEngine

    engine = ValidationEngine(rest_validator=_validator())
    assert "rest_api" in engine.supported_types()
    out = engine.run_validator(
        {
            "validator_id": "v1",
            "type": "rest_api",
            "config_json": {"endpoint": "team-red-ka", "prompt": "Refund window?"},
            "expected_json": {"operator": "contains", "value": "30 days"},
            "timeout_seconds": 30,
        },
        submission={},
        variables={},
    )
    assert out.status == PASSED


def test_engine_maps_forbidden_key_to_error():
    from services.validation_engine import ValidationEngine

    engine = ValidationEngine(rest_validator=_validator())
    out = engine.run_validator(
        {
            "validator_id": "v1",
            "type": "rest_api",
            "config_json": {"endpoint": "e", "prompt": "p", "url": "https://evil"},
            "expected_json": None,
            "timeout_seconds": 30,
        },
        submission={},
        variables={},
    )
    assert out.status == ERROR


_PACK = """
schema_version: "1.0"
pack:
  slug: rest-probe
  title: Rest Probe
  version: 1.0.0
  owner: pilot@databricks.com
quests:
  - slug: q1
    title: Quest
    tasks:
      - slug: t1
        title: Task
        objective: Probe
        points: 100
        manual_validation_required: true
        validators:
          - id: v-manual
            type: manual
          - id: v-rest
            type: rest_api
{config}
"""


def _lint(config_yaml):
    from services.quest_pack_linter import lint_manifest_text

    return lint_manifest_text(_PACK.format(config=config_yaml))


def test_linter_accepts_valid_rest_api():
    result = _lint(
        '            endpoint: "${team_slug}-gateway"\n'
        '            prompt: "Refund window?"\n'
        "            expect:\n"
        "              operator: contains\n"
        '              value: "30"'
    )
    assert result.ok, result.errors
    assert not result.warnings


def test_linter_errors_on_missing_endpoint_and_prompt():
    result = _lint('            max_tokens: 10')
    assert not result.ok
    messages = " ".join(e["message"] for e in result.errors)
    assert "endpoint" in messages and "prompt" in messages


def test_linter_errors_on_url_key():
    result = _lint(
        '            endpoint: "e"\n'
        '            prompt: "p"\n'
        '            url: "https://evil.example"'
    )
    assert not result.ok


def test_linter_warns_when_no_expect():
    result = _lint('            endpoint: "e"\n            prompt: "p"')
    assert result.ok
    assert any("expect" in w["message"] for w in result.warnings)


def test_linter_errors_on_removed_types():
    from services.quest_pack_linter import lint_manifest_text

    pack = _PACK.format(config='            endpoint: "e"\n            prompt: "p"').replace(
        "type: rest_api", "type: system_table"
    )
    result = lint_manifest_text(pack)
    assert not result.ok
    assert any("Unknown validator type" in e["message"] for e in result.errors)

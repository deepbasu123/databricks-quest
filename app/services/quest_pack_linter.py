"""Quest pack manifest linter.

Produces actionable, structured findings without touching the database. Used by
``POST /api/host/quest-packs/lint`` and as a gate before import. Findings are
either ``error`` (blocks import) or ``warning`` (allowed, but worth flagging).

The linter does three passes:
1. YAML parse (syntax).
2. Pydantic structural validation (required fields / types).
3. Semantic rules (unique slugs, references, validators, operators, template
   variables).
"""

import re
from typing import Any, Dict, List, Optional, Tuple

import yaml

from models.quest_pack import (
    KNOWN_EXPECT_OPERATORS,
    KNOWN_VALIDATOR_TYPES,
    SUPPORTED_SCHEMA_VERSIONS,
    QuestPackManifest,
)

# Server-resolved template variables permitted in manifest strings.
SUPPORTED_TEMPLATE_VARS = {
    "event_id",
    "event_slug",
    "team_id",
    "team_slug",
    "team_prefix",
    "team_catalog",
    "team_schema",
    "event_catalog",
    "event_schema",
    "event_start",
    "event_end",
    "current_user",
    "team_members",
}

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TEMPLATE_VAR_RE = re.compile(r"\$\{([a-zA-Z0-9_]+)\}")


class LintResult:
    """Collects lint findings and a content summary."""

    def __init__(self) -> None:
        self.errors: List[Dict[str, str]] = []
        self.warnings: List[Dict[str, str]] = []
        self.summary: Dict[str, int] = {
            "quests": 0,
            "tasks": 0,
            "validators": 0,
            "hints": 0,
        }
        self.manifest: Optional[QuestPackManifest] = None

    def error(self, loc: str, message: str) -> None:
        self.errors.append({"loc": loc, "message": message})

    def warn(self, loc: str, message: str) -> None:
        self.warnings.append({"loc": loc, "message": message})

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "summary": self.summary,
        }


def _check_template_vars(result: LintResult, loc: str, text: Optional[str]) -> None:
    if not text or not isinstance(text, str):
        return
    for var in _TEMPLATE_VAR_RE.findall(text):
        if var not in SUPPORTED_TEMPLATE_VARS:
            result.warn(
                loc,
                f"Unknown template variable '${{{var}}}'. Supported: "
                + ", ".join(sorted(SUPPORTED_TEMPLATE_VARS)),
            )


# Validator types whose outcome is machine-decided (everything except manual).
AUTO_VALIDATOR_TYPES = {"sql_assertion", "databricks_sdk", "workspace_api", "rest_api"}
# Types that degrade to host review at runtime and therefore must pair with an
# explicit manual fallback (the rule the authoring guide has always stated).
FALLBACK_REQUIRED_TYPES = {"databricks_sdk", "workspace_api", "rest_api"}


def lint_manifest_text(manifest_yaml: str, strict: bool = False) -> LintResult:
    """Lint a raw YAML manifest string and return a :class:`LintResult`.

    ``strict`` adds the **playability gate** used for shipped packs and CI:
    every task must be provably playable end-to-end — auto validators carry
    machine-executable ``solutions``, SDK/REST validators pair with a manual
    fallback, manual-only tasks declare ``manual_validation_required``, and an
    unknown ``databricks_sdk`` check name becomes an error instead of a warning.
    """
    result = LintResult()

    # ── Pass 1: YAML syntax ──────────────────────────────────────────────────
    try:
        raw = yaml.safe_load(manifest_yaml)
    except yaml.YAMLError as exc:
        loc = "yaml"
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            loc = f"yaml:line {mark.line + 1}, col {mark.column + 1}"
        result.error(loc, f"Invalid YAML: {getattr(exc, 'problem', str(exc))}")
        return result

    if not isinstance(raw, dict):
        result.error("root", "Manifest must be a YAML mapping at the top level.")
        return result

    # ── Pass 2: structural validation via Pydantic ───────────────────────────
    try:
        manifest = QuestPackManifest.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError (avoid hard import)
        errors = getattr(exc, "errors", None)
        if callable(errors):
            for err in exc.errors():  # type: ignore[attr-defined]
                loc = ".".join(str(p) for p in err.get("loc", [])) or "root"
                result.error(loc, err.get("msg", "invalid value"))
        else:
            result.error("root", f"Manifest structure invalid: {exc}")
        return result

    result.manifest = manifest
    result.summary = manifest.counts()

    # ── Pass 3: semantic rules ────────────────────────────────────────────────
    if manifest.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        result.warn(
            "schema_version",
            f"Unsupported schema_version '{manifest.schema_version}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_SCHEMA_VERSIONS))}",
        )

    if not _SLUG_RE.match(manifest.pack.slug):
        result.error(
            "pack.slug",
            f"Invalid slug '{manifest.pack.slug}'. Use lowercase letters, "
            "digits, and single dashes (e.g. 'ai-bi-gameday').",
        )

    if not manifest.pack.version.strip():
        result.error("pack.version", "pack.version must not be empty.")

    if not manifest.quests:
        result.error("quests", "A quest pack must define at least one quest.")

    quest_slugs: set = set()
    for qi, quest in enumerate(manifest.quests):
        qloc = f"quests[{qi}]({quest.slug})"

        if not _SLUG_RE.match(quest.slug):
            result.error(f"{qloc}.slug", f"Invalid quest slug '{quest.slug}'.")
        if quest.slug in quest_slugs:
            result.error(f"{qloc}.slug", f"Duplicate quest slug '{quest.slug}'.")
        quest_slugs.add(quest.slug)
        _check_template_vars(result, f"{qloc}.narrative_md", quest.narrative_md)

        if not quest.tasks:
            result.error(f"{qloc}.tasks", "Each quest must define at least one task.")

        task_slugs: set = set()
        for ti, task in enumerate(quest.tasks):
            tloc = f"{qloc}.tasks[{ti}]({task.slug})"

            if not _SLUG_RE.match(task.slug):
                result.error(f"{tloc}.slug", f"Invalid task slug '{task.slug}'.")
            if task.slug in task_slugs:
                result.error(
                    f"{tloc}.slug",
                    f"Duplicate task slug '{task.slug}' within quest '{quest.slug}'.",
                )
            task_slugs.add(task.slug)

            if task.points < 0:
                result.error(f"{tloc}.points", "points must be >= 0.")

            _check_template_vars(result, f"{tloc}.instructions_md", task.instructions_md)

            # Validators required unless the task is explicitly manual.
            has_manual = any(v.type == "manual" for v in task.validators)
            if not task.validators and not task.manual_validation_required:
                result.error(
                    f"{tloc}.validators",
                    "Task has no validators. Add at least one validator or set "
                    "manual_validation_required: true.",
                )

            validator_ids: set = set()
            for vi, validator in enumerate(task.validators):
                vloc = f"{tloc}.validators[{vi}]({validator.id})"
                if validator.id in validator_ids:
                    result.error(
                        f"{vloc}.id",
                        f"Duplicate validator id '{validator.id}' within task.",
                    )
                validator_ids.add(validator.id)

                if validator.type not in KNOWN_VALIDATOR_TYPES:
                    # An unknown type would SKIP silently at runtime — the
                    # "stubbed task" failure mode — so it blocks import.
                    result.error(
                        f"{vloc}.type",
                        f"Unknown validator type '{validator.type}'. Every type "
                        "must have an executable backend. Known types: "
                        + ", ".join(sorted(KNOWN_VALIDATOR_TYPES)),
                    )

                if validator.mode not in {"sync", "async"}:
                    result.error(
                        f"{vloc}.mode",
                        f"Validator mode must be 'sync' or 'async', got '{validator.mode}'.",
                    )

                _lint_validator_config(result, vloc, validator, strict=strict)

            for hi, hint in enumerate(task.hints):
                hloc = f"{tloc}.hints[{hi}]"
                if hint.penalty_points > 0:
                    result.warn(
                        f"{hloc}.penalty_points",
                        "penalty_points is usually <= 0 (a penalty). "
                        f"Got {hint.penalty_points}.",
                    )

            _lint_solutions(result, tloc, task)
            if strict:
                _lint_task_playability(result, tloc, task)

    # Unlock-rule references must point at real quest slugs.
    for qi, quest in enumerate(manifest.quests):
        rule = quest.unlock_rule
        if rule and rule.type == "quest_completed":
            qloc = f"quests[{qi}]({quest.slug}).unlock_rule"
            if not rule.quest_slug:
                result.error(
                    f"{qloc}.quest_slug",
                    "unlock_rule type 'quest_completed' requires quest_slug.",
                )
            elif rule.quest_slug not in quest_slugs:
                result.error(
                    f"{qloc}.quest_slug",
                    f"unlock_rule references unknown quest '{rule.quest_slug}'.",
                )
            elif rule.quest_slug == quest.slug:
                result.error(
                    f"{qloc}.quest_slug",
                    "A quest cannot be gated on completing itself.",
                )

    return result


def _lint_solutions(result: LintResult, tloc: str, task: Any) -> None:
    """Validate the host-only ``solutions`` steps when present.

    Each step is a mapping with exactly one of ``sql`` (non-empty statement),
    ``workspace_op`` (mapping with an ``op`` name), or ``skip`` (a reason).
    """
    solutions = getattr(task, "solutions", None)
    if solutions is None:
        return
    if not isinstance(solutions, list) or not solutions:
        result.error(f"{tloc}.solutions", "solutions must be a non-empty list of steps.")
        return
    for si, step in enumerate(solutions):
        sloc = f"{tloc}.solutions[{si}]"
        if not isinstance(step, dict):
            result.error(sloc, "Each solution step must be a mapping.")
            continue
        kinds = [k for k in ("sql", "workspace_op", "skip") if k in step]
        if len(kinds) != 1:
            result.error(
                sloc,
                "Each solution step must have exactly one of: sql, workspace_op, skip.",
            )
            continue
        kind = kinds[0]
        value = step[kind]
        if kind == "sql":
            if not isinstance(value, str) or not value.strip():
                result.error(f"{sloc}.sql", "sql step must be a non-empty statement.")
            else:
                _check_template_vars(result, f"{sloc}.sql", value)
        elif kind == "workspace_op":
            if not isinstance(value, dict) or not str(value.get("op") or "").strip():
                result.error(
                    f"{sloc}.workspace_op",
                    "workspace_op step must be a mapping with an 'op' name.",
                )
        elif not isinstance(value, str) or not value.strip():
            result.error(f"{sloc}.skip", "skip step must carry a reason string.")


def _lint_task_playability(result: LintResult, tloc: str, task: Any) -> None:
    """Strict-mode playability gate for one task (shipped packs + CI)."""
    types = [v.type for v in task.validators]
    auto_types = [t for t in types if t in AUTO_VALIDATOR_TYPES]
    has_manual = "manual" in types

    # SDK/REST validators degrade to host review at runtime → the pack must
    # declare that path explicitly (manual fallback + the review flag).
    if any(t in FALLBACK_REQUIRED_TYPES for t in types):
        if not has_manual or not task.manual_validation_required:
            result.error(
                f"{tloc}.validators",
                "Strict: databricks_sdk/workspace_api/rest_api validators must "
                "pair with a manual validator and manual_validation_required: true.",
            )

    # Manual-only tasks must say so — review queues key off the flag.
    if not auto_types and not task.manual_validation_required:
        result.error(
            f"{tloc}.manual_validation_required",
            "Strict: a task with no auto validators must set "
            "manual_validation_required: true.",
        )

    # Machine playability: every auto-validated task ships executable solutions
    # so the operator preflight can play it end-to-end.
    if auto_types and not getattr(task, "solutions", None):
        result.error(
            f"{tloc}.solutions",
            "Strict: tasks with auto validators must carry 'solutions' steps "
            "(sql / workspace_op / skip) for the preflight harness.",
        )


def _lint_validator_config(
    result: LintResult, vloc: str, validator: Any, strict: bool = False
) -> None:
    """Type-specific validator checks."""
    vtype = validator.type
    cfg = validator.config()

    if vtype == "sql_assertion":
        statement = cfg.get("statement")
        if not statement:
            result.error(f"{vloc}.statement", "sql_assertion requires a 'statement'.")
        else:
            _check_template_vars(result, f"{vloc}.statement", statement)
        expect = validator.expect
        if expect is None:
            result.warn(
                f"{vloc}.expect",
                "sql_assertion has no 'expect' block; result will not be checked.",
            )
        elif expect.operator and expect.operator not in KNOWN_EXPECT_OPERATORS:
            result.error(
                f"{vloc}.expect.operator",
                f"Unknown operator '{expect.operator}'. Supported: "
                + ", ".join(sorted(KNOWN_EXPECT_OPERATORS)),
            )
    elif vtype in ("databricks_sdk", "workspace_api"):
        check = cfg.get("check")
        if not check:
            result.error(f"{vloc}.check", "databricks_sdk requires a 'check' name.")
        else:
            _lint_sdk_check(result, vloc, str(check), cfg.get("params") or {}, strict=strict)
    elif vtype == "rest_api":
        from validators.rest_api import FORBIDDEN_CONFIG_KEYS

        for key in FORBIDDEN_CONFIG_KEYS:
            if key in cfg:
                result.error(
                    f"{vloc}.{key}",
                    f"rest_api config must not contain '{key}' — endpoints are "
                    "addressed by serving-endpoint name only.",
                )
        endpoint = cfg.get("endpoint")
        if not endpoint:
            result.error(f"{vloc}.endpoint", "rest_api requires an 'endpoint' name.")
        else:
            _check_template_vars(result, f"{vloc}.endpoint", endpoint)
        prompt = cfg.get("prompt")
        if not prompt:
            result.error(f"{vloc}.prompt", "rest_api requires a 'prompt'.")
        else:
            _check_template_vars(result, f"{vloc}.prompt", prompt)
        expect = validator.expect
        if expect is None:
            result.warn(
                f"{vloc}.expect",
                "rest_api has no 'expect' block; any successful response will pass.",
            )
        elif expect.operator and expect.operator not in KNOWN_EXPECT_OPERATORS:
            result.error(
                f"{vloc}.expect.operator",
                f"Unknown operator '{expect.operator}'. Supported: "
                + ", ".join(sorted(KNOWN_EXPECT_OPERATORS)),
            )


def _lint_sdk_check(
    result: LintResult, vloc: str, check: str, params: Any, strict: bool = False
) -> None:
    """Validate a ``databricks_sdk`` check name and its params against the
    registry contracts in :mod:`services.sdk_checks`."""
    from services.sdk_checks import KNOWN_PARAMS, REQUIRED_PARAMS, known_checks

    if check not in known_checks():
        message = (
            f"Unknown databricks_sdk check '{check}'. Known checks: "
            + ", ".join(known_checks())
        )
        # Strict: an unknown check is a config error at runtime — block import.
        if strict:
            result.error(f"{vloc}.check", message)
        else:
            result.warn(f"{vloc}.check", message)
        return

    if not isinstance(params, dict):
        result.error(f"{vloc}.params", "databricks_sdk 'params' must be a mapping.")
        return

    for requirement in REQUIRED_PARAMS.get(check, []):
        if isinstance(requirement, tuple):
            if not any(params.get(k) for k in requirement):
                result.error(
                    f"{vloc}.params",
                    f"check '{check}' requires one of: " + ", ".join(requirement),
                )
        elif not params.get(requirement):
            result.error(
                f"{vloc}.params",
                f"check '{check}' requires param '{requirement}'.",
            )

    known = KNOWN_PARAMS.get(check)
    if known:
        for key in params:
            if key not in known:
                result.warn(
                    f"{vloc}.params",
                    f"check '{check}' does not use param '{key}'. Known params: "
                    + ", ".join(known),
                )
    for key, value in params.items():
        if isinstance(value, str):
            _check_template_vars(result, f"{vloc}.params.{key}", value)

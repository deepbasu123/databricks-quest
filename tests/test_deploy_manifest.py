"""Guards deploy-manifest.json — the machine-readable deploy surface an external
orchestrator (Control Tower) consumes. Keeps it valid and in sync with the repo
(pack dirs exist, declared packs parse with the declared keys)."""

from __future__ import annotations

import glob
import json
import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(REPO_ROOT, "deploy-manifest.json")


def _load() -> dict:
    with open(MANIFEST, encoding="utf-8") as fh:
        return json.load(fh)


def test_manifest_is_valid_json_with_required_shape():
    m = _load()
    assert m["schema_version"] == "1"
    assert m["app"]["source_path"] == "app"
    assert m["app"]["command"][0] == "uvicorn"
    assert set(m["roles"]) == {"standalone", "master", "child"}
    assert m["config"] and isinstance(m["config"], list)


def test_config_entries_well_formed():
    m = _load()
    for entry in m["config"]:
        assert entry["env"] and entry["env"].isupper()
        assert entry["type"] in {
            "bool", "enum", "string", "secret", "csv_emails", "warehouse_id",
        }
        for role in entry.get("roles", []):
            assert role in m["roles"]
        if entry["type"] == "enum":
            assert entry.get("options")


def test_pack_dirs_exist_and_packs_parse_with_declared_keys():
    m = _load()
    keys = m["packs"]["manifest_keys"]
    root_key, slug_key = keys["root"], keys["slug"]
    seen = 0
    for d in m["packs"]["dirs"]:
        full = os.path.join(REPO_ROOT, d)
        assert os.path.isdir(full), f"pack dir missing: {d}"
        for path in glob.glob(os.path.join(full, m["packs"]["glob"])):
            with open(path, encoding="utf-8") as fh:
                doc = yaml.safe_load(fh)
            pack = doc.get(root_key, {})
            assert pack.get(slug_key), f"{path} has no {root_key}.{slug_key}"
            seen += 1
    assert seen > 0, "no packs discovered via the manifest"


def test_integration_endpoints_declared():
    m = _load()
    integ = m["integration"]
    assert integ["service_token_env"] == "QUEST_SERVICE_TOKEN"
    for key in ("create_event", "import_pack", "roster_upsert", "completions"):
        assert integ[key]["path"].startswith("/api/")

"""deploy.sh must not build SQL by string concatenation (gap P1-20).

The deploy-time Delta->Lakebase shell sync was removed because it built bulk
INSERT statements by concatenating values with only quote-doubling. The
parameterized notebook (notebooks/lakebase_sync.py, psycopg2 execute_values) is
the single sync path. This lint keeps the unsafe pattern from coming back.
"""

import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    with open(os.path.join(REPO_ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


def test_deploy_sh_has_no_string_concatenated_inserts():
    text = _read("deploy.sh")
    for forbidden in (
        "', '.join(values)",
        "', '.join(escaped)",
        "INSERT INTO {table_name} ({columns}) VALUES",
    ):
        assert forbidden not in text, f"unsafe SQL-building pattern back in deploy.sh: {forbidden}"


def test_lakebase_sync_uses_parameterized_execute_values():
    nb = _read("notebooks/lakebase_sync.py")
    assert "execute_values" in nb  # the safe, parameterized sync remains the path

"""Drills that prove the CI sentinels ENFORCE their controls,
not merely exist. An integrity check for ci.yml catches
deletion; these drills catch the other weakening: a file that
survives but no longer enforces. Each drill weakens one guard's
target in a scratch copy and requires the guard to fail loudly.

Sentinels covered: ruff (lint), check_schema_parity.py,
error_paths_audit.py, oracle_probe.py. The syntax gate's drill
lives in test_failure_model.py::test_sentinel_catches_its_own_
target_class; the rollback drill (env-isolated subprocess)
lives in test_failure_model.py::test_rollback_recovery_
consistency.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc", ".pytest_cache", ".ruff_cache")


def fresh_copy(tmp_path, name="repo"):
    copy = tmp_path / name
    shutil.copytree(ROOT, copy, ignore=IGNORE)
    return copy


def run(script, cwd, timeout=420):
    """Run a sentinel as a subprocess. env is explicit and
    isolated: no os.environ mutation leaks into this process
    or into later tests."""
    return subprocess.run(
        [sys.executable, str(Path(script))],
        capture_output=True, text=True,
        cwd=str(cwd),
        env={**os.environ, "DATABASE_URL": ""},  # sentinels
        timeout=timeout)                        # self-provision


def test_ruff_enforces_lint(tmp_path):
    bad = tmp_path / "bad_style.py"
    bad.write_text("import os\nimport sys\n"      # unused: F401
                   "x = 1\n")
    r = subprocess.run(
        [sys.executable, "-m", "ruff", "check",
         str(bad), "--select", "F401"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode != 0, "ruff passed an unused import"


def test_parity_enforces_schema_lock(tmp_path):
    """A model table with no migration must fail parity."""
    copy = fresh_copy(tmp_path)
    models = copy / "app" / "models.py"
    src = models.read_text()
    assert "class ParityDrillModel" not in src
    models.write_text(src + '''

class ParityDrillModel(Base):
    __tablename__ = "parity_drill_model"
    id: Mapped[int] = mapped_column(primary_key=True)
''')
    r = run(copy / "scripts/check_schema_parity.py", copy)
    assert r.returncode != 0, (
        "parity passed an unmigrated model table")
    assert "parity_drill_model" in (r.stdout + r.stderr)


def test_error_audit_enforces_explanations(tmp_path):
    """A bare abort(400) with no explanation must fail the
    error-path audit - rejection without explanation is a
    silent failure from the user's point of view."""
    copy = fresh_copy(tmp_path)
    routes = copy / "app" / "routes.py"
    src = routes.read_text()
    weakened = src.replace(
        'abort(400, "campaign name required")',
        "abort(400)", 1)
    assert weakened != src, "drill anchor missing"
    routes.write_text(weakened)
    r = run(copy / "scripts/error_paths_audit.py", copy)
    assert r.returncode != 0, (
        "error audit passed an unexplained 400")
    assert "explanation" in (r.stdout + r.stderr).lower()


def test_probe_enforces_tenant_scope(tmp_path):
    """An unscoped campaign lookup must fail the oracle probe -
    the probe must catch the very class it exists to detect."""
    copy = fresh_copy(tmp_path)
    routes = copy / "app" / "routes.py"
    src = routes.read_text()
    weakened = src.replace(
        "        id=cid, user_id=user.id).one_or_none()",
        "        id=cid).one_or_none()  # drill", 1)
    assert weakened != src, "drill anchor missing"
    routes.write_text(weakened)
    r = run(copy / "scripts/oracle_probe.py", copy)
    assert r.returncode != 0, (
        "oracle probe passed an unscoped campaign lookup")
    # drill-specific evidence, never just a nonzero exit
    out = r.stdout + r.stderr
    assert "IDOR" in out or "leak" in out.lower(), out[-300:]

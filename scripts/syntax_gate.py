"""Syntax gate: static integrity of the Pilot (app), the
Intermediary (payment registry), and the Oracle (probe).

Runs three checks and fails loudly on any:

  1. Byte-compile every Python file (app, scripts, migrations,
     tests) - syntax errors, including ones pytest would only
     hit at collection time.
  2. Parse every Jinja template - a template with broken
     syntax otherwise surfaces only when a user renders it.
  3. Scan for scripted-edit damage fingerprints: injected-drift
     markers left from a drill, doubled-quote artifacts from
     sed-style replacements, and unresolved conflict markers.

Exit 0 = clean. Runs in CI before tests. If this gate ever fails
on a drill in progress, the drill's revert is incomplete -
that is the gate working, not a false positive.
"""
import compileall
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

failures = []

# 1. byte-compile
for pkg in ("app", "scripts", "migrations", "tests"):
    ok = compileall.compile_dir(
        str(ROOT / pkg), quiet=2, force=True)
    if not ok:
        failures.append(f"byte-compile failed under {pkg}/")

# 2. jinja parse
from jinja2 import Environment
env = Environment(autoescape=True)
templates = sorted((ROOT / "app/templates").glob("*.html"))
for t in templates:
    try:
        env.parse(t.read_text())
    except Exception as e:
        failures.append(f"template {t.name}: {e}")
if not templates:
    failures.append("no templates found (gate setup broken)")

# 3. damage fingerprints from scripted edits
# pattern built from fragments so this file does not
# literally contain its own targets (self-match found by the
# gate itself on first run, 2026-10-23)
_D = ("DRIFT" + " INJECTED", '""' + "csrf", "FIX" + "ME",
      "TO" + "DO", "<" * 7, ">" * 7, "|" * 7)
DAMAGE = re.compile("|".join(re.escape(x) for x in _D))
for area in ("app", "scripts", "tests"):
    for f in (ROOT / area).rglob("*"):
        if f.suffix not in (".py", ".html") or not f.is_file():
            continue
        for i, line in enumerate(
                f.read_text().splitlines(), 1):
            if DAMAGE.search(line):
                failures.append(
                    f"damage marker at {f.relative_to(ROOT)}:{i}")

if failures:
    print("SYNTAX GATE FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(f"syntax gate: all python compiles, {len(templates)} "
      "templates parse, no damage markers")
sys.exit(0)

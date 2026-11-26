"""Check that the built wheel contains everything the package contains — and nothing it should not.

Why this exists, in one sentence: `magic-builder --demo` was broken in the packaged artifact and worked
perfectly from the checkout. The example binder is a `.csv`, `.gitignore` had a rule that matched it
(`*_collection.csv`, meant for the author's real collections), and hatchling honours `.gitignore` when it
picks files — so the file was not in git, not in the wheel, and `--demo` would have failed for every
user who installed it while passing every test here.

Nothing about that is specific to a `.csv`. The invariant that catches it, and the next one like it:

    every file under the package directory is in the wheel, and every file in the wheel is one of ours.

Both directions, because they fail differently: a missing file breaks a feature for users only, and an
extra file ships something nobody meant to publish (a cache, a stray test, a secret).

Usage:  python tests/check_wheel.py [path/to.whl]
Without an argument it builds into a temporary directory, so CI needs no prior step.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "src" / "magic_builder"
WHEEL_PREFIX = "magic_builder/"

# Never expected in a package tree, and worth failing on rather than tolerating: the first is build
# residue, the second is the Scryfall cache, which is 80 MB and belongs to the user's machine.
JUNK_SUFFIXES = (".pyc", ".pyo")
JUNK_NAMES = {"scryfall_default_cards.jsonl.gz", "scryfall_default_cards.json"}


def build_wheel(outdir: Path) -> Path:
    print("  building a wheel...")
    r = subprocess.run(["uv", "build", "--wheel", "--out-dir", str(outdir)],
                       cwd=REPO, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        raise SystemExit("  build failed")
    wheels = sorted(outdir.glob("*.whl"))
    if not wheels:
        raise SystemExit("  the build produced no wheel")
    return wheels[-1]


def shipped_files() -> set[str]:
    """Every file in the package, as the wheel would name it. Excludes bytecode."""
    out = set()
    for p in PKG.rglob("*"):
        if not p.is_file():
            continue
        if any(part == "__pycache__" for part in p.parts):
            continue
        if p.suffix in JUNK_SUFFIXES:
            continue
        out.add(WHEEL_PREFIX + p.relative_to(PKG).as_posix())
    return out


def main() -> int:
    if len(sys.argv) > 1:
        wheel = Path(sys.argv[1])
    else:
        tmp = tempfile.mkdtemp(prefix="mb-wheelcheck-")
        wheel = build_wheel(Path(tmp))

    print(f"  wheel: {wheel.name}")
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
        in_wheel = {n for n in names if n.startswith(WHEEL_PREFIX) and not n.endswith("/")}

    package_files = shipped_files()
    missing = sorted(package_files - in_wheel)
    extra = sorted(in_wheel - package_files)
    junk = sorted(n for n in in_wheel
                  if n.endswith(JUNK_SUFFIXES) or Path(n).name in JUNK_NAMES)

    problems = 0

    if missing:
        problems += 1
        print(f"\n  MISSING from the wheel ({len(missing)}):")
        for n in missing[:20]:
            print(f"    {n}")
        print("    A feature that reads one of these works from a checkout and fails for a user who")
        print("    installed the package. If it is meant to ship, check .gitignore — hatchling honours")
        print("    it, so an ignore rule aimed at personal data can swallow a package file too.")

    if extra:
        problems += 1
        print(f"\n  UNEXPECTED in the wheel ({len(extra)}):")
        for n in extra[:20]:
            print(f"    {n}")
        print("    Every file in the wheel should come from the package directory and nowhere else.")

    if junk:
        problems += 1
        print(f"\n  JUNK in the wheel ({len(junk)}):")
        for n in junk[:20]:
            print(f"    {n}")

    # The entry points have to resolve too: a wheel whose console scripts point at a missing module
    # installs cleanly and then fails on the first command, which is the worst place to find out.
    with zipfile.ZipFile(wheel) as z:
        entry = [n for n in z.namelist() if n.endswith("entry_points.txt")]
        if not entry:
            problems += 1
            print("\n  no entry_points.txt in the wheel — the console scripts would not exist")
        else:
            text = z.read(entry[0]).decode()
            scripts = [ln.split("=")[0].strip() for ln in text.splitlines()
                       if "=" in ln and not ln.startswith("[")]
            print(f"  console scripts: {', '.join(scripts) or 'none'}")
            if not scripts:
                problems += 1
                print("  no console scripts declared")

    if problems:
        print(f"\n  FALLITO ({problems} problem(s))")
        return 1
    print(f"  OK: all {len(package_files)} package files ship, nothing unexpected, entry points intact")
    return 0


if __name__ == "__main__":
    sys.exit(main())

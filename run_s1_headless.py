"""Headless runner: solve a project (or the built-in S1 network) and print it.

Usage::

    python run_s1_headless.py                    # built-in S1 with A = 80
    python run_s1_headless.py --a-number 0       # the value published in the assignment
    python run_s1_headless.py examples/s1_a80.psn
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from psa.core.errors import PsaError
from psa.core.examples import build_s1
from psa.core.perunit import to_per_unit
from psa.core.serialize import load
from psa.core.study import STUDY_REGISTRY


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", nargs="?",
                    help="a .psn project file; omit to use the built-in S1 network")
    ap.add_argument("--a-number", type=float, default=80.0,
                    help="A-number for the built-in S1 network (default 80; "
                         "ignored when a project is given)")
    ap.add_argument("--study", action="append", choices=sorted(STUDY_REGISTRY),
                    help="run only these studies (repeatable)")
    ap.add_argument("--run-script", action="store_true",
                    help="execute the variable script the project links to "
                         "(ordinary Python: only for files you trust)")
    args = ap.parse_args(argv)

    study_options: dict[str, dict] = {}
    if args.project:
        try:
            proj = load(args.project, run_script=args.run_script)
        except (OSError, ValueError, PsaError) as exc:
            print(f"Could not open {args.project}: {exc}", file=sys.stderr)
            return 2
        net = proj.network
        # Each study runs with the options of the project's case of the same id.
        study_options = {c.id: c.options for c in proj.cases}
        title = proj.meta.get("title") or Path(args.project).name
        if proj.params.script_path and not args.run_script:
            print(f"Note: the linked script {proj.params.script_path} was not "
                  f"run; using the values saved in the project "
                  f"(pass --run-script to run it).")
    else:
        net = build_s1(args.a_number)
        title = net.meta.get("title", "Assignment S1")

    print(f"PowerLab - {title}")
    print("=" * (11 + len(title)))

    issues = net.validate()
    if issues:
        print()
        for i in issues:
            print(f"  {i}")

    keys = args.study or list(STUDY_REGISTRY)
    try:
        pu = to_per_unit(net)
        # The ports and buses chosen in the GUI are saved per study; use them.
        results = [STUDY_REGISTRY[k](net, study_options.get(k, {}), pu)
                   for k in keys]
    except PsaError as exc:
        print(f"\nThe studies could not run:\n{exc}", file=sys.stderr)
        return 2

    for res in results:
        print()
        print(res.text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

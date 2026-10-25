"""Headless runner: solve a project's study cases and print or export the results.

Usage::

    python run_headless.py examples/lf_220kv.psn                  # every case of the project
    python run_headless.py examples/lf_220kv.psn --case LFd       # one case (repeatable)
    python run_headless.py examples/s2_grid.psn --study thevenin  # a study with its defaults
    python run_headless.py examples/lf_220kv.psn --set A=0        # change a project variable
    python run_headless.py examples/s2_grid.psn --export s2.json  # also write .txt .csv .tex .json
    python run_headless.py examples/s2_grid.psn --list            # the cases and the studies

A case that fails prints why and the others still run (exit code 1); a project
that cannot be opened, an unknown case or a bad variable gives exit code 2.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from psa.core.cases import Case
from psa.core.errors import PsaError
from psa.core.export import FORMATS, export_results, format_for
from psa.core.serialize import load
from psa.core.study import STUDY_REGISTRY, default_cases, run_case


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", help="a .psn project file")
    ap.add_argument("--case", action="append", default=[], metavar="ID",
                    help="run this case of the project (repeatable)")
    ap.add_argument("--study", action="append", default=[], metavar="KEY",
                    choices=sorted(STUDY_REGISTRY),
                    help="run this study: the project's case of the same id, or its "
                         "defaults (repeatable)")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                    help="set a project variable before solving (repeatable)")
    ap.add_argument("--export", metavar="PATH",
                    help=f"also write the results to PATH ({', '.join(sorted(FORMATS))})")
    ap.add_argument("--quiet", action="store_true", help="do not print the results")
    ap.add_argument("--list", action="store_true",
                    help="list the project's cases and the available studies, and exit")
    ap.add_argument("--run-script", action="store_true",
                    help="execute the variable script the project links to "
                         "(ordinary Python: only for files you trust)")
    return ap


def _cases(proj, args) -> list[Case]:
    by_id = {c.id: c for c in proj.cases}
    missing = [cid for cid in args.case if cid not in by_id]
    if missing:
        known = ", ".join(by_id) or "none"
        raise KeyError(f"No case {', '.join(missing)} in the project (cases: {known}).")
    chosen = [by_id[cid] for cid in args.case]
    for key in args.study:
        own = by_id.get(key)
        chosen.append(own if own is not None and own.study == key else Case(id=key, study=key))
    if chosen:
        return chosen
    return list(proj.cases) or default_cases()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        proj = load(args.project, run_script=args.run_script)
    except (OSError, ValueError, PsaError) as exc:
        print(f"Could not open {args.project}: {exc}", file=sys.stderr)
        return 2

    if args.list:
        print("Cases:")
        for c in proj.cases:
            print(f"  {c.id:<12} {c.study:<15} {c.title}")
        if not proj.cases:
            print("  (none: every study runs with its defaults)")
        print("Studies:")
        for key, study in STUDY_REGISTRY.items():
            print(f"  {key:<15} {study.title}")
        return 0

    for item in args.set:
        name, sep, value = item.partition("=")
        if not sep or not name.strip():
            print(f"--set expects NAME=VALUE, got '{item}'.", file=sys.stderr)
            return 2
        proj.params.set_var(name.strip(), value.strip())
    if args.set:
        errors = [i for i in proj.apply_parameters() if i.is_error]
        if errors:
            print("The variables could not be applied:\n  "
                  + "\n  ".join(str(e) for e in errors), file=sys.stderr)
            return 2
    if args.export:
        try:
            format_for(args.export)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2

    try:
        cases = _cases(proj, args)
    except KeyError as exc:
        print(exc.args[0], file=sys.stderr)
        return 2

    title = proj.meta.get("title") or Path(args.project).name
    if not args.quiet:
        print(f"PowerLab - {title}")
        print("=" * (11 + len(title)))
        if args.set:
            print("Variables set: " + ", ".join(args.set))
        if proj.params.script_path and not args.run_script:
            print(f"Note: the linked script {proj.params.script_path} was not run; using "
                  f"the values saved in the project (pass --run-script to run it).")

    results, failed = [], 0
    for case in cases:
        try:
            res = run_case(proj.network, case, proj.params)
        except PsaError as exc:
            failed += 1
            print(f"\nCase {case.id} ({case.study}) failed:\n{exc}", file=sys.stderr)
            continue
        results.append(res)
        if not args.quiet:
            print()
            print(res.text())

    if args.export and results:
        path = export_results(results, args.export)
        if not args.quiet:
            print(f"\nWrote {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

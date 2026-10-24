"""Write the example projects built by psa.core.examples.example_projects().

    python tools/make_examples.py

``tests/test_s2.py`` checks that the files in ``examples/`` still match what
the builders produce, so run this after changing a builder, a layout or a case.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from psa.core.examples import example_projects  # noqa: E402
from psa.core.serialize import save  # noqa: E402


def main() -> int:
    for name, project in example_projects().items():
        path = save(project, ROOT / "examples" / name)
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

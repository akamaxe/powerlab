"""The user manual stays in step with the code.

The reference tables of ``docs/manual`` are generated from the components'
``FIELDS`` and the studies' options by ``tools/make_manual.py``; these tests
fail when the generated files are stale, when a component type or study has no
table in the manual, or when the manual refers to a figure that does not exist.
"""

from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

from psa.core.components import COMPONENT_CLASSES
from psa.core.study import STUDY_REGISTRY

ROOT = Path(__file__).resolve().parent.parent
MANUAL = ROOT / "docs" / "manual"


def _make_manual():
    spec = importlib.util.spec_from_file_location("make_manual", ROOT / "tools" / "make_manual.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sources() -> str:
    files = [MANUAL / "manual.tex", *sorted((MANUAL / "chapters").glob("*.tex"))]
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


class ManualMatchesTheCode(unittest.TestCase):
    def test_generated_tables_are_up_to_date(self):
        """If this fails, run ``python tools/make_manual.py``."""
        for name, text in _make_manual().generated_tables().items():
            with self.subTest(table=name):
                saved = (MANUAL / "generated" / name).read_text(encoding="utf-8")
                self.assertEqual(saved, text)

    def test_every_component_and_study_has_its_table(self):
        sources = _sources()
        for cls in COMPONENT_CLASSES:
            with self.subTest(component=cls.TYPE):
                self.assertIn(rf"\input{{generated/fields-{cls.TYPE}}}", sources)
        for key in STUDY_REGISTRY:
            with self.subTest(study=key):
                self.assertIn(rf"\input{{generated/options-{key}}}", sources)

    def test_every_figure_exists(self):
        sources = _sources()
        names = set(re.findall(r"\\shot(?:\[[^\]]*\])?\{([^}]+)\}", sources))
        names |= set(re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{figures/([^}]+)\}", sources))
        names = {n for n in names if "#" not in n}        # macro definitions
        names |= {f"icon-{cls.TYPE}.png" for cls in COMPONENT_CLASSES}
        self.assertGreater(len(names), 20)
        for name in sorted(names):
            with self.subTest(figure=name):
                self.assertTrue((MANUAL / "figures" / name).is_file(), name)

    def test_the_pdf_is_in_the_repository(self):
        self.assertTrue((MANUAL / "PowerLab_User_Manual.pdf").is_file())


if __name__ == "__main__":
    unittest.main()

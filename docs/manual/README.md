# PowerLab user manual

[`PowerLab_User_Manual.pdf`](PowerLab_User_Manual.pdf) is the user manual: installation, the
window, drawing networks, every component and study, study cases, results, the data manager,
export, the command line, the EG2100 assignments task by task, the project file, troubleshooting,
and the model equations.

## Sources

| Path | Contents |
|---|---|
| `manual.tex` | Preamble, title page and chapter list (KOMA-Script `scrreprt`, pdfLaTeX) |
| `chapters/*.tex` | One file per chapter and appendix, written by hand |
| `generated/*.tex` | The reference tables (every field of every component, every option of every study) and the callout positions of the window figure. Generated: do not edit |
| `figures/*.png` | Screenshots, exported diagrams and palette icons. Generated: do not edit |

## Rebuilding

From the repository root:

```
python tools/make_manual.py            # regenerate the reference tables
python tools/make_manual.py --figures  # ... and the screenshots
python tools/make_manual.py --pdf      # ... and compile PowerLab_User_Manual.pdf
python tools/make_manual.py --all      # all three
```

The PDF needs a TeX distribution with `latexmk` (TeX Live works); the auxiliary files go to
`docs/manual/build/`, which git ignores. The screenshots are taken with the native Qt platform on
windows that are never shown, with the window settings redirected to a temporary file, so they
always use the default layout.

`tests/test_manual.py` fails when the generated tables no longer match the code (a field or
option was added, renamed or re-described), when a component type or study has no table in the
manual, or when a chapter refers to a missing figure. After changing fields, options or the GUI,
run `python tools/make_manual.py --all` and commit the regenerated files with the change.

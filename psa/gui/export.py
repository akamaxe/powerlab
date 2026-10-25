"""Export the diagram as PNG, SVG or PDF.

The scene is rendered without its grid and without the selection highlight,
on white, cropped to the drawing plus a margin. Result badges on the diagram
are part of it, so exporting with the annotations shown gives a figure for a
report.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QMarginsF, QRectF, QSize, QSizeF, Qt
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPainter, QPdfWriter
from PySide6.QtSvg import QSvgGenerator

#: File extension -> format name.
DIAGRAM_FORMATS = {".png": "png", ".svg": "svg", ".pdf": "pdf"}
MARGIN = 24.0
#: Pixels per scene unit in a PNG: sharp enough to print at a column's width.
PNG_SCALE = 2.0


def diagram_rect(scene) -> QRectF:
    """What gets exported: every item, plus a margin."""
    return scene.itemsBoundingRect().adjusted(-MARGIN, -MARGIN, MARGIN, MARGIN)


@contextmanager
def _print_mode(scene):
    """Hide the grid and the selection while the scene paints itself."""
    selected = scene.selectedItems()
    for item in selected:
        item.setSelected(False)
    scene.print_mode = True
    scene.invalidate(scene.sceneRect(), scene.SceneLayer.BackgroundLayer)
    try:
        yield
    finally:
        scene.print_mode = False
        scene.invalidate(scene.sceneRect(), scene.SceneLayer.BackgroundLayer)
        for item in selected:
            item.setSelected(True)


def _render(scene, painter: QPainter, target: QRectF, source: QRectF) -> None:
    painter.setRenderHints(QPainter.RenderHint.Antialiasing
                           | QPainter.RenderHint.TextAntialiasing)
    scene.render(painter, target, source, Qt.AspectRatioMode.KeepAspectRatio)


def export_diagram(scene, path: str | Path) -> Path:
    """Write the diagram to ``path``; the format follows the extension."""
    path = Path(path)
    fmt = DIAGRAM_FORMATS.get(path.suffix.lower())
    if fmt is None:
        raise ValueError(f"Unknown diagram format for '{path.name}': use .png, .svg or .pdf.")
    source = diagram_rect(scene)
    w, h = source.width(), source.height()
    with _print_mode(scene):
        if fmt == "png":
            image = QImage(QSize(round(w * PNG_SCALE), round(h * PNG_SCALE)),
                           QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.white)
            painter = QPainter(image)
            _render(scene, painter, QRectF(0, 0, image.width(), image.height()), source)
            painter.end()
            if not image.save(str(path)):
                raise OSError(f"Could not write {path}")
        elif fmt == "svg":
            gen = QSvgGenerator()
            gen.setFileName(str(path))
            gen.setSize(QSize(round(w), round(h)))
            gen.setViewBox(QRectF(0, 0, w, h))
            gen.setTitle(path.stem)
            gen.setDescription("Single-line diagram exported by PowerLab")
            painter = QPainter(gen)
            _render(scene, painter, QRectF(0, 0, w, h), source)
            painter.end()
        else:
            pdf = QPdfWriter(str(path))
            pdf.setResolution(72)                  # one device pixel per point
            pdf.setPageLayout(QPageLayout(QPageSize(QSizeF(w, h), QPageSize.Unit.Point),
                                          QPageLayout.Orientation.Portrait,
                                          QMarginsF(0, 0, 0, 0)))
            pdf.setTitle(path.stem)
            pdf.setCreator("PowerLab")
            painter = QPainter(pdf)
            _render(scene, painter, QRectF(0, 0, w, h), source)
            painter.end()
    return path

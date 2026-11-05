"""The trash can drawing.

Everything is painted as vectors inside a 100x100 box and scaled to whatever
the window happens to be, so the icon is equally sharp on a 1080p screen, on a
4K screen at 150% scaling, and at any size the user drags the window to.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

BOX = 100.0

INK = QColor(226, 232, 240)
INK_HOVER = QColor(125, 231, 176)
CARD_TOP = QColor(38, 41, 51)
CARD_BOTTOM = QColor(24, 26, 33)
CARD_HOVER_TOP = QColor(42, 62, 54)
CARD_HOVER_BOTTOM = QColor(26, 40, 35)
BORDER = QColor(255, 255, 255, 38)
BORDER_HOVER = QColor(125, 231, 176, 190)
BUSY = QColor(129, 190, 247)


def _fit(rect: QRectF) -> tuple[float, QPointF]:
    """Scale factor and origin that centre the 100x100 box inside *rect*."""
    side = min(rect.width(), rect.height())
    scale = side / BOX
    origin = QPointF(
        rect.x() + (rect.width() - side) / 2.0,
        rect.y() + (rect.height() - side) / 2.0,
    )
    return scale, origin


def paint_card(painter: QPainter, rect: QRectF, hover: bool = False) -> None:
    """The sticky-note style background panel."""
    radius = min(rect.width(), rect.height()) * 0.18
    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    gradient.setColorAt(0.0, CARD_HOVER_TOP if hover else CARD_TOP)
    gradient.setColorAt(1.0, CARD_HOVER_BOTTOM if hover else CARD_BOTTOM)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(gradient))
    painter.drawRoundedRect(rect, radius, radius)

    pen = QPen(BORDER_HOVER if hover else BORDER)
    pen.setWidthF(max(1.0, min(rect.width(), rect.height()) * 0.012))
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    inset = pen.widthF() / 2.0
    painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), radius, radius)


def paint_can(
    painter: QPainter,
    rect: QRectF,
    lid_open: float = 0.0,
    color: QColor | None = None,
) -> None:
    """Draw the can.  *lid_open* runs 0 (shut) to 1 (tipped open)."""
    scale, origin = _fit(rect)
    if scale <= 0:
        return
    ink = QColor(color) if color is not None else QColor(INK)

    painter.save()
    painter.translate(origin)
    painter.scale(scale, scale)

    pen = QPen(ink)
    pen.setWidthF(5.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    # Body: tapered tub with a rounded bottom.
    body = QPainterPath()
    body.moveTo(21.0, 36.0)
    body.lineTo(27.5, 86.0)
    body.quadTo(28.5, 92.0, 34.5, 92.0)
    body.lineTo(65.5, 92.0)
    body.quadTo(71.5, 92.0, 72.5, 86.0)
    body.lineTo(79.0, 36.0)
    fill = QColor(ink)
    fill.setAlpha(28)
    painter.setBrush(QBrush(fill))
    painter.drawPath(body)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    # Ribs, angled to follow the taper.
    rib = QPen(ink)
    rib.setWidthF(4.0)
    rib.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(rib)
    for top_x, bottom_x in ((36.0, 39.0), (50.0, 50.0), (64.0, 61.0)):
        painter.drawLine(QPointF(top_x, 48.0), QPointF(bottom_x, 80.0))
    painter.setPen(pen)

    # Lid and handle pivot together around the left end of the lid.
    painter.save()
    painter.translate(15.0, 30.0)
    painter.rotate(-26.0 * max(0.0, min(1.0, lid_open)))
    painter.translate(-15.0, -30.0)

    lid = QRectF(15.0, 22.0, 70.0, 10.0)
    lid_fill = QColor(ink)
    lid_fill.setAlpha(36)
    painter.setBrush(QBrush(lid_fill))
    painter.drawRoundedRect(lid, 5.0, 5.0)

    handle = QPainterPath()
    handle.moveTo(41.0, 22.0)
    handle.lineTo(41.0, 15.0)
    handle.quadTo(41.0, 11.0, 45.0, 11.0)
    handle.lineTo(55.0, 11.0)
    handle.quadTo(59.0, 11.0, 59.0, 15.0)
    handle.lineTo(59.0, 22.0)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(handle)
    painter.restore()

    painter.restore()


def paint_progress_ring(painter: QPainter, rect: QRectF, fraction: float) -> None:
    """Thin arc around the can while a drop is being processed."""
    scale, origin = _fit(rect)
    if scale <= 0:
        return
    painter.save()
    painter.translate(origin)
    painter.scale(scale, scale)

    ring = QRectF(6.0, 6.0, 88.0, 88.0)
    track = QPen(QColor(255, 255, 255, 40))
    track.setWidthF(4.0)
    painter.setPen(track)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(ring)

    arc = QPen(BUSY)
    arc.setWidthF(4.0)
    arc.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(arc)
    span = int(-360 * 16 * max(0.0, min(1.0, fraction)))
    painter.drawArc(ring, 90 * 16, span)
    painter.restore()


def make_pixmap(size: int = 64, lid_open: float = 0.0, with_card: bool = True) -> QPixmap:
    """Render the icon for the tray and window icon."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rect = QRectF(0, 0, size, size)
    if with_card:
        paint_card(painter, rect.adjusted(1, 1, -1, -1))
        paint_can(painter, rect.adjusted(size * 0.16, size * 0.16, -size * 0.16, -size * 0.16), lid_open)
    else:
        paint_can(painter, rect, lid_open)
    painter.end()
    return pixmap


def paint_resize_grip(painter: QPainter, rect: QRectF, side: float) -> None:
    """Three short diagonals in the bottom-right, so the corner looks grabbable."""
    length = max(4.0, min(14.0, side * 0.11))
    inset = max(4.0, side * 0.055)
    pen = QPen(QColor(255, 255, 255, 90))
    pen.setWidthF(max(1.2, side * 0.014))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    corner_x = rect.right() - inset
    corner_y = rect.bottom() - inset
    for step in range(3):
        offset = length * (step / 3.0)
        reach = length - offset
        if reach <= 1.0:
            continue
        painter.drawLine(
            QPointF(corner_x - reach, corner_y - offset),
            QPointF(corner_x - offset, corner_y - reach),
        )

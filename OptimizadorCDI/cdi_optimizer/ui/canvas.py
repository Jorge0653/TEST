from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)


class CanvasPrevisualizacion(QWidget):
    PALETA_PIEZAS = ("#3d7665", "#548c77", "#6b9b86", "#377f83", "#819a72")

    def __init__(self, parent=None, compacto=False):
        super().__init__(parent)
        self.compacto = compacto
        if compacto:
            self.setMinimumSize(120, 72)
        else:
            self.setMinimumSize(400, 360)
        self.datos_dibujo = None
        self._hovered_engraving = None
        self._engraving_tooltip_timer = QTimer(self)
        self._engraving_tooltip_timer.setSingleShot(True)
        self._engraving_tooltip_timer.setInterval(350)
        self._engraving_tooltip_timer.timeout.connect(self._mostrar_tooltip_grabado)
        self.setMouseTracking(not compacto)
        self.setObjectName("previewCanvas")

    def actualizar_grafico(self, datos):
        self.datos_dibujo = datos
        self._hovered_engraving = None
        self._engraving_tooltip_timer.stop()
        QToolTip.hideText()
        self.update()

    def mouseMoveEvent(self, event):
        if not self.compacto:
            hovered = self._grabado_bajo_cursor(
                event.position().x(), event.position().y()
            )
            if hovered != self._hovered_engraving:
                self._engraving_tooltip_timer.stop()
                self._hovered_engraving = hovered
                if hovered is not None:
                    self._engraving_tooltip_timer.start()
                else:
                    QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._engraving_tooltip_timer.stop()
        self._hovered_engraving = None
        QToolTip.hideText()
        super().leaveEvent(event)

    def _grabado_bajo_cursor(self, cursor_x, cursor_y):
        if not self.datos_dibujo or not self.datos_dibujo.get("grabados"):
            return None
        board_width = float(self.datos_dibujo.get("ancho", 0))
        board_height = float(self.datos_dibujo.get("alto", 0))
        if board_width <= 0 or board_height <= 0:
            return None
        area = self.rect().adjusted(52, 44, -52, -58)
        if area.width() <= 0 or area.height() <= 0:
            return None
        scale = min(area.width() / board_width, area.height() / board_height)
        offset_x = (self.width() - board_width * scale) / 2
        offset_y = (self.height() - board_height * scale) / 2 + 1
        for index, engraving in enumerate(self.datos_dibujo["grabados"]):
            x = offset_x + float(engraving["x"]) * scale
            y = offset_y + float(engraving["y"]) * scale
            width = float(engraving["w"]) * scale
            height = float(engraving["h"]) * scale
            if x <= cursor_x <= x + width and y <= cursor_y <= y + height:
                return index
        return None

    def _mostrar_tooltip_grabado(self):
        index = self._hovered_engraving
        if self.compacto or index is None or not self.datos_dibujo:
            return
        cursor = self.mapFromGlobal(QCursor.pos())
        if self._grabado_bajo_cursor(cursor.x(), cursor.y()) != index:
            return
        engraving = self.datos_dibujo["grabados"][index]
        tooltip = (
            f"{engraving.get('nombre', 'Grabado')}\n"
            f"Medidas: {engraving['w']:g} × {engraving['h']:g} mm"
        )
        if engraving.get("rotado"):
            tooltip += "\nGiro: 90°"
        QToolTip.showText(QCursor.pos(), tooltip, self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f7f9f7"))

        if not self.datos_dibujo:
            painter.setPen(QColor("#62716a"))
            painter.setFont(QFont("Bahnschrift", 13))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Calcula una propuesta para ver el acomodo")
            return

        data = self.datos_dibujo
        board_width = float(data.get("ancho", 0))
        board_height = float(data.get("alto", 0))
        if board_width <= 0 or board_height <= 0:
            return

        if self.compacto:
            area = self.rect().adjusted(6, 6, -6, -6)
        else:
            area = self.rect().adjusted(52, 44, -52, -58)
        if area.width() <= 0 or area.height() <= 0:
            return
        scale = min(area.width() / board_width, area.height() / board_height)
        drawn_width = board_width * scale
        drawn_height = board_height * scale
        if self.compacto:
            offset_x = area.left() + (area.width() - drawn_width) / 2
            offset_y = area.top() + (area.height() - drawn_height) / 2
        else:
            offset_x = (self.width() - drawn_width) / 2
            offset_y = (self.height() - drawn_height) / 2 + 1

        plates = data.get("placas", [])
        if plates:
            plate_x = offset_x
            for plate in plates:
                plate_y = offset_y + (board_height - plate["alto"]) * scale
                self._draw_plate(painter, plate, plate_x, plate_y, scale)
                plate_x += plate["ancho"] * scale
        else:
            self._draw_plate(painter, data, offset_x, offset_y, scale)

        for index, engraving in enumerate(data.get("grabados", [])):
            x = offset_x + float(engraving["x"]) * scale
            y = offset_y + float(engraving["y"]) * scale
            width = float(engraving["w"]) * scale
            height = float(engraving["h"]) * scale
            painter.setPen(QPen(QColor("#285747"), 1))
            painter.setBrush(QColor(self.PALETA_PIEZAS[index % len(self.PALETA_PIEZAS)]))
            painter.drawRect(round(x), round(y), max(1, round(width)), max(1, round(height)))
            if width > 74 and height > 28:
                painter.setPen(QColor("#ffffff"))
                painter.setFont(QFont("Bahnschrift", 8))
                painter.drawText(
                    round(x + 5), round(y + 4), max(1, round(width - 10)), max(1, round(height - 8)),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                    engraving.get("nombre", "Grabado"),
                )

        if len(plates) == 2:
            join_x = offset_x + float(plates[0]["ancho"]) * scale
            join_y = offset_y + (board_height - min(plate["alto"] for plate in plates)) * scale
            painter.setPen(QPen(QColor("#c45f42"), 2, Qt.PenStyle.DashLine))
            painter.drawLine(round(join_x), round(join_y), round(join_x), round(offset_y + drawn_height))

        if not self.compacto:
            painter.setPen(QColor("#34453d"))
            painter.setFont(QFont("Bahnschrift", 9))
            painter.drawText(
                self.rect().adjusted(0, 12, 0, 0),
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
                f"{board_width:g} × {board_height:g} mm",
            )

    @staticmethod
    def _draw_plate(painter, plate, x, y, scale):
        width = float(plate["ancho"])
        height = float(plate["alto"])
        grip = float(plate.get("pinza", 0))
        painter.setPen(QPen(QColor("#aab5ae"), 1))
        painter.setBrush(QColor("#e9eeea"))
        painter.drawRect(round(x), round(y), round(width * scale), round(height * scale))
        if grip > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#d77b5c"))
            painter.drawRect(
                round(x), round(y + (height - grip) * scale), round(width * scale), round(grip * scale)
            )

        margins = plate.get("margenes", {})
        left = float(margins.get("izquierdo", 0))
        right = float(margins.get("derecho", 0))
        top = float(margins.get("superior", 0))
        tolerance = float(plate.get("tolerancia", 0))
        usable_height = max(0, height - grip - top - tolerance)
        usable_width = max(0, width - left - right - tolerance)
        painter.setPen(QPen(QColor("#89978f"), 1, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(
            round(x + left * scale),
            round(y + top * scale),
            round(usable_width * scale),
            round(usable_height * scale),
        )


class ResultadoTab(QWidget):
    def __init__(self, titulo, parent=None):
        super().__init__(parent)
        self.datos = None
        self._detalle_default = titulo
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)

        self.resumen = QLabel("Sin propuesta")
        self.resumen.setObjectName("resultSummary")
        layout.addWidget(self.resumen)

        self.canvas = CanvasPrevisualizacion()
        layout.addWidget(self.canvas, 1)

        footer = QHBoxLayout()
        self.detalle = QLabel(titulo)
        self.detalle.setObjectName("mutedText")
        footer.addWidget(self.detalle, 1)
        self.btn_copiar = QPushButton("Copiar medidas")
        self.btn_copiar.setObjectName("secondaryButton")
        self.btn_copiar.clicked.connect(self.copiar_medidas)
        self.btn_copiar.setEnabled(False)
        footer.addWidget(self.btn_copiar)
        layout.addLayout(footer)

    def actualizar_grafico(self, datos):
        self.datos = datos
        self.canvas.actualizar_grafico(datos)
        if not datos:
            self.resumen.setText("Sin propuesta calculada")
            self.detalle.setText(self._detalle_default)
            self.btn_copiar.setEnabled(False)
            return

        count = len(datos.get("grabados", []))
        plate_count = len(datos.get("placas", [])) or 1
        if datos.get("placas"):
            self.detalle.setText("     ·     ".join(
                f"Placa {index}: {plate['ancho']:g} × {plate['alto']:g} mm"
                for index, plate in enumerate(datos["placas"], start=1)
            ))
        else:
            self.detalle.setText(self._detalle_default)
        self.resumen.setText(
            f"{datos['ancho']:g} × {datos['alto']:g} mm     ·     "
            f"{count} grabados     ·     {plate_count} placa{'s' if plate_count != 1 else ''}     ·     "
            f"{datos.get('eficiencia', 0):g}% de aprovechamiento"
        )
        self.btn_copiar.setEnabled(True)

    def copiar_medidas(self):
        if not self.datos:
            return
        from PySide6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if self.datos.get("placas"):
            measurements = "\n".join(
                f"Placa {index}: {plate['ancho']:g} × {plate['alto']:g} mm"
                for index, plate in enumerate(self.datos["placas"], start=1)
            )
            clipboard.setText(
                f"Conjunto: {self.datos['ancho']:g} × {self.datos['alto']:g} mm\n{measurements}"
            )
        else:
            clipboard.setText(f"{self.datos['ancho']:g} × {self.datos['alto']:g} mm")
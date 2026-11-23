from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from cdi_optimizer.core.metadata import extraer_medidas_xmp


class CanvasPrevisualizacion(QWidget):
    grabado_clicked = Signal(str)
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

    @staticmethod
    def _metadatos_extendidos(engraving):
        info = {
            "tinta_nombre": engraving.get("tinta_nombre"),
            "color_rgb": engraving.get("color_rgb"),
            "lineaje_lpi": engraving.get("lineaje_lpi"),
            "angulo_grados": engraving.get("angulo_grados"),
            "punto_trama": engraving.get("punto_trama"),
        }
        if any(value is not None for value in info.values()):
            return info
        ruta = engraving.get("ruta")
        if not ruta:
            return info
        cached = extraer_medidas_xmp(ruta)
        if not cached:
            return info
        for key in info:
            if key in cached:
                info[key] = cached[key]
        return info

    @staticmethod
    def _color_relleno_pieza(base_hex, rgb_tuple):
        base = QColor(base_hex)
        if not rgb_tuple or len(rgb_tuple) != 3:
            return base, None
        ink_color = QColor(*rgb_tuple)
        blended = QColor(
            round(base.red() * 0.72 + ink_color.red() * 0.28),
            round(base.green() * 0.72 + ink_color.green() * 0.28),
            round(base.blue() * 0.72 + ink_color.blue() * 0.28),
        )
        return blended, ink_color

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

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.compacto:
            index = self._grabado_bajo_cursor(
                event.position().x(), event.position().y()
            )
            if index is not None:
                path = self.datos_dibujo["grabados"][index].get("ruta")
                if path:
                    self.grabado_clicked.emit(path)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def leaveEvent(self, event):
        self._engraving_tooltip_timer.stop()
        self._hovered_engraving = None
        QToolTip.hideText()
        super().leaveEvent(event)

    def _calcular_transformacion(self):
        if not self.datos_dibujo:
            return None
        board_width = float(self.datos_dibujo.get("ancho", 0))
        board_height = float(self.datos_dibujo.get("alto", 0))
        if board_width <= 0 or board_height <= 0:
            return None
        area = (
            self.rect().adjusted(6, 6, -6, -6)
            if self.compacto
            else self.rect().adjusted(52, 44, -52, -58)
        )
        if area.width() <= 0 or area.height() <= 0:
            return None
        scale = min(area.width() / board_width, area.height() / board_height)
        if self.compacto:
            offset_x = area.left() + (area.width() - board_width * scale) / 2
            offset_y = area.top() + (area.height() - board_height * scale) / 2
        else:
            offset_x = (self.width() - board_width * scale) / 2
            offset_y = (self.height() - board_height * scale) / 2 + 1
        return board_width, board_height, scale, offset_x, offset_y

    def _grabado_bajo_cursor(self, cursor_x, cursor_y):
        if not self.datos_dibujo or not self.datos_dibujo.get("grabados"):
            return None
        transform = self._calcular_transformacion()
        if transform is None:
            return None
        _, _, scale, offset_x, offset_y = transform
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
        meta = self._metadatos_extendidos(engraving)
        lines = [
            f"{engraving.get('nombre', 'Grabado')}",
            f"Medidas: {engraving['w']:g} × {engraving['h']:g} mm",
        ]
        if meta["tinta_nombre"]:
            lines.append(f"Tinta: {meta['tinta_nombre']}")
        screen_parts = []
        if meta["lineaje_lpi"] is not None:
            screen_parts.append(f"{meta['lineaje_lpi']:g} LPI")
        if meta["angulo_grados"] is not None:
            screen_parts.append(f"{meta['angulo_grados']:g}°")
        if screen_parts:
            lines.append(f"Trama: {' · '.join(screen_parts)}")
        if meta["punto_trama"]:
            lines.append(f"Punto: {meta['punto_trama']}")
        if engraving.get("rotado"):
            lines.append("Giro: 90°")
        QToolTip.showText(QCursor.pos(), "\n".join(lines), self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f7f9f7"))

        if not self.datos_dibujo:
            painter.setPen(QColor("#62716a"))
            painter.setFont(QFont("Bahnschrift", 13))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Calcula una propuesta para ver el acomodo",
            )
            return

        data = self.datos_dibujo
        transform = self._calcular_transformacion()
        if transform is None:
            return
        board_width, board_height, scale, offset_x, offset_y = transform
        drawn_width = board_width * scale
        drawn_height = board_height * scale

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
            meta = self._metadatos_extendidos(engraving)
            base_hex = self.PALETA_PIEZAS[index % len(self.PALETA_PIEZAS)]
            fill_color, ink_color = self._color_relleno_pieza(
                base_hex, meta["color_rgb"]
            )

            rect_x = round(x)
            rect_y = round(y)
            rect_w = max(1, round(width))
            rect_h = max(1, round(height))

            painter.setPen(QPen(QColor("#285747"), 1))
            painter.setBrush(fill_color)
            painter.drawRect(rect_x, rect_y, rect_w, rect_h)

            text_left_pad = 5
            if ink_color is not None and rect_w >= 10 and rect_h >= 6:
                stripe_w = 4 if self.compacto else min(7, max(4, rect_w // 14))
                painter.save()
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(ink_color)
                painter.drawRect(rect_x + 1, rect_y + 1, stripe_w, max(1, rect_h - 2))
                painter.restore()
                text_left_pad = stripe_w + 5

            if not self.compacto and width > 74 and height > 28:
                painter.setPen(QColor("#ffffff"))
                painter.setFont(QFont("Bahnschrift", 8, QFont.Weight.DemiBold))
                label_text = engraving.get("nombre", "Grabado")
                if height > 44:
                    sub_parts = []
                    if meta["tinta_nombre"]:
                        sub_parts.append(meta["tinta_nombre"])
                    if meta["lineaje_lpi"] is not None:
                        sub_parts.append(f"{meta['lineaje_lpi']:g} LPI")
                    if meta["angulo_grados"] is not None:
                        sub_parts.append(f"{meta['angulo_grados']:g}°")
                    if sub_parts:
                        label_text += "\n" + " · ".join(sub_parts)
                painter.drawText(
                    rect_x + text_left_pad,
                    rect_y + 4,
                    max(1, rect_w - text_left_pad - 4),
                    max(1, rect_h - 8),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                    label_text,
                )

        if len(plates) == 2:
            join_x = offset_x + float(plates[0]["ancho"]) * scale
            join_y = (
                offset_y
                + (board_height - min(plate["alto"] for plate in plates)) * scale
            )
            painter.setPen(QPen(QColor("#c45f42"), 2, Qt.PenStyle.DashLine))
            painter.drawLine(
                round(join_x),
                round(join_y),
                round(join_x),
                round(offset_y + drawn_height),
            )

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
        painter.drawRect(
            round(x), round(y), round(width * scale), round(height * scale)
        )
        if grip > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#d77b5c"))
            painter.drawRect(
                round(x),
                round(y + (height - grip) * scale),
                round(width * scale),
                round(grip * scale),
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
    resumen_requested = Signal()
    grabado_clicked = Signal(str)
    remnant_mode_changed = Signal(bool)
    remnant_used_requested = Signal()
    remnant_exit_requested = Signal()

    def __init__(self, titulo, parent=None):
        super().__init__(parent)
        self.datos = None
        self._remnant_mode = False
        self._previous_data = None
        self._detalle_default = titulo

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)

        self.resumen = QLabel("Sin propuesta")
        self.resumen.setObjectName("resultSummary")
        layout.addWidget(self.resumen)

        self.canvas = CanvasPrevisualizacion()
        self.canvas.grabado_clicked.connect(self.grabado_clicked.emit)
        layout.addWidget(self.canvas, 1)

        footer = QHBoxLayout()
        self.detalle = QLabel(titulo)
        self.detalle.setObjectName("mutedText")
        footer.addWidget(self.detalle, 1)

        self.btn_resumen = QPushButton("Ver resumen")
        self.btn_resumen.setObjectName("secondaryButton")
        self.btn_resumen.clicked.connect(self.resumen_requested.emit)
        self.btn_resumen.setEnabled(False)
        footer.addWidget(self.btn_resumen)
        layout.addLayout(footer)

        self.remnant_toolbar = QWidget(self)
        self.remnant_toolbar.setMinimumHeight(68)
        toolbar_layout = QVBoxLayout(self.remnant_toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(6)

        self.remnant_context = QLabel()
        self.remnant_context.setObjectName("sectionTitle")
        self.remnant_context.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.remnant_context.setWordWrap(True)
        toolbar_layout.addWidget(self.remnant_context)

        remnant_actions = QHBoxLayout()
        remnant_actions.addStretch(1)
        self.remnant_used_button = QPushButton("Marcar usado…")
        self.remnant_used_button.setObjectName("secondaryButton")
        self.remnant_used_button.clicked.connect(self.remnant_used_requested.emit)
        remnant_actions.addWidget(self.remnant_used_button)

        self.remnant_exit_button = QPushButton("Salir de vista")
        self.remnant_exit_button.setObjectName("primaryButton")
        self.remnant_exit_button.clicked.connect(self.remnant_exit_requested.emit)
        remnant_actions.addWidget(self.remnant_exit_button)
        remnant_actions.addStretch(1)
        toolbar_layout.addLayout(remnant_actions)

        layout.addWidget(self.remnant_toolbar)
        self.remnant_toolbar.hide()

    def actualizar_grafico(self, datos):
        if self._remnant_mode:
            self._remnant_mode = False
            self._previous_data = None
            self.remnant_toolbar.hide()
            self.remnant_mode_changed.emit(False)
        self._render_grafico(datos)

    def mostrar_retazo(self, datos):
        if not self._remnant_mode:
            self._previous_data = self.datos
        self._remnant_mode = True
        self._render_grafico(datos)
        self.remnant_context.setText(
            f"VISTA DE RETAZO #{datos['retazo_id']} · "
            f"{datos['retazo_ancho']:g} × {datos['retazo_alto']:g} mm · "
            f"{datos['material']}"
        )
        self.remnant_toolbar.show()
        self.remnant_mode_changed.emit(True)

    def salir_visualizacion_retazo(self):
        if not self._remnant_mode:
            return
        previous_data = self._previous_data
        self._remnant_mode = False
        self._previous_data = None
        self.remnant_toolbar.hide()
        self._render_grafico(previous_data)
        self.remnant_mode_changed.emit(False)

    def _render_grafico(self, datos):
        self.datos = datos
        self.canvas.actualizar_grafico(datos)
        if not datos:
            self.resumen.setText("Sin propuesta calculada")
            self.detalle.setText(self._detalle_default)
            self.btn_resumen.setEnabled(False)
            return
        count = len(datos.get("grabados", []))
        plate_count = len(datos.get("placas", [])) or 1
        if datos.get("placas"):
            self.detalle.setText(
                "   |   ".join(
                    f"Placa {index}: {plate['ancho']:g} × {plate['alto']:g} mm"
                    for index, plate in enumerate(datos["placas"], start=1)
                )
            )
        else:
            self.detalle.setText(self._detalle_default)
        self.resumen.setText(
            f"{datos['ancho']:g} × {datos['alto']:g} mm   ·   "
            f"{count} grabados   ·   {plate_count} placa{'s' if plate_count != 1 else ''}   ·   "
            f"{datos.get('eficiencia', 0):g}% de aprovechamiento"
        )
        self.btn_resumen.setEnabled(True)
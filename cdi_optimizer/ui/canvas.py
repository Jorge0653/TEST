from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from cdi_optimizer.core.dispatcher import validar_acomodo_para_maquina
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
    enviar_maquina_requested = Signal(int)
    dispatched_cleared = Signal()

    def __init__(self, titulo, parent=None):
        super().__init__(parent)
        self.datos = None
        self._remnant_mode = False
        self._dispatched_mode = False
        self._previous_data = None
        self._detalle_default = titulo
        self._maquinas = []
        self._dispatch_busy = False
        self._manual_selection_count = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        self.resumen = QLabel("Sin propuesta")
        self.resumen.setObjectName("resultSummary")
        layout.addWidget(self.resumen)

        self.dispatched_banner = QFrame(self)
        self.dispatched_banner.setObjectName("dispatchedBanner")
        banner_layout = QHBoxLayout(self.dispatched_banner)
        banner_layout.setContentsMargins(12, 6, 8, 6)
        banner_layout.setSpacing(10)
        self.dispatched_label = QLabel()
        self.dispatched_label.setObjectName("dispatchedBannerText")
        self.dispatched_label.setWordWrap(True)
        banner_layout.addWidget(self.dispatched_label, 1)
        self.clear_dispatched_button = QPushButton("Limpiar vista")
        self.clear_dispatched_button.setObjectName("secondaryButton")
        self.clear_dispatched_button.clicked.connect(self.limpiar_vista_enviada)
        banner_layout.addWidget(self.clear_dispatched_button)
        self.dispatched_banner.hide()
        layout.addWidget(self.dispatched_banner)

        self.canvas = CanvasPrevisualizacion()
        self.canvas.grabado_clicked.connect(self.grabado_clicked.emit)
        layout.addWidget(self.canvas, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.detalle = QLabel(titulo)
        self.detalle.setObjectName("mutedText")
        footer.addWidget(self.detalle, 1)

        self.btn_resumen = QPushButton("Ver resumen")
        self.btn_resumen.setObjectName("secondaryButton")
        self.btn_resumen.setMinimumHeight(40)
        self.btn_resumen.clicked.connect(self.resumen_requested.emit)
        self.btn_resumen.setEnabled(False)
        footer.addWidget(self.btn_resumen)

        self._machine_buttons = {}
        for slot in (1, 2):
            btn = QPushButton(f"Enviar a Máquina {slot}")
            btn.setObjectName(f"machineButton{slot}")
            btn.setProperty("warning", False)
            btn.setMinimumHeight(40)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setEnabled(False)
            btn.clicked.connect(
                lambda _checked=False, s=slot: self.enviar_maquina_requested.emit(s)
            )
            footer.addWidget(btn)
            self._machine_buttons[slot] = btn

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

    @property
    def en_modo_enviado(self):
        return self._dispatched_mode

    def actualizar_maquinas(self, maquinas):
        self._maquinas = list(maquinas)
        self._actualizar_botones_maquina()

    def actualizar_seleccion_manual(self, count):
        self._manual_selection_count = max(0, int(count))
        self._actualizar_botones_maquina()

    def set_dispatch_busy(self, busy):
        self._dispatch_busy = bool(busy)
        self._actualizar_botones_maquina()

    def _actualizar_botones_maquina(self):
        by_slot = {m.slot: m for m in self._maquinas}
        has_active_layout = bool(self.datos) and not self._dispatched_mode
        has_manual_selection = not has_active_layout and self._manual_selection_count > 0

        for slot, btn in self._machine_buttons.items():
            maquina = by_slot.get(slot)
            if maquina is None:
                btn.setText(f"Enviar a Máquina {slot}")
                btn.setToolTip("Configura esta máquina en Preferencias → Máquinas.")
                btn.setEnabled(False)
                self._set_button_warning(btn, False)
                continue

            if not maquina.configurada:
                btn.setText(f"Enviar a {maquina.nombre}")
                btn.setEnabled(False)
                btn.setToolTip(
                    f"{maquina.nombre} no tiene carpeta destino configurada.\n"
                    "Configúrala en Preferencias → Máquinas."
                )
                self._set_button_warning(btn, False)
                continue

            if has_active_layout:
                warnings = validar_acomodo_para_maquina(self.datos, maquina)
                has_warning = bool(warnings)
                prefix = "⚠ " if has_warning else ""
                btn.setText(f"{prefix}Enviar a {maquina.nombre}")
                btn.setEnabled(not self._dispatch_busy)
                tooltip = (
                    f"Mover los .len del acomodo visible a {maquina.nombre}\n"
                    f"Tambor máximo: {maquina.tambor_w:g} × {maquina.tambor_h:g} mm"
                )
                if warnings:
                    tooltip += "\n\n⚠ " + "\n⚠ ".join(warnings)
                btn.setToolTip(tooltip)
                self._set_button_warning(btn, has_warning)
            elif has_manual_selection:
                count = self._manual_selection_count
                btn.setText(f"Enviar {count} a {maquina.nombre}")
                btn.setEnabled(not self._dispatch_busy)
                btn.setToolTip(
                    f"Envío manual directo (sin acomodo calculado): "
                    f"moverá {count} archivo(s) seleccionado(s) en el árbol a {maquina.nombre}.\n"
                    f"Tambor máximo: {maquina.tambor_w:g} × {maquina.tambor_h:g} mm"
                )
                self._set_button_warning(btn, False)
            else:
                btn.setText(f"Enviar a {maquina.nombre}")
                btn.setEnabled(False)
                btn.setToolTip(
                    "Calcula un acomodo o selecciona archivos en el árbol para enviar."
                )
                self._set_button_warning(btn, False)

    @staticmethod
    def _set_button_warning(button, warning):
        if button.property("warning") != warning:
            button.setProperty("warning", warning)
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def _salir_modo_enviado(self):
        if self._dispatched_mode:
            self._dispatched_mode = False
            self.dispatched_banner.hide()

    def mostrar_enviado(self, datos, nombre_maquina, cantidad_archivos):
        if self._remnant_mode:
            self._remnant_mode = False
            self._previous_data = None
            self.remnant_toolbar.hide()
            self.remnant_mode_changed.emit(False)
        self._dispatched_mode = True
        self._render_grafico(datos)
        self.dispatched_label.setText(
            f"✓ Enviado a {nombre_maquina} ({cantidad_archivos} archivo"
            f"{'s' if cantidad_archivos != 1 else ''} LEN) · Vista de referencia"
        )
        self.dispatched_banner.show()
        self._actualizar_botones_maquina()

    def limpiar_vista_enviada(self):
        if not self._dispatched_mode:
            return
        self._dispatched_mode = False
        self.dispatched_banner.hide()
        self._render_grafico(None)
        self.dispatched_cleared.emit()

    def actualizar_grafico(self, datos):
        self._salir_modo_enviado()
        if self._remnant_mode:
            self._remnant_mode = False
            self._previous_data = None
            self.remnant_toolbar.hide()
            self.remnant_mode_changed.emit(False)
        self._render_grafico(datos)

    def mostrar_retazo(self, datos):
        self._salir_modo_enviado()
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
            self._actualizar_botones_maquina()
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
        self._actualizar_botones_maquina()
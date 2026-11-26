from datetime import datetime

from PySide6.QtCore import QEvent, QSettings, QTimer, Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from cdi_optimizer.core.remnants import calcular_cortes_sobrantes_retazo
from cdi_optimizer.core.scanner import extraer_codigo_fotopolimero
from .canvas import CanvasPrevisualizacion

_SORT_ROLE = Qt.ItemDataRole.UserRole + 1


def _etiqueta_fp_material(material_name):
    code = extraer_codigo_fotopolimero(material_name)
    return f"FP{code}" if code else None


def _leer_margenes_produccion(parent=None):
    """Obtain current margins, grip and cut tolerance from main window or QSettings."""
    node = parent
    while node is not None:
        if hasattr(node, "_configuracion_actual"):
            try:
                return node._configuracion_actual()
            except Exception:
                break
        node = node.parent() if hasattr(node, "parent") and callable(node.parent) else None

    settings = QSettings("MTRemozar", "OptimizadorCDI")
    return {
        "margen_izquierdo": float(settings.value("margen_izquierdo", 7)),
        "margen_derecho": float(settings.value("margen_derecho", 7)),
        "margen_superior": float(settings.value("margen_superior", 7)),
        "pinza": float(settings.value("pinza", 25)),
        "tolerancia": float(settings.value("tol_corte", 3)),
    }


def _formatear_fecha_ingreso(raw_timestamp):
    """Return human-readable date and numeric timestamp for sorting."""
    text = str(raw_timestamp or "").strip()
    if not text:
        return "—", 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text[:19], fmt)
            return parsed.strftime("%Y-%m-%d"), parsed.timestamp()
        except ValueError:
            continue
    return text[:10], 0.0


class _SortableTableWidgetItem(QTableWidgetItem):
    """Table item that sorts numerically when _SORT_ROLE is populated."""

    def __lt__(self, other):
        if isinstance(other, QTableWidgetItem):
            my_val = self.data(_SORT_ROLE)
            other_val = other.data(_SORT_ROLE)
            if my_val is not None and other_val is not None:
                try:
                    return float(my_val) < float(other_val)
                except (TypeError, ValueError):
                    return str(my_val).casefold() < str(other_val).casefold()
        return super().__lt__(other)


class _DialogoDatosRetazo(QDialog):
    def __init__(
        self,
        materials,
        remnant=None,
        parent=None,
        *,
        inventory=None,
        locations=None,
        production_config=None,
        allow_batch=False,
        is_duplicate=False,
    ):
        super().__init__(parent)
        self.inventory = inventory or getattr(parent, "inventory", None)
        self._materials = list(materials)
        self._remnant = remnant
        self._allow_batch = bool(allow_batch or remnant is None or is_duplicate)
        self._production_config = production_config or _leer_margenes_produccion(parent)

        if is_duplicate:
            title = "Duplicar retazo"
        elif remnant:
            title = f"Editar retazo #{remnant.get('id', '')}"
        else:
            title = "Nuevo retazo"
        self.setWindowTitle(title)
        self.setMinimumWidth(470)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.width = QDoubleSpinBox()
        self.width.setRange(1, 5000)
        self.width.setDecimals(1)
        self.width.setSingleStep(10)
        self.width.setValue(300.0)
        self.width.setSuffix(" mm")

        self.height = QDoubleSpinBox()
        self.height.setRange(1, 5000)
        self.height.setDecimals(1)
        self.height.setSingleStep(10)
        self.height.setValue(400.0)
        self.height.setSuffix(" mm")

        self.usable_preview = QLabel()
        self.usable_preview.setWordWrap(True)
        self.usable_preview.setStyleSheet("font-size: 9pt; padding: 2px 0;")

        material_row = QHBoxLayout()
        material_row.setSpacing(6)
        self.material = QComboBox()
        self.material.setEditable(True)
        self.material.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.material.addItems(self._materials)
        self.material.setCurrentIndex(-1)
        self.material.lineEdit().setPlaceholderText("Selecciona o escribe para buscar")
        self._update_material_completer()
        material_row.addWidget(self.material, 1)

        self.add_material_btn = QPushButton("+")
        self.add_material_btn.setObjectName("secondaryButton")
        self.add_material_btn.setFixedWidth(34)
        self.add_material_btn.setToolTip("Dar de alta un material nuevo sin cerrar esta ventana")
        self.add_material_btn.clicked.connect(self._alta_rapida_material)
        material_row.addWidget(self.add_material_btn)

        self.material_fp_hint = QLabel()
        self.material_fp_hint.setWordWrap(True)
        self.material_fp_hint.setStyleSheet("font-size: 9pt;")

        self.location = QLineEdit()
        self.location.setPlaceholderText("Ej. Estante A, Cajón 67...")
        available_locations = (
            list(locations)
            if locations is not None
            else (self.inventory.ubicaciones() if self.inventory is not None and hasattr(self.inventory, "ubicaciones") else [])
        )
        if available_locations:
            loc_completer = QCompleter(available_locations, self.location)
            loc_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            loc_completer.setFilterMode(Qt.MatchFlag.MatchContains)
            self.location.setCompleter(loc_completer)

        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Opcional")

        self.quantity = QSpinBox()
        self.quantity.setRange(1, 50)
        self.quantity.setValue(1)
        self.quantity.setSuffix(" pza(s)")
        self.quantity.setToolTip("Permite registrar varios retazos idénticos en un solo paso")

        if remnant:
            self.width.setValue(float(remnant["ancho_mm"]))
            self.height.setValue(float(remnant["alto_mm"]))
            self.material.setCurrentText(str(remnant["material"]))
            self.location.setText(str(remnant.get("ubicacion", "")))
            self.notes.setText(str(remnant.get("notas", "")))
        elif len(self._materials) == 1:
            self.material.setCurrentIndex(0)

        form.addRow("Ancho físico", self.width)
        form.addRow("Alto físico", self.height)
        form.addRow("Área útil estimada", self.usable_preview)
        form.addRow("Material", material_row)
        form.addRow("Compatibilidad FP", self.material_fp_hint)
        form.addRow("Ubicación", self.location)
        form.addRow("Notas", self.notes)
        if self._allow_batch:
            form.addRow("Cantidad (copias)", self.quantity)
        else:
            self.quantity.hide()

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Guardar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.width.valueChanged.connect(self._actualizar_vista_previa_util)
        self.height.valueChanged.connect(self._actualizar_vista_previa_util)
        self.material.currentTextChanged.connect(self._actualizar_hint_material)

        for spin in (self.width, self.height):
            if spin.lineEdit() is not None:
                spin.lineEdit().installEventFilter(self)

        self._actualizar_vista_previa_util()
        self._actualizar_hint_material()
        QTimer.singleShot(0, self._foco_inicial)

    def _foco_inicial(self):
        self.width.setFocus(Qt.FocusReason.OtherFocusReason)
        if self.width.lineEdit() is not None:
            self.width.lineEdit().selectAll()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn and watched in (
            self.width.lineEdit(),
            self.height.lineEdit(),
        ):
            QTimer.singleShot(0, watched.selectAll)
        return super().eventFilter(watched, event)

    def _update_material_completer(self):
        completer = QCompleter(self._materials, self.material)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.material.setCompleter(completer)

    def _actualizar_vista_previa_util(self):
        w = float(self.width.value())
        h = float(self.height.value())
        area_cm2 = (w * h) / 100.0
        plate_w, plate_h = sorted((w, h))
        cfg = self._production_config
        usable_w = (
            plate_w
            - float(cfg.get("margen_izquierdo", 7))
            - float(cfg.get("margen_derecho", 7))
            - float(cfg.get("tolerancia", 3))
        )
        usable_h = (
            plate_h
            - float(cfg.get("margen_superior", 7))
            - float(cfg.get("pinza", 25))
            - float(cfg.get("tolerancia", 3))
        )
        if usable_w <= 0 or usable_h <= 0:
            self.usable_preview.setStyleSheet("color: #b43c2f; font-size: 9pt; font-weight: 700;")
            self.usable_preview.setText(
                f"Superficie física: {area_cm2:,.1f} cm² · "
                "Sin área útil con los márgenes y pinza actuales."
            )
        else:
            self.usable_preview.setStyleSheet("color: #3d7665; font-size: 9pt;")
            self.usable_preview.setText(
                f"{usable_w:g} × {usable_h:g} mm útiles "
                f"(Superficie física: {area_cm2:,.1f} cm²)"
            )

    def _actualizar_hint_material(self):
        text = self.material.currentText().strip()
        if not text:
            self.material_fp_hint.setStyleSheet("color: #75847c; font-size: 9pt;")
            self.material_fp_hint.setText("Selecciona un material del catálogo.")
            return
        fp_tag = _etiqueta_fp_material(text)
        if fp_tag:
            self.material_fp_hint.setStyleSheet("color: #285b49; font-size: 9pt; font-weight: 700;")
            self.material_fp_hint.setText(
                f"Compatible automáticamente con archivos {fp_tag}."
            )
        else:
            self.material_fp_hint.setStyleSheet("color: #a86420; font-size: 9pt;")
            self.material_fp_hint.setText(
                "Sin código 45 / 67 / 112: solo se emparejará con archivos LEN sin etiqueta FP."
            )

    def _alta_rapida_material(self):
        initial = self.material.currentText().strip()
        name, ok = QInputDialog.getText(
            self,
            "Nuevo material",
            "Nombre del material (incluye 45, 67 o 112 para vincular con FP):",
            text=initial,
        )
        if not ok or not name.strip():
            return
        clean = name.strip()
        if self.inventory is not None:
            try:
                clean = self.inventory.agregar_material(clean)
            except ValueError as error:
                QMessageBox.information(self, "Catálogo de materiales", str(error))
        if clean.casefold() not in {m.casefold() for m in self._materials}:
            self._materials.append(clean)
            self._materials.sort(key=str.casefold)
            self.material.clear()
            self.material.addItems(self._materials)
            self._update_material_completer()
        self.material.setCurrentText(clean)

    def accept(self):
        mat_text = self.material.currentText().strip()
        if not mat_text:
            QMessageBox.warning(
                self,
                "Material requerido",
                "Selecciona o escribe un material antes de guardar el retazo.",
            )
            self.material.setFocus()
            return

        by_key = {m.casefold(): m for m in self._materials}
        if mat_text.casefold() not in by_key:
            fp_tag = _etiqueta_fp_material(mat_text)
            fp_notice = (
                f"\n\nSe vinculará automáticamente con archivos {fp_tag}."
                if fp_tag
                else "\n\nNota: El nombre no incluye 45, 67 ni 112, por lo que no se asociará automáticamente a archivos etiquetados con FP."
            )
            answer = QMessageBox.question(
                self,
                "Material no registrado",
                f"El material '{mat_text}' no está en el catálogo.{fp_notice}\n\n"
                "¿Deseas darlo de alta ahora mismo sin perder los datos capturados?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.material.setFocus()
                return
            if self.inventory is not None:
                try:
                    mat_text = self.inventory.agregar_material(mat_text)
                except (ValueError, OSError) as error:
                    QMessageBox.warning(self, "Material no añadido", str(error))
                    self.material.setFocus()
                    return
            self._materials.append(mat_text)
            self.material.addItem(mat_text)
            self.material.setCurrentText(mat_text)
        else:
            self.material.setCurrentText(by_key[mat_text.casefold()])

        super().accept()

    def values(self):
        return {
            "ancho_mm": self.width.value(),
            "alto_mm": self.height.value(),
            "material": self.material.currentText().strip(),
            "ubicacion": self.location.text().strip(),
            "notas": self.notes.text().strip(),
        }

    def batch_count(self):
        return self.quantity.value() if self._allow_batch else 1


class DialogoDimensionesRetazo(QDialog):
    def __init__(self, remnant, parent=None, *, layout_result=None):
        super().__init__(parent)
        self.setWindowTitle("Actualizar sobrante")
        self.setMinimumWidth(420)
        self._remnant = remnant
        self._orig_w = float(remnant["ancho_mm"])
        self._orig_h = float(remnant["alto_mm"])
        orig_area_cm2 = (self._orig_w * self._orig_h) / 100.0

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        info_label = QLabel(
            f"Retazo original #{remnant.get('id', '')}: "
            f"{self._orig_w:g} × {self._orig_h:g} mm "
            f"({orig_area_cm2:,.1f} cm² · {remnant.get('material', '')})"
        )
        info_label.setObjectName("mutedText")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        reminder_text = "Mide con regla el sobrante físico real tras el corte e ingresa sus nuevas medidas."
        if layout_result:
            cut_w = float(layout_result.get("ancho_acomodo", layout_result.get("ancho", 0)))
            cut_h = float(layout_result.get("alto_acomodo", layout_result.get("alto", 0)))
            if cut_w > 0 and cut_h > 0:
                reminder_text = (
                    f"Corte ocupado por el acomodo: {cut_w:g} × {cut_h:g} mm.\n"
                    "Mide con regla la pieza física sobrante e ingresa sus dimensiones reales:"
                )
        reminder_label = QLabel(reminder_text)
        reminder_label.setWordWrap(True)
        reminder_label.setStyleSheet("color: #263830; font-size: 9pt;")
        layout.addWidget(reminder_label)

        self.width = QDoubleSpinBox()
        self.width.setRange(1, 5000)
        self.width.setDecimals(1)
        self.width.setSingleStep(10)
        self.width.setSuffix(" mm")
        self.width.setValue(self._orig_w)

        self.height = QDoubleSpinBox()
        self.height.setRange(1, 5000)
        self.height.setDecimals(1)
        self.height.setSingleStep(10)
        self.height.setSuffix(" mm")
        self.height.setValue(self._orig_h)

        self.area_preview = QLabel()
        self.area_preview.setStyleSheet("color: #3d7665; font-size: 9pt;")

        form = QFormLayout()
        form.addRow("Ancho restante", self.width)
        form.addRow("Alto restante", self.height)
        form.addRow("Área restante", self.area_preview)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Guardar sobrante")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.width.valueChanged.connect(self._actualizar_area_restante)
        self.height.valueChanged.connect(self._actualizar_area_restante)
        for spin in (self.width, self.height):
            if spin.lineEdit() is not None:
                spin.lineEdit().installEventFilter(self)

        self._actualizar_area_restante()
        QTimer.singleShot(0, self._foco_inicial)

    def _foco_inicial(self):
        self.width.setFocus(Qt.FocusReason.OtherFocusReason)
        if self.width.lineEdit() is not None:
            self.width.lineEdit().selectAll()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn and watched in (
            self.width.lineEdit(),
            self.height.lineEdit(),
        ):
            QTimer.singleShot(0, watched.selectAll)
        return super().eventFilter(watched, event)

    def _actualizar_area_restante(self):
        w = float(self.width.value())
        h = float(self.height.value())
        area_cm2 = (w * h) / 100.0
        self.area_preview.setText(f"{area_cm2:,.1f} cm²")

    def accept(self):
        new_w = float(self.width.value())
        new_h = float(self.height.value())
        orig_short, orig_long = sorted((self._orig_w, self._orig_h))
        new_short, new_long = sorted((new_w, new_h))

        if abs(new_short - orig_short) < 1e-4 and abs(new_long - orig_long) < 1e-4:
            QMessageBox.warning(
                self,
                "Medidas sin cambios",
                "Las dimensiones ingresadas son idénticas al retazo original.\n\n"
                "Mide el sobrante físico tras el corte e ingresa las medidas reales antes de guardar.",
            )
            self._foco_inicial()
            return

        if new_short > orig_short + 1e-4 or new_long > orig_long + 1e-4:
            answer = QMessageBox.question(
                self,
                "Medida mayor al retazo original",
                f"Las medidas ingresadas ({new_w:g} × {new_h:g} mm) superan las "
                f"dimensiones originales del retazo ({self._orig_w:g} × {self._orig_h:g} mm).\n\n"
                "¿Confirmas que la medida física es correcta?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._foco_inicial()
                return

        super().accept()

    def values(self):
        return {"ancho_mm": self.width.value(), "alto_mm": self.height.value()}


def elegir_accion_retazo_usado(inventory, remnant_id, parent=None, *, layout_result=None):
    remnant = next(
        (item for item in inventory.listar() if item["id"] == remnant_id), None
    )
    if remnant is None:
        raise KeyError(f"No existe el retazo {remnant_id}.")
    message = QMessageBox(parent)
    message.setWindowTitle("Retazo usado")
    message.setText(
        f"¿Qué deseas hacer con el retazo #{remnant_id} "
        f"({remnant['ancho_mm']:g} × {remnant['alto_mm']:g} mm · {remnant['material']})?"
    )
    remove = message.addButton(
        "Eliminar del inventario", QMessageBox.ButtonRole.DestructiveRole
    )
    update = message.addButton(
        "Actualizar tamaño sobrante", QMessageBox.ButtonRole.ActionRole
    )
    message.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
    message.exec()
    if message.clickedButton() is remove:
        inventory.eliminar(remnant_id)
        return "deleted"
    if message.clickedButton() is update:
        dialog = DialogoDimensionesRetazo(
            remnant, parent, layout_result=layout_result
        )
        if not dialog.exec():
            return None
        inventory.actualizar(
            remnant_id,
            **dialog.values(),
            material=remnant["material"],
            ubicacion=remnant["ubicacion"],
            notas=remnant["notas"],
        )
        return "updated"
    return None


class DialogoMateriales(QDialog):
    def __init__(self, inventory, parent=None):
        super().__init__(parent)
        self.inventory = inventory
        self.setWindowTitle("Catálogo de materiales")
        self.resize(500, 400)
        layout = QVBoxLayout(self)

        info = QLabel(
            "Incluye el código 45, 67 o 112 en el nombre del material (ej. '67 HUAHUANG') "
            "para vincularlo automáticamente con archivos FP45, FP67 o FP112."
        )
        info.setObjectName("mutedText")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.list = QListWidget(self)
        layout.addWidget(self.list, 1)

        self.name = QLineEdit(self)
        self.name.setPlaceholderText("Nombre del material (ej. 67 HUAHUANG)")
        layout.addWidget(self.name)

        self.compatibility_label = QLabel()
        self.compatibility_label.setStyleSheet("font-size: 9pt;")
        layout.addWidget(self.compatibility_label)

        actions = QHBoxLayout()
        add = QPushButton("Añadir")
        rename = QPushButton("Renombrar")
        remove = QPushButton("Eliminar")
        close = QPushButton("Cerrar")
        for button in (add, rename, remove, close):
            button.setObjectName("secondaryButton")
        add.clicked.connect(self._add)
        rename.clicked.connect(self._rename)
        remove.clicked.connect(self._remove)
        close.clicked.connect(self.accept)
        self.list.currentItemChanged.connect(self._on_current_item_changed)
        self.name.textChanged.connect(self._update_compatibility_hint)

        actions.addWidget(add)
        actions.addWidget(rename)
        actions.addWidget(remove)
        actions.addStretch(1)
        actions.addWidget(close)
        layout.addLayout(actions)
        self._refresh()
        self._update_compatibility_hint(self.name.text())

    def _on_current_item_changed(self, current, _previous=None):
        if current is None:
            return
        raw_name = current.data(Qt.ItemDataRole.UserRole) or current.text()
        self.name.setText(raw_name)

    def _update_compatibility_hint(self, text):
        clean = str(text or "").strip()
        if not clean:
            self.compatibility_label.setStyleSheet("color: #75847c; font-size: 9pt;")
            self.compatibility_label.setText("Escribe un nombre para verificar su código FP.")
            return
        fp_tag = _etiqueta_fp_material(clean)
        if fp_tag:
            self.compatibility_label.setStyleSheet(
                "color: #285b49; font-size: 9pt; font-weight: 700;"
            )
            self.compatibility_label.setText(f"Compatibilidad detectada: {fp_tag}")
        else:
            self.compatibility_label.setStyleSheet("color: #a86420; font-size: 9pt;")
            self.compatibility_label.setText(
                "Sin código 45 / 67 / 112: no se asignará automáticamente a archivos FP45, FP67 ni FP112."
            )

    def _refresh(self, selected=""):
        self.list.clear()
        selected_row = -1
        for row, material_name in enumerate(self.inventory.materiales()):
            fp_tag = _etiqueta_fp_material(material_name)
            badge = f"   [{fp_tag}]" if fp_tag else "   [Sin código FP]"
            item = QListWidgetItem(f"{material_name}{badge}")
            item.setData(Qt.ItemDataRole.UserRole, material_name)
            if not fp_tag:
                item.setForeground(QColor("#9a5b13"))
                item.setToolTip(
                    "Este material no incluye 45, 67 ni 112; solo se usará con archivos LEN sin tipo FP."
                )
            self.list.addItem(item)
            if material_name == selected:
                selected_row = row
        if selected_row >= 0:
            self.list.setCurrentRow(selected_row)
        elif self.list.count():
            self.list.setCurrentRow(0)

    def _confirmar_si_falta_codigo_fp(self, candidate_name):
        clean = str(candidate_name or "").strip()
        if not clean or _etiqueta_fp_material(clean) is not None:
            return True
        answer = QMessageBox.question(
            self,
            "Material sin código de fotopolímero",
            f"El nombre '{clean}' no incluye el código 45, 67 ni 112.\n\n"
            "Los archivos etiquetados como FP45, FP67 o FP112 no se vincularán "
            "automáticamente con este material.\n\n"
            "¿Deseas guardarlo de todos modos?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _add(self):
        candidate = self.name.text().strip()
        if candidate and not self._confirmar_si_falta_codigo_fp(candidate):
            return
        try:
            name = self.inventory.agregar_material(self.name.text())
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Material no añadido", str(error))
            return
        self.name.clear()
        self._refresh(name)

    def _rename(self):
        current = self.list.currentItem()
        if current is None:
            return
        raw_current = current.data(Qt.ItemDataRole.UserRole) or current.text()
        candidate = self.name.text().strip()
        if candidate and not self._confirmar_si_falta_codigo_fp(candidate):
            return
        try:
            name = self.inventory.actualizar_material(raw_current, self.name.text())
        except (ValueError, KeyError, OSError) as error:
            QMessageBox.warning(self, "Material no actualizado", str(error))
            return
        self._refresh(name)

    def _remove(self):
        current = self.list.currentItem()
        if current is None:
            return
        raw_current = current.data(Qt.ItemDataRole.UserRole) or current.text()
        try:
            self.inventory.eliminar_material(raw_current)
        except (ValueError, KeyError, OSError) as error:
            QMessageBox.warning(self, "Material no eliminado", str(error))
            return
        self.name.clear()
        self._refresh()


class DialogoInventarioRetazos(QDialog):
    def __init__(self, inventory, parent=None):
        super().__init__(parent)
        self.inventory = inventory
        self._all_remnants = []
        self.setWindowTitle("Inventario de retazos")
        self.resize(1040, 580)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Barra superior: Búsqueda + Filtro por Fotopolímero + Botón Primario Añadir
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText(
            "Buscar por #ID, medida (ej. 455), material, ubicación o nota..."
        )
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._aplicar_filtros)
        top_bar.addWidget(self.search_input, 1)

        top_bar.addWidget(QLabel("Calibre:"))
        self.fp_filter = QComboBox(self)
        self.fp_filter.setMinimumWidth(165)
        self.fp_filter.addItem("Todos", "ALL")
        self.fp_filter.addItem("FP45", "FP45")
        self.fp_filter.addItem("FP67", "FP67")
        self.fp_filter.addItem("FP112", "FP112")
        self.fp_filter.addItem("Sin código FP", "NONE")
        self.fp_filter.currentIndexChanged.connect(self._aplicar_filtros)
        top_bar.addWidget(self.fp_filter)

        self.add_button = QPushButton("+ Añadir retazo")
        self.add_button.setObjectName("primaryButton")
        self.add_button.clicked.connect(self._agregar)
        top_bar.addWidget(self.add_button)

        layout.addLayout(top_bar)

        # Tabla principal con 8 columnas y ordenamiento numérico real
        self.table = QTableWidget(0, 8, self)
        self.table.setHorizontalHeaderLabels(
            [
                "#ID",
                "Ancho físico",
                "Alto físico",
                "Área",
                "Material (FP)",
                "Ubicación",
                "Notas",
                "Ingreso",
            ]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)

        self.table.itemDoubleClicked.connect(lambda _item: self._editar())
        self.table.itemSelectionChanged.connect(self._actualizar_estado_botones)
        layout.addWidget(self.table, 1)

        # Franja de KPIs de almacén y aviso de estado
        kpi_frame = QFrame(self)
        kpi_frame.setStyleSheet(
            "QFrame { background: #eaf0eb; border: 1px solid #d7dfd9; border-radius: 4px; }"
        )
        kpi_layout = QHBoxLayout(kpi_frame)
        kpi_layout.setContentsMargins(10, 6, 10, 6)
        self.summary_label = QLabel()
        self.summary_label.setStyleSheet("color: #263830; font-weight: 700; border: 0;")
        self.feedback_label = QLabel()
        self.feedback_label.setStyleSheet("color: #285b49; font-weight: 700; border: 0;")
        kpi_layout.addWidget(self.summary_label, 1)
        kpi_layout.addWidget(self.feedback_label)
        layout.addWidget(kpi_frame)

        # Barra inferior de acciones operativas y de catálogo/CSV
        actions = QHBoxLayout()
        self.edit_button = QPushButton("Modificar")
        self.duplicate_button = QPushButton("Duplicar")
        self.remove_button = QPushButton("Marcar usado / sobrante…")
        self.materials_button = QPushButton("Materiales…")
        self.import_csv_button = QPushButton("Importar CSV")
        self.export_csv_button = QPushButton("Exportar CSV")
        self.csv_format_button = QPushButton("Formato CSV")
        close = QPushButton("Cerrar")

        self.edit_button.setToolTip("Editar medidas, material o ubicación (Doble clic en la fila)")
        self.duplicate_button.setToolTip("Crear copias a partir del retazo seleccionado")
        self.remove_button.setToolTip(
            "Descontar un corte parcial actualizando el sobrante o eliminar el retazo (Tecla Supr)"
        )
        self.import_csv_button.setToolTip(
            "Requiere ancho_mm, alto_mm y material. Las columnas pueden cambiar de orden."
        )
        self.export_csv_button.setToolTip(
            "Exporta el inventario actual (o una plantilla vacía lista para Excel) en formato CSV."
        )

        self.edit_button.clicked.connect(self._editar)
        self.duplicate_button.clicked.connect(self._duplicar)
        self.remove_button.clicked.connect(self._eliminar)
        self.materials_button.clicked.connect(self._administrar_materiales)
        self.import_csv_button.clicked.connect(self._importar_csv)
        self.export_csv_button.clicked.connect(self._exportar_csv)
        self.csv_format_button.clicked.connect(self._mostrar_formato_csv)
        close.clicked.connect(self.accept)

        for button in (
            self.edit_button,
            self.duplicate_button,
            self.remove_button,
            self.materials_button,
            self.import_csv_button,
            self.export_csv_button,
            self.csv_format_button,
            close,
        ):
            button.setObjectName("secondaryButton")

        actions.addWidget(self.edit_button)
        actions.addWidget(self.duplicate_button)
        actions.addWidget(self.remove_button)
        actions.addSpacing(14)
        actions.addWidget(self.materials_button)
        actions.addWidget(self.import_csv_button)
        actions.addWidget(self.export_csv_button)
        actions.addWidget(self.csv_format_button)
        actions.addStretch(1)
        actions.addWidget(close)
        layout.addLayout(actions)

        # Atajos de teclado naturales en la tabla
        delete_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.table)
        delete_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete_shortcut.activated.connect(self._eliminar)

        find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        find_shortcut.activated.connect(self.search_input.setFocus)

        self._refresh()

    def _selected_id(self):
        row = self.table.currentRow()
        if row < 0 or self.table.isRowHidden(row):
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        return int(item.data(Qt.ItemDataRole.UserRole))

    def _actualizar_estado_botones(self):
        has_selection = self._selected_id() is not None
        self.edit_button.setEnabled(has_selection)
        self.duplicate_button.setEnabled(has_selection)
        self.remove_button.setEnabled(has_selection)

    def _mostrar_aviso_temporal(self, text):
        self.feedback_label.setText(text)
        QTimer.singleShot(
            6000,
            lambda: self.feedback_label.clear()
            if self.feedback_label.text() == text
            else None,
        )

    def _refresh(self, selected_id=None):
        self._all_remnants = self.inventory.listar()
        sorting_was_enabled = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._all_remnants))

        for row, remnant in enumerate(self._all_remnants):
            rem_id = int(remnant["id"])
            width = float(remnant["ancho_mm"])
            height = float(remnant["alto_mm"])
            area_cm2 = (width * height) / 100.0
            area_m2 = (width * height) / 1_000_000.0
            material_name = str(remnant["material"])
            fp_tag = _etiqueta_fp_material(material_name)
            location = str(remnant.get("ubicacion", ""))
            notes = str(remnant.get("notas", ""))
            date_text, date_ts = _formatear_fecha_ingreso(remnant.get("creado_en"))

            id_item = _SortableTableWidgetItem(f"#{rem_id}")
            id_item.setData(Qt.ItemDataRole.UserRole, rem_id)
            id_item.setData(_SORT_ROLE, rem_id)
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            width_item = _SortableTableWidgetItem(f"{width:g} mm")
            width_item.setData(Qt.ItemDataRole.UserRole, rem_id)
            width_item.setData(_SORT_ROLE, width)
            width_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )

            height_item = _SortableTableWidgetItem(f"{height:g} mm")
            height_item.setData(_SORT_ROLE, height)
            height_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )

            area_item = _SortableTableWidgetItem(f"{area_cm2:,.1f} cm²")
            area_item.setData(_SORT_ROLE, area_cm2)
            area_item.setToolTip(f"{area_m2:.3f} m²")
            area_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )

            if fp_tag:
                mat_display = f"{material_name} ({fp_tag})"
                mat_item = _SortableTableWidgetItem(mat_display)
                mat_item.setForeground(QColor("#204e3d"))
                mat_item.setToolTip(f"Compatible con archivos {fp_tag}")
            else:
                mat_display = f"{material_name} (Sin FP)"
                mat_item = _SortableTableWidgetItem(mat_display)
                mat_item.setForeground(QColor("#9a5b13"))
                mat_item.setToolTip(
                    "El nombre del material no contiene 45, 67 ni 112; "
                    "no se asociará automáticamente con archivos etiquetados FP."
                )
            mat_item.setData(_SORT_ROLE, mat_display)
            mat_item.setData(Qt.ItemDataRole.UserRole, fp_tag or "NONE")

            loc_item = _SortableTableWidgetItem(location)
            loc_item.setData(_SORT_ROLE, location)

            notes_item = _SortableTableWidgetItem(notes)
            notes_item.setData(_SORT_ROLE, notes)

            date_item = _SortableTableWidgetItem(date_text)
            date_item.setData(_SORT_ROLE, date_ts or rem_id)
            date_item.setToolTip(f"Registrado: {remnant.get('creado_en', date_text)}")
            date_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            for column, item in enumerate(
                (
                    id_item,
                    width_item,
                    height_item,
                    area_item,
                    mat_item,
                    loc_item,
                    notes_item,
                    date_item,
                )
            ):
                self.table.setItem(row, column, item)

        self.table.setSortingEnabled(sorting_was_enabled)
        self._actualizar_contadores_filtro_fp()
        self._aplicar_filtros(selected_id=selected_id)

    def _actualizar_contadores_filtro_fp(self):
        counts = {"ALL": len(self._all_remnants), "FP45": 0, "FP67": 0, "FP112": 0, "NONE": 0}
        for remnant in self._all_remnants:
            tag = _etiqueta_fp_material(remnant["material"])
            counts[tag if tag in counts else "NONE"] += 1
        labels = {
            "ALL": f"Todos ({counts['ALL']})",
            "FP45": f"FP45 ({counts['FP45']})",
            "FP67": f"FP67 ({counts['FP67']})",
            "FP112": f"FP112 ({counts['FP112']})",
            "NONE": f"Sin código FP ({counts['NONE']})",
        }
        for idx in range(self.fp_filter.count()):
            code = self.fp_filter.itemData(idx)
            if code in labels:
                self.fp_filter.setItemText(idx, labels[code])

    def _aplicar_filtros(self, *_args, selected_id=None):
        if selected_id is None:
            selected_id = self._selected_id()
        query = self.search_input.text().strip().casefold()
        fp_code = self.fp_filter.currentData() or "ALL"

        visible_count = 0
        visible_area_cm2 = 0.0
        fp_breakdown = {"FP45": 0, "FP67": 0, "FP112": 0, "NONE": 0}
        first_visible_row = -1
        target_row = -1

        for row in range(self.table.rowCount()):
            id_item = self.table.item(row, 0)
            width_item = self.table.item(row, 1)
            height_item = self.table.item(row, 2)
            area_item = self.table.item(row, 3)
            mat_item = self.table.item(row, 4)
            loc_item = self.table.item(row, 5)
            notes_item = self.table.item(row, 6)
            if id_item is None or mat_item is None:
                continue

            row_fp = mat_item.data(Qt.ItemDataRole.UserRole) or "NONE"
            fp_matches = fp_code == "ALL" or row_fp == fp_code

            if query:
                searchable = " ".join(
                    (
                        id_item.text(),
                        width_item.text() if width_item else "",
                        height_item.text() if height_item else "",
                        mat_item.text(),
                        loc_item.text() if loc_item else "",
                        notes_item.text() if notes_item else "",
                    )
                ).casefold()
                text_matches = query in searchable
            else:
                text_matches = True

            visible = fp_matches and text_matches
            self.table.setRowHidden(row, not visible)
            if visible:
                visible_count += 1
                area_val = float(area_item.data(_SORT_ROLE) or 0.0) if area_item else 0.0
                visible_area_cm2 += area_val
                fp_breakdown[row_fp if row_fp in fp_breakdown else "NONE"] += 1
                if first_visible_row < 0:
                    first_visible_row = row
                if int(id_item.data(Qt.ItemDataRole.UserRole)) == selected_id:
                    target_row = row

        if target_row >= 0:
            self.table.selectRow(target_row)
        elif first_visible_row >= 0:
            self.table.selectRow(first_visible_row)
        else:
            self.table.clearSelection()

        total_count = len(self._all_remnants)
        prefix = (
            f"Mostrando {visible_count} de {total_count} retazos"
            if (query or fp_code != "ALL")
            else f"{total_count} retazo{'s' if total_count != 1 else ''} en inventario"
        )
        breakdown_str = (
            f"FP45: {fp_breakdown['FP45']}  |  "
            f"FP67: {fp_breakdown['FP67']}  |  "
            f"FP112: {fp_breakdown['FP112']}"
        )
        if fp_breakdown["NONE"]:
            breakdown_str += f"  |  Sin FP: {fp_breakdown['NONE']}"
        self.summary_label.setText(
            f"{prefix}   ·   Superficie: {visible_area_cm2:,.1f} cm²   ({breakdown_str})"
        )
        self._actualizar_estado_botones()

    def _agregar(self):
        available_materials = self.inventory.materiales()
        if not available_materials:
            QMessageBox.information(
                self,
                "Catálogo vacío",
                "Da de alta al menos un material antes de añadir retazos.",
            )
            self._administrar_materiales()
            available_materials = self.inventory.materiales()
            if not available_materials:
                return
        dialog = _DialogoDatosRetazo(
            available_materials,
            parent=self,
            inventory=self.inventory,
            allow_batch=True,
        )
        if not dialog.exec():
            return
        try:
            count = dialog.batch_count()
            if count > 1 and hasattr(self.inventory, "agregar_lote"):
                ids = self.inventory.agregar_lote(
                    **dialog.values(), cantidad=count
                )
                remnant_id = ids[-1]
                self._refresh(remnant_id)
                self._mostrar_aviso_temporal(
                    f"Registrados {len(ids)} retazos: #{ids[0]} a #{ids[-1]}"
                )
            else:
                remnant_id = self.inventory.agregar(**dialog.values())
                self._refresh(remnant_id)
                self._mostrar_aviso_temporal(f"Guardado como retazo #{remnant_id}")
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Retazo no guardado", str(error))

    def _editar(self):
        remnant_id = self._selected_id()
        if remnant_id is None:
            return
        remnant = next(
            (item for item in self.inventory.listar() if item["id"] == remnant_id),
            None,
        )
        if remnant is None:
            return
        dialog = _DialogoDatosRetazo(
            self.inventory.materiales(),
            remnant,
            self,
            inventory=self.inventory,
            allow_batch=False,
        )
        if not dialog.exec():
            return
        try:
            self.inventory.actualizar(remnant_id, **dialog.values())
        except (ValueError, OSError, KeyError) as error:
            QMessageBox.warning(self, "Retazo no actualizado", str(error))
            return
        self._refresh(remnant_id)
        self._mostrar_aviso_temporal(f"Retazo #{remnant_id} actualizado")

    def _duplicar(self):
        remnant_id = self._selected_id()
        if remnant_id is None:
            return
        remnant = next(
            (item for item in self.inventory.listar() if item["id"] == remnant_id),
            None,
        )
        if remnant is None:
            return
        dialog = _DialogoDatosRetazo(
            self.inventory.materiales(),
            remnant,
            self,
            inventory=self.inventory,
            allow_batch=True,
            is_duplicate=True,
        )
        if not dialog.exec():
            return
        try:
            count = dialog.batch_count()
            if count > 1 and hasattr(self.inventory, "agregar_lote"):
                ids = self.inventory.agregar_lote(
                    **dialog.values(), cantidad=count
                )
                new_id = ids[-1]
                self._refresh(new_id)
                self._mostrar_aviso_temporal(
                    f"Duplicados {len(ids)} retazos: #{ids[0]} a #{ids[-1]}"
                )
            else:
                new_id = self.inventory.agregar(**dialog.values())
                self._refresh(new_id)
                self._mostrar_aviso_temporal(f"Duplicado como retazo #{new_id}")
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Retazo no duplicado", str(error))

    def _eliminar(self):
        remnant_id = self._selected_id()
        if remnant_id is None:
            return
        try:
            action = elegir_accion_retazo_usado(self.inventory, remnant_id, self)
        except (KeyError, OSError, ValueError) as error:
            QMessageBox.warning(self, "Retazo no actualizado", str(error))
            return
        if action is None:
            return
        if action == "deleted":
            self._refresh()
            self._mostrar_aviso_temporal(f"Retazo #{remnant_id} eliminado")
        else:
            self._refresh(remnant_id)
            self._mostrar_aviso_temporal(
                f"Retazo #{remnant_id} actualizado con sobrante"
            )

    def _importar_csv(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Importar retazos",
            "",
            "Archivos CSV (*.csv)",
        )
        if not filename:
            return
        try:
            imported = self.inventory.importar_csv(filename)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "CSV no importado", str(error))
            return
        self._refresh()
        self._mostrar_aviso_temporal(f"{imported} retazos importados")
        QMessageBox.information(
            self,
            "Importación terminada",
            f"Se añadieron {imported} retazos al inventario.",
        )

    def _exportar_csv(self):
        default_name = "inventario_retazos.csv"
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Exportar inventario de retazos",
            default_name,
            "Archivos CSV (*.csv)",
        )
        if not filename:
            return
        try:
            exported = self.inventory.exportar_csv(filename)
        except OSError as error:
            QMessageBox.warning(self, "CSV no exportado", str(error))
            return
        if exported == 0:
            QMessageBox.information(
                self,
                "Plantilla CSV generada",
                "Se guardó una plantilla CSV con los encabezados oficiales lista para "
                "abrir en Excel, capturar medidas e importar de vuelta.",
            )
        else:
            self._mostrar_aviso_temporal(f"Exportados {exported} retazos a CSV")
            QMessageBox.information(
                self,
                "Exportación terminada",
                f"Se exportaron {exported} retazos al archivo:\n{filename}",
            )

    def _administrar_materiales(self):
        DialogoMateriales(self.inventory, self).exec()
        self._refresh()

    def _mostrar_formato_csv(self):
        QMessageBox.information(
            self,
            "Formato del CSV",
            "Encabezados obligatorios: ancho_mm, alto_mm y material. "
            "Opcionales: ubicacion y notas. Puedes cambiar el orden de las columnas; "
            "cada dato se lee por su encabezado.\n\n"
            "Sugerencia: Usa el botón 'Exportar CSV' para generar una plantilla "
            "instantánea compatible con Excel.\n\n"
            "Ejemplo con punto y coma:\n"
            "ancho_mm;alto_mm;material;ubicacion;notas\n"
            "320;230;67 HUAHUANG;Estante A;Sin daño\n\n"
            "Las medidas son milímetros positivos y el material debe existir en "
            "Materiales. Se acepta coma o punto y coma como separador.",
        )


class DialogoAcomodosRetazos(QDialog):
    def __init__(self, results, inventory, parent=None, summary=None):
        super().__init__(parent)
        self.inventory = inventory
        self.results = list(results)
        self.selected_result = None
        self.setWindowTitle("Acomodos en retazos")
        self.resize(1080, 620)
        layout = QVBoxLayout(self)
        if summary:
            summary_label = QLabel(summary)
            summary_label.setWordWrap(True)
            layout.addWidget(summary_label)
        if not self.results:
            layout.addWidget(
                QLabel("Ningún retazo disponible admite todas las piezas seleccionadas.")
            )
            close = QPushButton("Cerrar")
            close.clicked.connect(self.reject)
            layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
            return

        content = QHBoxLayout()
        self.table = QTableWidget(len(self.results), 5, self)
        self.table.setHorizontalHeaderLabels(
            [
                "Retazo físico",
                "Material",
                "Corte propuesto",
                "Grabados",
                "Aprovechamiento del retazo",
            ]
        )
        self.table.horizontalHeaderItem(4).setToolTip(
            "Área de grabados respecto al área útil completa del retazo, "
            "después de márgenes, pinza y tolerancia."
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)

        for row, result in enumerate(self.results):
            self.table.setItem(
                row,
                0,
                QTableWidgetItem(
                    f"#{result['retazo_id']} · "
                    f"{result['retazo_ancho']:g} × {result['retazo_alto']:g} mm"
                ),
            )
            self.table.setItem(row, 1, QTableWidgetItem(result["material"]))
            self.table.setItem(
                row,
                2,
                QTableWidgetItem(
                    f"{result.get('ancho_acomodo', result['ancho']):g} × "
                    f"{result.get('alto_acomodo', result['alto']):g} mm"
                ),
            )
            self.table.setItem(
                row,
                3,
                QTableWidgetItem(
                    str(
                        len(
                            result.get(
                                "archivos_asignados", result.get("grabados", [])
                            )
                        )
                    )
                ),
            )
            self.table.setItem(
                row,
                4,
                QTableWidgetItem(f"{result['aprovechamiento_retazo']:g}%"),
            )

        self.canvas = CanvasPrevisualizacion()
        self.canvas.setMinimumSize(400, 360)
        content.addWidget(self.table, 3)
        content.addWidget(self.canvas, 2)
        layout.addLayout(content, 1)

        buttons = QDialogButtonBox()
        self.use_button = buttons.addButton(
            "Mostrar acomodo", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.use_button.setEnabled(False)
        self.remove_button = buttons.addButton(
            "Marcar retazo usado", QDialogButtonBox.ButtonRole.DestructiveRole
        )
        self.remove_button.setEnabled(False)
        buttons.addButton("Cerrar", QDialogButtonBox.ButtonRole.RejectRole)
        self.table.currentCellChanged.connect(self._show_selected)
        self.use_button.clicked.connect(self._accept_selected)
        self.remove_button.clicked.connect(self._remove_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.table.selectRow(0)

    def _show_selected(self, row, column, previous_row, previous_column):
        self.selected_result = (
            self.results[row] if 0 <= row < len(self.results) else None
        )
        self.use_button.setEnabled(self.selected_result is not None)
        self.remove_button.setEnabled(self.selected_result is not None)
        self.canvas.actualizar_grafico(self.selected_result)

    def _accept_selected(self):
        if self.selected_result is not None:
            self.accept()

    def _remove_selected(self):
        if self.selected_result is None:
            return
        remnant_id = self.selected_result["retazo_id"]
        try:
            action = elegir_accion_retazo_usado(
                self.inventory,
                remnant_id,
                self,
                layout_result=self.selected_result,
            )
        except (KeyError, OSError, ValueError) as error:
            QMessageBox.warning(self, "Retazo no actualizado", str(error))
            return
        removed_row = self.table.currentRow()
        if action is None:
            return
        rows_to_remove = [
            index
            for index, result in enumerate(self.results)
            if result["retazo_id"] == remnant_id
        ]
        for index in reversed(rows_to_remove):
            self.table.removeRow(index)
        self.results = [
            result for result in self.results if result["retazo_id"] != remnant_id
        ]
        self.selected_result = None
        self.canvas.actualizar_grafico(None)
        self.use_button.setEnabled(False)
        self.remove_button.setEnabled(False)
        if not self.results:
            self.reject()
        else:
            next_row = min(max(removed_row, 0), len(self.results) - 1)
            self.table.selectRow(next_row)
            self._show_selected(next_row, 0, -1, 0)
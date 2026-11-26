from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class DialogoConfiguracion(QDialog):
    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferencias de producción")
        self.setMinimumWidth(560)
        self.settings = settings or QSettings("MTRemozar", "OptimizadorCDI")

        layout = QVBoxLayout(self)
        tabs = QTabWidget(self)

        production_page = QWidget(tabs)
        production_page.setObjectName("preferencesPage")
        production_form = QFormLayout(production_page)
        production_form.setSpacing(12)

        files_page = QWidget(tabs)
        files_page.setObjectName("preferencesPage")
        files_form = QFormLayout(files_page)
        files_form.setSpacing(12)

        machines_page = QWidget(tabs)
        machines_page.setObjectName("preferencesPage")
        machines_layout = QVBoxLayout(machines_page)
        machines_layout.setSpacing(10)

        calculation_page = QWidget(tabs)
        calculation_page.setObjectName("preferencesPage")
        calculation_form = QFormLayout(calculation_page)
        calculation_form.setSpacing(12)

        # --- Pestaña Producción ---
        self.tol_corte = QSpinBox()
        self.tol_corte.setRange(0, 100)
        self.tol_corte.setValue(int(self.settings.value("tol_corte", 3)))
        self.tol_corte.setSuffix(" mm")

        self.ratio_maximo = QDoubleSpinBox()
        self.ratio_maximo.setRange(1.0, 10.0)
        self.ratio_maximo.setDecimals(2)
        self.ratio_maximo.setSingleStep(0.25)
        self.ratio_maximo.setValue(float(self.settings.value("ratio_maximo", 3.0)))
        self.ratio_maximo.setSuffix(" : 1")
        self.ratio_maximo.setToolTip(
            "Limita la proporción máxima alto/ancho para evitar planchas demasiado estrechas."
        )

        self.tambor_w = QSpinBox()
        self.tambor_w.setRange(100, 5000)
        self.tambor_w.setValue(int(self.settings.value("tambor_w", 1067)))
        self.tambor_w.setSuffix(" mm")
        self.tambor_h = QSpinBox()
        self.tambor_h.setRange(100, 5000)
        self.tambor_h.setValue(int(self.settings.value("tambor_h", 1270)))
        self.tambor_h.setSuffix(" mm")
        drum_layout = QHBoxLayout()
        drum_layout.addWidget(QLabel("Ancho"))
        drum_layout.addWidget(self.tambor_w)
        drum_layout.addSpacing(10)
        drum_layout.addWidget(QLabel("Alto"))
        drum_layout.addWidget(self.tambor_h)

        drum_hint = QLabel(
            "Límite usado para calcular planchas parciales (independiente del tambor físico de cada máquina)."
        )
        drum_hint.setObjectName("mutedText")
        drum_hint.setWordWrap(True)

        production_form.addRow("Tolerancia de corte", self.tol_corte)
        production_form.addRow("Ratio máximo alto/ancho", self.ratio_maximo)
        production_form.addRow("Tamaño máx. placa parcial", drum_layout)
        production_form.addRow("", drum_hint)

        # --- Pestaña Archivos ---
        self.ruta_rip = QLineEdit(self.settings.value("ruta_rip", ""))
        self.ruta_rip.setReadOnly(True)
        browse = QPushButton("Examinar…")
        browse.clicked.connect(self.seleccionar_carpeta)
        folder_layout = QHBoxLayout()
        folder_layout.addWidget(self.ruta_rip, 1)
        folder_layout.addWidget(browse)
        files_form.addRow("Carpeta raíz de trabajos RIP", folder_layout)

        root_hint = QLabel(
            "Carpeta monitoreada donde diseño sube los .len pendientes (ej. carpeta 03). "
            "Al enviar un acomodo a una máquina, los archivos se moverán desde aquí."
        )
        root_hint.setObjectName("mutedText")
        root_hint.setWordWrap(True)
        files_form.addRow("", root_hint)

        # --- Pestaña Máquinas ---
        machines_intro = QLabel(
            "Configura el nombre, la carpeta de cola y el tamaño máximo físico del tambor "
            "para cada máquina CDI. Se usará al despachar acomodos."
        )
        machines_intro.setObjectName("mutedText")
        machines_intro.setWordWrap(True)
        machines_layout.addWidget(machines_intro)

        self._machine_controls = {}
        for slot in (1, 2):
            group = QGroupBox(f"Máquina {slot}")
            form = QFormLayout(group)
            form.setSpacing(8)

            name_edit = QLineEdit(
                str(self.settings.value(f"maquina_{slot}_nombre", f"Máquina {slot}"))
            )
            name_edit.setPlaceholderText(f"Ej. Máquina {slot}")

            path_edit = QLineEdit(
                str(self.settings.value(f"maquina_{slot}_ruta", ""))
            )
            path_edit.setReadOnly(True)
            path_edit.setPlaceholderText("Sin carpeta configurada")
            browse_btn = QPushButton("Examinar…")
            browse_btn.setObjectName("secondaryButton")
            browse_btn.clicked.connect(
                lambda _checked=False, s=slot: self._seleccionar_carpeta_maquina(s)
            )
            clear_btn = QPushButton("Limpiar")
            clear_btn.setObjectName("secondaryButton")
            clear_btn.clicked.connect(
                lambda _checked=False, edit=path_edit: edit.clear()
            )
            path_row = QHBoxLayout()
            path_row.addWidget(path_edit, 1)
            path_row.addWidget(browse_btn)
            path_row.addWidget(clear_btn)

            spin_w = QSpinBox()
            spin_w.setRange(100, 5000)
            spin_w.setValue(int(self.settings.value(f"maquina_{slot}_tambor_w", 1067)))
            spin_w.setSuffix(" mm")

            spin_h = QSpinBox()
            spin_h.setRange(100, 5000)
            spin_h.setValue(int(self.settings.value(f"maquina_{slot}_tambor_h", 1270)))
            spin_h.setSuffix(" mm")

            m_drum = QHBoxLayout()
            m_drum.addWidget(QLabel("Ancho"))
            m_drum.addWidget(spin_w)
            m_drum.addSpacing(10)
            m_drum.addWidget(QLabel("Alto"))
            m_drum.addWidget(spin_h)

            form.addRow("Nombre visible", name_edit)
            form.addRow("Carpeta destino", path_row)
            form.addRow("Tambor máximo", m_drum)
            machines_layout.addWidget(group)

            self._machine_controls[slot] = {
                "nombre": name_edit,
                "ruta": path_edit,
                "tambor_w": spin_w,
                "tambor_h": spin_h,
            }
        machines_layout.addStretch(1)

        # --- Pestaña Cálculos ---
        self.agrupar_tamanos_cercanos = QCheckBox(
            "Probar grabados de tamaño cercano (experimental)"
        )
        self.agrupar_tamanos_cercanos.setChecked(
            self.settings.value("agrupar_tamanos_cercanos", False, type=bool)
        )
        self.agrupar_tamanos_cercanos.setToolTip(
            "Añade grupos candidatos cuyos anchos y altos difieran como máximo "
            "la tolerancia elegida. Mantiene las medidas reales al calcular el acomodo."
        )
        calculation_form.addRow(
            "Coincidencia dimensional", self.agrupar_tamanos_cercanos
        )
        self.tolerancia_tamanos_cercanos = QComboBox()
        for tolerance in (1, 2, 3):
            self.tolerancia_tamanos_cercanos.addItem(f"{tolerance} mm", tolerance)
        tolerance_index = self.tolerancia_tamanos_cercanos.findData(
            int(self.settings.value("tolerancia_tamanos_cercanos", 1))
        )
        self.tolerancia_tamanos_cercanos.setCurrentIndex(max(0, tolerance_index))
        self.tolerancia_tamanos_cercanos.setEnabled(
            self.agrupar_tamanos_cercanos.isChecked()
        )
        self.agrupar_tamanos_cercanos.toggled.connect(
            self.tolerancia_tamanos_cercanos.setEnabled
        )
        calculation_form.addRow(
            "Diferencia máxima por dimensión", self.tolerancia_tamanos_cercanos
        )
        self.precalcular_sugerencias = QCheckBox("Precalcular sugerencias")
        self.precalcular_sugerencias.setChecked(
            self.settings.value("precalcular_sugerencias", False, type=bool)
        )
        self.precalcular_sugerencias.setToolTip(
            "Usa un proceso de baja prioridad. Se cancela al detectar actividad "
            "y los resultados se guardan solo si archivos y parámetros siguen iguales."
        )
        calculation_form.addRow("Rendimiento", self.precalcular_sugerencias)
        self.minutos_inactividad = QSpinBox()
        self.minutos_inactividad.setRange(1, 120)
        self.minutos_inactividad.setValue(
            int(self.settings.value("minutos_inactividad_precache", 5))
        )
        self.minutos_inactividad.setSuffix(" min")
        self.minutos_inactividad.setEnabled(self.precalcular_sugerencias.isChecked())
        self.precalcular_sugerencias.toggled.connect(
            self.minutos_inactividad.setEnabled
        )
        calculation_form.addRow(
            "Inactividad antes del precálculo", self.minutos_inactividad
        )
        self.historial_seleccion = QSpinBox()
        self.historial_seleccion.setRange(1, 100)
        self.historial_seleccion.setValue(
            int(self.settings.value("historial_seleccion", 25))
        )
        self.historial_seleccion.setSuffix(" cambios")
        self.historial_seleccion.setToolTip(
            "Cantidad de estados de selección que se pueden deshacer o rehacer."
        )
        calculation_form.addRow("Historial de selección", self.historial_seleccion)

        tabs.addTab(production_page, "Producción")
        tabs.addTab(files_page, "Archivos")
        tabs.addTab(machines_page, "Máquinas")
        tabs.addTab(calculation_page, "Cálculos")
        layout.addWidget(tabs, 1)

        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("Cancelar")
        cancel.setObjectName("secondaryButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Guardar")
        save.setObjectName("primaryButton")
        save.clicked.connect(self.guardar_y_cerrar)
        actions.addWidget(cancel)
        actions.addWidget(save)
        layout.addLayout(actions)

    def seleccionar_carpeta(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Seleccionar carpeta raíz del RIP", self.ruta_rip.text() or "C:/"
        )
        if folder:
            self.ruta_rip.setText(folder)

    def _seleccionar_carpeta_maquina(self, slot):
        controls = self._machine_controls[slot]
        start_dir = controls["ruta"].text() or self.ruta_rip.text() or "C:/"
        folder = QFileDialog.getExistingDirectory(
            self,
            f"Seleccionar carpeta destino para {controls['nombre'].text().strip() or f'Máquina {slot}'}",
            start_dir,
        )
        if folder:
            controls["ruta"].setText(folder)

    def guardar_y_cerrar(self):
        self.settings.setValue("tol_corte", self.tol_corte.value())
        self.settings.setValue("ratio_maximo", self.ratio_maximo.value())
        self.settings.setValue("tambor_w", self.tambor_w.value())
        self.settings.setValue("tambor_h", self.tambor_h.value())
        self.settings.setValue("ruta_rip", self.ruta_rip.text())
        for slot, controls in self._machine_controls.items():
            name = controls["nombre"].text().strip() or f"Máquina {slot}"
            self.settings.setValue(f"maquina_{slot}_nombre", name)
            self.settings.setValue(f"maquina_{slot}_ruta", controls["ruta"].text().strip())
            self.settings.setValue(f"maquina_{slot}_tambor_w", controls["tambor_w"].value())
            self.settings.setValue(f"maquina_{slot}_tambor_h", controls["tambor_h"].value())
        self.settings.setValue(
            "agrupar_tamanos_cercanos", self.agrupar_tamanos_cercanos.isChecked()
        )
        self.settings.setValue(
            "tolerancia_tamanos_cercanos",
            self.tolerancia_tamanos_cercanos.currentData(),
        )
        self.settings.setValue(
            "precalcular_sugerencias", self.precalcular_sugerencias.isChecked()
        )
        self.settings.setValue(
            "minutos_inactividad_precache", self.minutos_inactividad.value()
        )
        self.settings.setValue(
            "historial_seleccion", self.historial_seleccion.value()
        )
        self.accept()
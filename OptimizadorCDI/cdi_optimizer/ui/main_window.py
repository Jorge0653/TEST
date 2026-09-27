import os
import sys
from threading import Event

from PySide6.QtCore import (
    QEvent,
    QFileSystemWatcher,
    QObject,
    QRunnable,
    QSettings,
    QThreadPool,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QAction, QCloseEvent, QCursor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cdi_optimizer.core import (
    escanear_carpeta_rip,
    extraer_medidas_xmp,
    generar_alternativas_archivos,
    sugerir_combinaciones,
)
from .canvas import CanvasPrevisualizacion, ResultadoTab
from .preferences import DialogoConfiguracion
from .suggestions import DialogoSugerencias
from .theme import APP_STYLESHEET


def _asset_path(filename):
    bundle_root = getattr(
        sys,
        "_MEIPASS",
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    )
    return os.path.join(bundle_root, filename)


class PanelMargenes(QWidget):
    def __init__(self, settings):
        super().__init__()
        layout = QGridLayout(self)
        layout.setContentsMargins(2, 4, 2, 2)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)

        self.spin_top = self._spin(settings, "margen_superior", 7)
        self.spin_left = self._spin(settings, "margen_izquierdo", 7)
        self.spin_right = self._spin(settings, "margen_derecho", 7)
        self.spin_bottom = self._spin(settings, "pinza", 25)
        self.spin_bottom.setStyleSheet("background: #fff2ed; border-color: #e2b5a4;")

        center = QLabel("ÁREA\nÚTIL")
        center.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.setStyleSheet(
            "background: #eaf0eb; border: 1px dashed #9eafa4; border-radius: 4px; "
            "color: #648071; font-size: 8pt; font-weight: 700;"
        )
        center.setFixedSize(78, 62)
        layout.addWidget(self.spin_top, 0, 1)
        layout.addWidget(self.spin_left, 1, 0)
        layout.addWidget(center, 1, 1, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.spin_right, 1, 2)
        layout.addWidget(self.spin_bottom, 2, 1)
        for column in range(3):
            layout.setColumnStretch(column, 1)

    @staticmethod
    def _spin(settings, key, default):
        spin = QSpinBox()
        spin.setRange(0, 500)
        spin.setValue(int(settings.value(key, default)))
        spin.setSuffix(" mm")
        spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return spin


class _WorkerSignals(QObject):
    finished = Signal(dict)
    failed = Signal(str)


class _OptimizationWorker(QRunnable):
    def __init__(
        self,
        filenames,
        config,
        cancel_event,
        variant_batch=None,
        existing_alternatives=(),
    ):
        super().__init__()
        self.filenames = filenames
        self.config = config
        self.cancel_event = cancel_event
        self.variant_batch = variant_batch
        self.existing_alternatives = tuple(existing_alternatives)
        self.signals = _WorkerSignals()

    def run(self):
        try:
            self.signals.finished.emit(
                generar_alternativas_archivos(
                    self.filenames,
                    self.config,
                    self.cancel_event,
                    variant_batch=self.variant_batch,
                    existing_alternatives=self.existing_alternatives,
                )
            )
        except Exception as error:
            self.signals.failed.emit(str(error))


class _SuggestionWorker(QRunnable):
    def __init__(self, filenames, config, cancel_event):
        super().__init__()
        self.filenames = filenames
        self.config = config
        self.cancel_event = cancel_event
        self.signals = _WorkerSignals()

    def run(self):
        try:
            self.signals.finished.emit(
                sugerir_combinaciones(
                    self.filenames,
                    self.config,
                    cancel_event=self.cancel_event,
                )
            )
        except Exception as error:
            self.signals.failed.emit(str(error))


class _SolutionCard(QFrame):
    selected = Signal(int)

    def __init__(self, index, solution, parent=None):
        super().__init__(parent)
        self.index = index
        self.solution = solution
        self.setObjectName("solutionCard")
        self.setProperty("selected", False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.canvas = CanvasPrevisualizacion(compacto=True)
        self.canvas.setMinimumSize(120, 72)
        self.canvas.setMaximumSize(150, 90)
        self.canvas.actualizar_grafico(solution)
        layout.addWidget(self.canvas, 1)

        self.efficiency = QLabel(f"{solution['eficiencia']:g}%")
        self.efficiency.setObjectName("solutionEfficiency")
        self.efficiency.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.efficiency)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.canvas.installEventFilter(self)
        self.efficiency.installEventFilter(self)

    def eventFilter(self, watched, event):
        if (
            watched in (self.canvas, self.efficiency)
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self.selected.emit(self.index)
            return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.index)
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (
            Qt.Key.Key_Space,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        ):
            self.selected.emit(self.index)
            event.accept()
            return
        super().keyPressEvent(event)

    def set_selected(self, selected):
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class OptimizadorCDI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Optimizador CDI")
        self.setWindowIcon(QIcon(_asset_path("app.ico")))
        self.setMinimumSize(1180, 760)
        self.resize(1500, 920)
        self.settings = QSettings("MTRemozar", "OptimizadorCDI")
        self._updating_tree = False
        self._worker = None
        self._cancel_event = None
        self._close_after_cancel = False
        self._pending_folder_refresh = False
        self._variant_batch = 0
        self._more_variants_available = False
        self._results_stale = False
        self._ink_measurements_cache = {}
        self._hovered_ink_item = None
        self._ink_tooltip_timer = QTimer(self)
        self._ink_tooltip_timer.setSingleShot(True)
        self._ink_tooltip_timer.setInterval(300)
        self._ink_tooltip_timer.timeout.connect(self._mostrar_tooltip_tinta)
        self._folder_watcher = QFileSystemWatcher(self)
        self._folder_watcher.directoryChanged.connect(self._on_rip_directory_changed)
        self._folder_refresh_timer = QTimer(self)
        self._folder_refresh_timer.setSingleShot(True)
        self._folder_refresh_timer.setInterval(300)
        self._folder_refresh_timer.timeout.connect(self._refresh_rip_folder)
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self.setStyleSheet(APP_STYLESHEET)
        self._build_interface()
        self._create_actions()
        self.cargar_archivos_reales()

    def _build_interface(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(22, 18, 22, 12)
        root.setSpacing(14)

        header = QHBoxLayout()
        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo_pixmap = QPixmap(_asset_path("logo.png"))
        if not logo_pixmap.isNull():
            logo = QLabel()
            logo.setFixedSize(54, 54)
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            logo.setPixmap(
                logo_pixmap.scaled(
                    48,
                    48,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            brand.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)
        title = QLabel("Optimizador CDI")
        title.setObjectName("pageTitle")
        brand.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        brand.addStretch(1)
        header.addLayout(brand, 1)
        self.route_label = QLabel("Carpeta RIP sin configurar")
        self.route_label.setObjectName("mutedText")
        header.addWidget(self.route_label)
        self.settings_button = QPushButton("Preferencias")
        header.addWidget(self.settings_button)
        self.settings_button.clicked.connect(self.abrir_configuracion)
        root.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(18)
        root.addLayout(content, 1)

        content.addWidget(self._build_jobs_panel(), 1)
        content.addWidget(self._build_results_panel(), 4)
        content.addWidget(self._build_controls_panel(), 1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedSize(140, 12)
        self.progress_bar.setToolTip("Cálculo en curso")
        self.progress_bar.hide()
        self.statusBar().addPermanentWidget(self.progress_bar)
        self.statusBar().showMessage("Configura una carpeta RIP para comenzar.")

    def _build_jobs_panel(self):
        panel = QWidget()
        panel.setMinimumWidth(250)
        panel.setMaximumWidth(340)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)

        title_row = QHBoxLayout()
        title = QLabel("Trabajos RIP")
        title.setObjectName("sectionTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        self.refresh_button = QPushButton("Actualizar")
        self.refresh_button.setObjectName("secondaryButton")
        self.refresh_button.clicked.connect(self.cargar_archivos_reales)
        title_row.addWidget(self.refresh_button)
        layout.addLayout(title_row)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar trabajo o tinta…")
        self.search.textChanged.connect(self._filtrar_trabajos)
        layout.addWidget(self.search)

        actions = QHBoxLayout()
        self.select_visible_button = QPushButton("Seleccionar visibles")
        self.select_visible_button.setObjectName("secondaryButton")
        self.select_visible_button.clicked.connect(lambda: self._seleccionar_visibles(True))
        self.clear_button = QPushButton("Limpiar")
        self.clear_button.setObjectName("secondaryButton")
        self.clear_button.clicked.connect(lambda: self._seleccionar_visibles(False))
        actions.addWidget(self.select_visible_button)
        actions.addWidget(self.clear_button)
        layout.addLayout(actions)

        self.suggest_button = QPushButton("Sugerir combinaciones")
        self.suggest_button.setObjectName("primaryButton")
        self.suggest_button.setEnabled(False)
        self.suggest_button.clicked.connect(self.buscar_sugerencias)
        layout.addWidget(self.suggest_button)

        self.arbol_trabajos = QTreeWidget()
        self.arbol_trabajos.setHeaderHidden(True)
        self.arbol_trabajos.setUniformRowHeights(True)
        self.arbol_trabajos.setMouseTracking(True)
        self.arbol_trabajos.itemChanged.connect(self._on_item_changed)
        self.arbol_trabajos.itemEntered.connect(self._programar_tooltip_tinta)
        self.arbol_trabajos.itemDoubleClicked.connect(self.on_item_double_clicked)
        layout.addWidget(self.arbol_trabajos, 1)

        self.selection_count = QLabel("0 tintas seleccionadas")
        self.selection_count.setObjectName("selectionCount")
        layout.addWidget(self.selection_count)
        return panel

    def _build_results_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.preview_result = ResultadoTab("Opción seleccionada")
        layout.addWidget(self.preview_result, 1)
        return panel

    def _build_controls_panel(self):
        panel = QWidget()
        panel.setMinimumWidth(270)
        panel.setMaximumWidth(340)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        title = QLabel("Parámetros de producción")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        margin_group = QGroupBox("Márgenes y pinza")
        margin_layout = QVBoxLayout(margin_group)
        self.widget_margenes = PanelMargenes(self.settings)
        margin_layout.addWidget(self.widget_margenes)
        layout.addWidget(margin_group)

        spacing_group = QGroupBox("Separación mínima")
        spacing_layout = QFormLayout(spacing_group)
        self.spin_espaciado = QSpinBox()
        self.spin_espaciado.setRange(0, 100)
        self.spin_espaciado.setValue(int(self.settings.value("espaciado", 3)))
        self.spin_espaciado.setSuffix(" mm")
        self.spin_espaciado.valueChanged.connect(self._invalidar_resultados)
        spacing_layout.addRow("Margen por lado", self.spin_espaciado)
        layout.addWidget(spacing_group)

        rotation = QCheckBox("Permitir giro individual de 90°")
        rotation.setToolTip(
            "La placa final se orienta en retrato para que el lado de la pinza sea el corto."
        )
        rotation.setChecked(self.settings.value("rotacion", True, type=bool))
        rotation.toggled.connect(lambda value: self.settings.setValue("rotacion", value))
        rotation.toggled.connect(self._invalidar_resultados)
        layout.addWidget(rotation)
        self.rotation_checkbox = rotation

        for spin in (
            self.widget_margenes.spin_top,
            self.widget_margenes.spin_left,
            self.widget_margenes.spin_right,
            self.widget_margenes.spin_bottom,
        ):
            spin.valueChanged.connect(self._invalidar_resultados)

        self.calculate_button = QPushButton("CALCULAR ACOMODO")
        self.calculate_button.setObjectName("primaryButton")
        self.calculate_button.setMinimumHeight(48)
        self.calculate_button.clicked.connect(self.procesar_calculo)
        layout.addWidget(self.calculate_button)

        self.cancel_button = QPushButton("Cancelar cálculo")
        self.cancel_button.setObjectName("secondaryButton")
        self.cancel_button.clicked.connect(self.cancelar_calculo)
        self.cancel_button.hide()
        layout.addWidget(self.cancel_button)

        self.options_group = QGroupBox("Opciones")
        options_layout = QVBoxLayout(self.options_group)
        self.options_count = QLabel("Calcula para ver opciones")
        self.options_count.setObjectName("mutedText")
        options_layout.addWidget(self.options_count)
        self.options_grid = QGridLayout()
        self.options_grid.setContentsMargins(0, 0, 0, 0)
        self.options_grid.setHorizontalSpacing(6)
        self.options_grid.setVerticalSpacing(6)
        self.options_grid.setColumnStretch(0, 1)
        self.options_grid.setColumnStretch(1, 1)
        options_layout.addLayout(self.options_grid)
        self.more_variants_button = QPushButton("Buscar más variantes")
        self.more_variants_button.setObjectName("secondaryButton")
        self.more_variants_button.setToolTip(
            "Prueba otros perfiles de giro y proporción, sin repetir acomodos."
        )
        self.more_variants_button.setEnabled(False)
        self.more_variants_button.clicked.connect(self.buscar_mas_variantes)
        options_layout.addWidget(self.more_variants_button)
        layout.addWidget(self.options_group)
        self.solution_cards = []
        self.alternatives = []
        self._show_empty_options()

        note = QLabel("La alternativa de retazo combina dos placas independientes, alineadas por la base.")
        note.setObjectName("mutedText")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return panel

    def _show_empty_options(self):
        while self.options_grid.count():
            item = self.options_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        placeholder = QLabel("Las miniaturas aparecerán después del cálculo")
        placeholder.setObjectName("mutedText")
        placeholder.setWordWrap(True)
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setMinimumHeight(76)
        self.options_grid.addWidget(placeholder, 0, 0, 1, 2)
        self.solution_cards = []
        self._results_stale = False
        self._variant_batch = 0
        self._more_variants_available = False
        self.more_variants_button.setEnabled(False)

    def _create_actions(self):
        file_menu = self.menuBar().addMenu("Archivo")
        preferences = QAction("Preferencias…", self)
        preferences.setShortcut("Ctrl+P")
        preferences.triggered.connect(self.abrir_configuracion)
        refresh = QAction("Actualizar trabajos", self)
        refresh.setShortcut("F5")
        refresh.triggered.connect(self.cargar_archivos_reales)
        calculate = QAction("Calcular acomodo", self)
        calculate.setShortcut("Ctrl+Return")
        calculate.triggered.connect(self.procesar_calculo)
        self._preferences_action = preferences
        self._refresh_action = refresh
        self._calculate_action = calculate
        file_menu.addAction(preferences)
        file_menu.addAction(refresh)
        file_menu.addSeparator()
        file_menu.addAction(calculate)

    def cargar_archivos_reales(self):
        selected_paths = set(self._obtener_archivos_seleccionados())
        self._ink_tooltip_timer.stop()
        self._hovered_ink_item = None
        self._ink_measurements_cache.clear()
        saved_paths = self.settings.value("archivos_seleccionados", [])
        if isinstance(saved_paths, str):
            saved_paths = [saved_paths]
        selected_paths.update(saved_paths or [])
        self.alternatives = []
        self.preview_result.actualizar_grafico(None)
        self.options_count.setText("Calcula para ver opciones")
        self._show_empty_options()
        self._updating_tree = True
        self.arbol_trabajos.clear()
        folder = self.settings.value("ruta_rip", "")
        self._watch_rip_folder(folder)
        if not folder or not os.path.isdir(folder):
            self.route_label.setText("Carpeta RIP sin configurar")
            self.statusBar().showMessage("Selecciona la carpeta de salida del RIP en Preferencias.")
            self._updating_tree = False
            self._actualizar_resumen_seleccion()
            return

        self.route_label.setText(folder)
        jobs = escanear_carpeta_rip(folder)
        available_paths = {
            ink["ruta_completa"] for inks in jobs.values() for ink in inks
        }
        selected_paths.intersection_update(available_paths)
        ink_count = 0
        for job_name, inks in jobs.items():
            parent = QTreeWidgetItem(self.arbol_trabajos, [job_name])
            parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            parent.setCheckState(0, Qt.CheckState.Unchecked)
            for ink in inks:
                child = QTreeWidgetItem(parent, [ink["nombre_tinta"]])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Unchecked)
                child.setData(0, Qt.ItemDataRole.UserRole, ink["ruta_completa"])
                if ink["ruta_completa"] in selected_paths:
                    child.setCheckState(0, Qt.CheckState.Checked)
                ink_count += 1
            states = [parent.child(index).checkState(0) for index in range(parent.childCount())]
            if states and all(state == Qt.CheckState.Checked for state in states):
                parent.setCheckState(0, Qt.CheckState.Checked)
            elif any(state != Qt.CheckState.Unchecked for state in states):
                parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
            parent.setExpanded(True)
        self._updating_tree = False
        self._filtrar_trabajos(self.search.text())
        self._actualizar_resumen_seleccion()
        self.statusBar().showMessage(f"{len(jobs)} trabajos · {ink_count} archivos LEN encontrados")

    def _programar_tooltip_tinta(self, item, column):
        self._hovered_ink_item = item
        self._ink_tooltip_timer.start()

    def _mostrar_tooltip_tinta(self):
        item = self._hovered_ink_item
        if item is None or item.parent() is None:
            return
        pointer = self.arbol_trabajos.viewport().mapFromGlobal(QCursor.pos())
        if self.arbol_trabajos.itemAt(pointer) is not item:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not path:
            return
        if path not in self._ink_measurements_cache:
            self._ink_measurements_cache[path] = extraer_medidas_xmp(path)
        measurements = self._ink_measurements_cache[path]
        if measurements is None:
            tooltip = f"{item.text(0)}\nNo se pudieron leer las medidas del archivo LEN."
        else:
            tooltip = (
                f"{item.text(0)}\n"
                f"Medidas: {measurements['ancho_mm']:g} × "
                f"{measurements['alto_mm']:g} mm"
            )
            horizontal_dpi = measurements.get("resolucion_x_dpi")
            vertical_dpi = measurements.get("resolucion_y_dpi")
            if horizontal_dpi and vertical_dpi:
                resolution = (
                    f"{horizontal_dpi:g} DPI"
                    if horizontal_dpi == vertical_dpi
                    else f"{horizontal_dpi:g} × {vertical_dpi:g} DPI"
                )
                tooltip += f"\nResolución: {resolution}"
        QToolTip.showText(QCursor.pos(), tooltip, self.arbol_trabajos.viewport())

    def _watch_rip_folder(self, folder):
        watched = self._folder_watcher.directories()
        if watched:
            self._folder_watcher.removePaths(watched)
        if folder and os.path.isdir(folder):
            self._folder_watcher.addPath(folder)

    def _on_rip_directory_changed(self, folder):
        self._folder_refresh_timer.start()

    def _refresh_rip_folder(self):
        if self._worker is not None:
            self._pending_folder_refresh = True
            return
        self.cargar_archivos_reales()

    def _on_item_changed(self, item, column):
        if self._updating_tree:
            return
        self._updating_tree = True
        if item.parent() is None:
            checked = item.checkState(0) == Qt.CheckState.Checked
            for index in range(item.childCount()):
                item.child(index).setCheckState(
                    0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
        else:
            parent = item.parent()
            states = [parent.child(index).checkState(0) for index in range(parent.childCount())]
            if all(state == Qt.CheckState.Checked for state in states):
                parent.setCheckState(0, Qt.CheckState.Checked)
            elif any(state != Qt.CheckState.Unchecked for state in states):
                parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
            else:
                parent.setCheckState(0, Qt.CheckState.Unchecked)
        self._updating_tree = False
        self._marcar_resultados_desactualizados()
        self._actualizar_resumen_seleccion()

    def _seleccionar_visibles(self, selected):
        state = Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked
        self._updating_tree = True
        for parent_index in range(self.arbol_trabajos.topLevelItemCount()):
            parent = self.arbol_trabajos.topLevelItem(parent_index)
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                if not child.isHidden():
                    child.setCheckState(0, state)
            states = [parent.child(index).checkState(0) for index in range(parent.childCount())]
            if states and all(item_state == Qt.CheckState.Checked for item_state in states):
                parent.setCheckState(0, Qt.CheckState.Checked)
            elif any(item_state != Qt.CheckState.Unchecked for item_state in states):
                parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
            else:
                parent.setCheckState(0, Qt.CheckState.Unchecked)
        self._updating_tree = False
        self._marcar_resultados_desactualizados()
        self._actualizar_resumen_seleccion()

    def _marcar_resultados_desactualizados(self):
        if not self.alternatives or self._results_stale:
            return
        self._results_stale = True
        self._more_variants_available = False
        self._actualizar_estado_variantes()
        self.options_count.setText(
            f"{len(self.alternatives)} opciones · cálculo anterior"
        )
        self.statusBar().showMessage(
            "Selección modificada · el acomodo mostrado corresponde al cálculo anterior"
        )

    def _filtrar_trabajos(self, query):
        query = query.strip().casefold()
        for parent_index in range(self.arbol_trabajos.topLevelItemCount()):
            parent = self.arbol_trabajos.topLevelItem(parent_index)
            parent_matches = not query or query in parent.text(0).casefold()
            visible_child = False
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                visible = parent_matches or not query or query in child.text(0).casefold()
                child.setHidden(not visible)
                visible_child = visible_child or visible
            parent.setHidden(not visible_child)
            parent.setExpanded(bool(query) or parent.isExpanded())

    def _actualizar_resumen_seleccion(self):
        selected = self._obtener_archivos_seleccionados()
        count = len(selected)
        self.settings.setValue("archivos_seleccionados", selected)
        self.selection_count.setText(f"{count} tinta{'s' if count != 1 else ''} seleccionada{'s' if count != 1 else ''}")
        self.calculate_button.setEnabled(count > 0 and self._worker is None)
        self.suggest_button.setEnabled(
            len(self._obtener_archivos_disponibles()) > 1 and self._worker is None
        )

    def _invalidar_resultados(self):
        if not hasattr(self, "options_grid"):
            return
        if self.alternatives:
            self.alternatives = []
            self._results_stale = False
            self._variant_batch = 0
            self._more_variants_available = False
            self.preview_result.actualizar_grafico(None)
            self.options_count.setText("Parámetros modificados · calcula de nuevo")
            self._show_empty_options()

    def _set_busy(self, busy, busy_text="OPTIMIZANDO…"):
        self.progress_bar.setVisible(busy)
        for widget in (
            self.arbol_trabajos,
            self.search,
            self.refresh_button,
            self.select_visible_button,
            self.clear_button,
            self.suggest_button,
            self.settings_button,
            self.widget_margenes,
            self.spin_espaciado,
            self.rotation_checkbox,
            self.more_variants_button,
        ):
            widget.setEnabled(not busy)
        self._preferences_action.setEnabled(not busy)
        self._refresh_action.setEnabled(not busy)
        self._calculate_action.setEnabled(not busy)
        self.calculate_button.setText(busy_text if busy else "CALCULAR OPCIONES")
        self.cancel_button.setVisible(busy)
        self.cancel_button.setEnabled(busy)
        if not busy:
            if self._pending_folder_refresh:
                self._pending_folder_refresh = False
                QTimer.singleShot(0, self.cargar_archivos_reales)
            else:
                self._actualizar_resumen_seleccion()
            self._actualizar_estado_variantes()

    def _actualizar_estado_variantes(self):
        self.more_variants_button.setEnabled(
            self._worker is None
            and bool(self.alternatives)
            and not self._results_stale
            and self._more_variants_available
        )

    def _obtener_archivos_seleccionados(self):
        selected = []
        for parent_index in range(self.arbol_trabajos.topLevelItemCount()):
            parent = self.arbol_trabajos.topLevelItem(parent_index)
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                if child.checkState(0) == Qt.CheckState.Checked:
                    selected.append(child.data(0, Qt.ItemDataRole.UserRole))
        return selected

    def _obtener_archivos_disponibles(self):
        available = []
        for parent_index in range(self.arbol_trabajos.topLevelItemCount()):
            parent = self.arbol_trabajos.topLevelItem(parent_index)
            for child_index in range(parent.childCount()):
                path = parent.child(child_index).data(0, Qt.ItemDataRole.UserRole)
                if path:
                    available.append(path)
        return available

    def on_item_double_clicked(self, item, column):
        if item.parent() is not None:
            item.parent().setCheckState(0, Qt.CheckState.Checked)

    def _seleccionar_rutas(self, filenames):
        selected = set(filenames)
        self._updating_tree = True
        for parent_index in range(self.arbol_trabajos.topLevelItemCount()):
            parent = self.arbol_trabajos.topLevelItem(parent_index)
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                path = child.data(0, Qt.ItemDataRole.UserRole)
                child.setCheckState(
                    0,
                    Qt.CheckState.Checked if path in selected else Qt.CheckState.Unchecked,
                )
            states = [parent.child(index).checkState(0) for index in range(parent.childCount())]
            if states and all(state == Qt.CheckState.Checked for state in states):
                parent.setCheckState(0, Qt.CheckState.Checked)
            elif any(state != Qt.CheckState.Unchecked for state in states):
                parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
            else:
                parent.setCheckState(0, Qt.CheckState.Unchecked)
        self._updating_tree = False
        self._marcar_resultados_desactualizados()
        self._actualizar_resumen_seleccion()

    def buscar_sugerencias(self):
        filenames = self._obtener_archivos_disponibles()
        if len(filenames) < 2:
            return
        config = self._configuracion_actual()
        self.statusBar().showMessage(f"Buscando combinaciones entre {len(filenames)} archivos LEN…")
        self._cancel_event = Event()
        self._worker = _SuggestionWorker(filenames, config, self._cancel_event)
        self._worker.signals.finished.connect(self._on_sugerencias_encontradas)
        self._worker.signals.failed.connect(self._on_calculation_failed)
        self._set_busy(True, "BUSCANDO SUGERENCIAS…")
        self._thread_pool.start(self._worker)

    def _on_sugerencias_encontradas(self, result):
        self._worker = None
        self._cancel_event = None
        self._set_busy(False)
        if self._close_after_cancel:
            QTimer.singleShot(50, self.close)
            return
        if result.get("cancelado"):
            self.statusBar().showMessage("Búsqueda cancelada")
            return
        suggestions = result.get("sugerencias", [])
        suggestions = [
            suggestion
            for suggestion in suggestions
            if all(os.path.isfile(path) for path in suggestion["archivos"])
        ]
        if not suggestions:
            message = "No se encontró una combinación viable con las dimensiones y límites actuales."
            if result.get("errores"):
                message += "\n\n" + "\n".join(result["errores"])
            QMessageBox.information(self, "Sin sugerencias", message)
            return

        dialog = DialogoSugerencias(suggestions, self)
        if dialog.exec():
            filenames = dialog.archivos_seleccionados()
            self._seleccionar_rutas(filenames)
            self.statusBar().showMessage(
                f"Selección aplicada · {len(filenames)} grabados · calcula para ver el acomodo"
            )
        if result.get("errores"):
            QMessageBox.warning(self, "Archivos omitidos", "\n".join(result["errores"]))

    def abrir_configuracion(self):
        dialog = DialogoConfiguracion(self.settings, self)
        if dialog.exec():
            self.rotation_checkbox.setChecked(self.settings.value("rotacion", True, type=bool))
            self.cargar_archivos_reales()

    def _configuracion_actual(self):
        margin = self.widget_margenes
        config = {
            "margen_izquierdo": margin.spin_left.value(),
            "margen_derecho": margin.spin_right.value(),
            "margen_superior": margin.spin_top.value(),
            "pinza": margin.spin_bottom.value(),
            "espaciado": self.spin_espaciado.value(),
            "tolerancia": int(self.settings.value("tol_corte", 3)),
            "ratio_maximo": float(self.settings.value("ratio_maximo", 3.0)),
            "tambor_w": int(self.settings.value("tambor_w", 1067)),
            "tambor_h": int(self.settings.value("tambor_h", 1270)),
            "rotacion": self.rotation_checkbox.isChecked(),
        }
        for key in (
            "margen_izquierdo",
            "margen_derecho",
            "margen_superior",
            "pinza",
        ):
            self.settings.setValue(key, config[key])
        self.settings.setValue("espaciado", config["espaciado"])
        return config

    def procesar_calculo(self):
        filenames = self._obtener_archivos_seleccionados()
        if not filenames:
            return
        config = self._configuracion_actual()
        self._variant_batch = 0
        self._more_variants_available = False
        self._actualizar_estado_variantes()
        self.statusBar().showMessage(f"Generando alternativas para {len(filenames)} archivos LEN…")
        self._cancel_event = Event()
        self._worker = _OptimizationWorker(filenames, config, self._cancel_event)
        self._worker.signals.finished.connect(self._on_calculation_finished)
        self._worker.signals.failed.connect(self._on_calculation_failed)
        self._set_busy(True)
        self._thread_pool.start(self._worker)

    def _on_calculation_finished(self, result):
        self._worker = None
        self._cancel_event = None
        self._set_busy(False)
        if self._close_after_cancel:
            QTimer.singleShot(50, self.close)
            return
        if result.get("cancelado"):
            self.statusBar().showMessage("Cálculo cancelado")
            return
        errors = result.get("errores", [])
        alternatives = result.get("alternativas", [])
        self._mostrar_alternativas(alternatives)
        self._variant_batch = 0
        self._more_variants_available = result.get("hay_mas_variantes", False)
        self._actualizar_estado_variantes()
        self.statusBar().showMessage(f"Cálculo terminado · {len(alternatives)} opciones geométricas")
        if errors:
            QMessageBox.warning(self, "Archivos omitidos", "\n".join(errors))
        elif not alternatives:
            QMessageBox.warning(
                self,
                "Sin acomodo viable",
                "No hay acomodos dentro del ratio máximo y los límites actuales. "
                "Revisa el ratio máximo, las dimensiones del tambor y los márgenes.",
            )

    def _mostrar_alternativas(self, alternatives):
        self.alternatives = alternatives
        self._results_stale = False
        while self.options_grid.count():
            item = self.options_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.solution_cards = []
        if not alternatives:
            self._show_empty_options()
            self.options_count.setText("No se encontraron opciones válidas")
            self.preview_result.actualizar_grafico(None)
            return
        self.options_count.setText(f"{len(alternatives)} opciones")
        for index, solution in enumerate(alternatives, start=1):
            card = _SolutionCard(index, solution, self.options_group)
            card.selected.connect(self._seleccionar_alternativa)
            self.options_grid.addWidget(card, (index - 1) // 2, (index - 1) % 2)
            self.solution_cards.append(card)
        self._seleccionar_alternativa(1)

    def _anexar_alternativas(self, alternatives):
        if not alternatives:
            return
        selected_index = next(
            (
                card.index
                for card in self.solution_cards
                if card.property("selected")
            ),
            1,
        )
        first_index = len(self.alternatives) + 1
        self.alternatives.extend(alternatives)
        for index, solution in enumerate(alternatives, start=first_index):
            card = _SolutionCard(index, solution, self.options_group)
            card.selected.connect(self._seleccionar_alternativa)
            self.options_grid.addWidget(card, (index - 1) // 2, (index - 1) % 2)
            self.solution_cards.append(card)
        self.options_count.setText(f"{len(self.alternatives)} opciones")
        self._seleccionar_alternativa(selected_index)

    def buscar_mas_variantes(self):
        if (
            not self.alternatives
            or not self._more_variants_available
            or self._worker is not None
        ):
            return
        filenames = self._obtener_archivos_seleccionados()
        if not filenames:
            return
        config = self._configuracion_actual()
        self.statusBar().showMessage("Buscando variantes distintas de acomodo…")
        self._cancel_event = Event()
        self._worker = _OptimizationWorker(
            filenames,
            config,
            self._cancel_event,
            variant_batch=self._variant_batch,
            existing_alternatives=self.alternatives,
        )
        self._worker.signals.finished.connect(self._on_more_variants_found)
        self._worker.signals.failed.connect(self._on_calculation_failed)
        self._set_busy(True, "BUSCANDO VARIANTES…")
        self._thread_pool.start(self._worker)

    def _on_more_variants_found(self, result):
        self._worker = None
        self._cancel_event = None
        self._set_busy(False)
        if self._close_after_cancel:
            QTimer.singleShot(50, self.close)
            return
        if result.get("cancelado"):
            self.statusBar().showMessage("Búsqueda de variantes cancelada")
            return

        new_alternatives = result.get("alternativas", [])
        self._variant_batch += 1
        self._more_variants_available = result.get("hay_mas_variantes", False)
        if new_alternatives:
            self._anexar_alternativas(new_alternatives)
            self.statusBar().showMessage(
                f"{len(new_alternatives)} variantes nuevas · "
                f"{len(self.alternatives)} opciones en total"
            )
        elif self._more_variants_available:
            self.statusBar().showMessage(
                "Este perfil no produjo variantes nuevas; puedes probar el siguiente."
            )
        else:
            self.statusBar().showMessage(
                "No quedan variantes distintas dentro de los límites actuales."
            )
        self._actualizar_estado_variantes()
        if result.get("errores"):
            QMessageBox.warning(
                self, "Archivos omitidos", "\n".join(result["errores"])
            )

    def _seleccionar_alternativa(self, index):
        if not 1 <= index <= len(self.alternatives):
            return
        for card in self.solution_cards:
            card.set_selected(card.index == index)
        solution = self.alternatives[index - 1]
        self.preview_result.actualizar_grafico(solution)
        self.statusBar().showMessage(
            f"Opción {index} · {solution['ancho']:g} × {solution['alto']:g} mm · "
            f"{solution['eficiencia']:g}%"
        )

    def cancelar_calculo(self):
        if self._cancel_event is None:
            return
        self._cancel_event.set()
        self.cancel_button.setEnabled(False)
        self.statusBar().showMessage("Cancelando cálculo…")

    def closeEvent(self, event: QCloseEvent):
        self._folder_refresh_timer.stop()
        if self._worker is not None:
            self._close_after_cancel = True
            if self._cancel_event is not None:
                self._cancel_event.set()
            self._thread_pool.clear()
            event.ignore()
            self.statusBar().showMessage("Cancelando el cálculo antes de cerrar…")
            return
        if not self._thread_pool.waitForDone(1500):
            event.ignore()
            QTimer.singleShot(100, self.close)
            return
        watched = self._folder_watcher.directories()
        if watched:
            self._folder_watcher.removePaths(watched)
        event.accept()

    def _on_calculation_failed(self, message):
        self._worker = None
        self._cancel_event = None
        self._set_busy(False)
        if self._close_after_cancel:
            QTimer.singleShot(50, self.close)
            return
        self.statusBar().showMessage("El cálculo no pudo completarse.")
        QMessageBox.critical(self, "Error de optimización", message)
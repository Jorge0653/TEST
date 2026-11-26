import copy
import logging
import os
import re
import sqlite3
import sys
from collections import OrderedDict
from pathlib import Path
from threading import Event
from time import monotonic

from PySide6.QtCore import (
    QEvent,
    QFileSystemWatcher,
    QObject,
    QRunnable,
    QSettings,
    QStandardPaths,
    QThread,
    QThreadPool,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QAction, QCloseEvent, QCursor, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
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
from cdi_optimizer.core.dispatcher import (
    MaquinaDestino,
    extraer_rutas_acomodo,
    mover_archivos_a_maquina,
    preparar_envio,
    revertir_envio,
    validar_acomodo_para_maquina,
)
from cdi_optimizer.core.remnants import (
    InventarioRetazos,
    buscar_acomodos_en_retazos,
    sugerir_combinaciones_en_retazos,
)
from cdi_optimizer.core.scanner import _descomponer_nombre_len
from .canvas import CanvasPrevisualizacion, ResultadoTab
from .preferences import DialogoConfiguracion
from .remnants import (
    DialogoAcomodosRetazos,
    DialogoInventarioRetazos,
    elegir_accion_retazo_usado,
)
from .suggestions import DialogoSugerencias
from .theme import APP_STYLESHEET

_LOGGER = logging.getLogger("cdi_optimizer")


def _asset_path(filename):
    bundle_root = getattr(
        sys,
        "_MEIPASS",
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    )
    return os.path.join(bundle_root, filename)


def _suggestion_cache_key(filenames, config):
    files = []
    for filename in filenames:
        path = os.path.normcase(os.path.abspath(filename))
        try:
            stat = os.stat(path)
            fingerprint = (path, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        except OSError:
            fingerprint = (path, None, None, None)
        files.append(fingerprint)
    return tuple(sorted(files)), tuple(sorted(config.items()))


def _retazo_suggestions_cache_key(filenames, config, remnants, time_budget):
    inventory = tuple(
        sorted(
            (
                int(remnant["id"]),
                float(remnant["ancho_mm"]),
                float(remnant["alto_mm"]),
                str(remnant["material"]),
            )
            for remnant in remnants
        )
    )
    return (
        _suggestion_cache_key(filenames, config),
        inventory,
        round(float(time_budget), 3),
    )


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
    progress = Signal(str, int)
    partial = Signal(dict)
    elapsed = Signal(float)


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
                    progress_callback=lambda percent, message: self.signals.progress.emit(
                        message, percent
                    ),
                )
            )
        except Exception as error:
            _LOGGER.exception("Falló el cálculo de alternativas")
            self.signals.failed.emit(str(error))


class _SuggestionWorker(QRunnable):
    def __init__(self, filenames, config, cancel_event, background=False):
        super().__init__()
        self.filenames = filenames
        self.config = config
        self.cancel_event = cancel_event
        self.background = background
        self.signals = _WorkerSignals()

    def run(self):
        thread = QThread.currentThread()
        previous_priority = thread.priority()
        try:
            if self.background:
                thread.setPriority(QThread.Priority.LowPriority)
            started = monotonic()
            result = sugerir_combinaciones(
                self.filenames,
                self.config,
                cancel_event=self.cancel_event,
                progress_callback=lambda percent, message: self.signals.progress.emit(
                    message, percent
                ),
            )
            if not self.cancel_event.is_set():
                self.signals.elapsed.emit(monotonic() - started)
            self.signals.finished.emit(result)
        except Exception as error:
            _LOGGER.exception("Falló la búsqueda de sugerencias")
            self.signals.failed.emit(str(error))
        finally:
            if self.background:
                thread.setPriority(previous_priority)


class _RemnantWorker(QRunnable):
    def __init__(
        self,
        filenames,
        remnants,
        config,
        cancel_event,
        suggest=False,
        time_budget_seconds=120,
    ):
        super().__init__()
        self.filenames = list(filenames)
        self.remnants = list(remnants)
        self.config = dict(config)
        self.cancel_event = cancel_event
        self.suggest = suggest
        self.time_budget_seconds = time_budget_seconds
        self.signals = _WorkerSignals()

    def run(self):
        try:
            if self.suggest:
                result = sugerir_combinaciones_en_retazos(
                    self.filenames,
                    self.remnants,
                    self.config,
                    cancel_event=self.cancel_event,
                    progress_callback=lambda percent, message: self.signals.progress.emit(
                        message, percent
                    ),
                    partial_callback=self.signals.partial.emit,
                    time_budget_seconds=self.time_budget_seconds,
                )
            else:
                result = buscar_acomodos_en_retazos(
                    self.filenames,
                    self.remnants,
                    self.config,
                    cancel_event=self.cancel_event,
                    progress_callback=lambda percent, message: self.signals.progress.emit(
                        message, percent
                    ),
                )
            self.signals.finished.emit(result)
        except Exception as error:
            _LOGGER.exception("Falló la búsqueda de acomodos en retazos")
            self.signals.failed.emit(str(error))


class _RemnantProgressDialog(QDialog):
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sugerencias en retazos")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        self.count_label = QLabel("Propuestas encontradas: 0")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count_label.setStyleSheet("font-weight: 700;")
        layout.addWidget(self.count_label)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_button = QPushButton("Detener búsqueda")
        self.cancel_button.setObjectName("secondaryButton")
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        actions.addWidget(self.cancel_button)
        layout.addLayout(actions)

    def set_count(self, count):
        self.count_label.setText(f"Propuestas encontradas: {count}")

    def set_cancelling(self, count):
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Deteniendo…")
        self.count_label.setText(
            f"Propuestas encontradas: {count} · deteniendo búsqueda…"
        )


class _DialogoResumen(QDialog):
    def __init__(self, selected_paths, available_paths, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Resumen del cálculo")
        self.resize(760, 460)
        self.setStyleSheet(
            "QDialog { background: #f4f7f4; color: #263830; }"
            "QTableWidget { background: #ffffff; alternate-background-color: #f1f5f1;"
            " color: #263830; gridline-color: #d7dfd9; border: 1px solid #cbd5ce; }"
            "QHeaderView::section { background: #e8efea; color: #263830;"
            " border: 0; border-bottom: 1px solid #cbd5ce; padding: 6px; font-weight: 700; }"
            "QTableWidget::item:selected { background: #dceae1; color: #203b30; }"
        )

        jobs_by_ticket = {}
        for path, selected in [
            *((path, True) for path in selected_paths),
            *((path, False) for path in available_paths),
        ]:
            job_name = self._job_name(path)
            ticket, name = self._ticket_and_name(job_name)
            key = ("ticket", ticket) if ticket else ("job", job_name)
            row = jobs_by_ticket.setdefault(
                key,
                {"ticket": ticket, "names": set(), "selected": 0, "available": 0},
            )
            row["names"].add(name)
            row["selected" if selected else "available"] += 1
        jobs = [row for row in jobs_by_ticket.values() if row["selected"]]
        jobs.sort(
            key=lambda row: (
                row["ticket"],
                ", ".join(sorted(row["names"])).casefold(),
            )
        )

        layout = QVBoxLayout(self)
        total_inks = sum(row["selected"] for row in jobs)
        layout.addWidget(
            QLabel(
                f"{len(jobs)} papeletas/trabajos · {total_inks} tintas seleccionadas"
            )
        )
        self.table = QTableWidget(len(jobs), 3, self)
        self.table.setHorizontalHeaderLabels(
            ["Papeleta física", "Trabajo", "Tintas seleccionadas"]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )

        for row_index, job in enumerate(jobs):
            names = " / ".join(sorted(job["names"], key=str.casefold))
            values = (
                job["ticket"] or "Sin papeleta",
                names,
                f"{job['selected']} de {job['available'] or job['selected']}",
            )
            for column, value in enumerate(values):
                self.table.setItem(row_index, column, QTableWidgetItem(value))
        layout.addWidget(self.table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    @staticmethod
    def _job_name(path):
        job_name, _, _ = _descomponer_nombre_len(path)
        return job_name

    @staticmethod
    def _ticket_and_name(job):
        match = re.match(r"^(\d{5})(?:\s*-\s*|\s+)(.+)$", job)
        if match:
            return match.group(1), match.group(2).strip()
        return "", job


class _DialogoConfirmarEnvio(QDialog):
    def __init__(
        self,
        maquina,
        layout_data,
        rutas,
        drum_warnings,
        colisiones,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Enviar acomodo a {maquina.nombre}")
        self.setMinimumWidth(540)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        pw = float(layout_data.get("ancho_acomodo", layout_data.get("ancho", 0)))
        ph = float(layout_data.get("alto_acomodo", layout_data.get("alto", 0)))
        plate_count = len(layout_data.get("placas", [])) or 1

        summary = QLabel(
            f"<b>Destino:</b> {maquina.nombre}<br>"
            f"<b>Carpeta:</b> {maquina.ruta}<br>"
            f"<b>Acomodo:</b> {pw:g} × {ph:g} mm · {len(rutas)} archivo(s) LEN · "
            f"{plate_count} placa{'s' if plate_count != 1 else ''}<br>"
            f"<b>Tambor máximo de {maquina.nombre}:</b> "
            f"{maquina.tambor_w:g} × {maquina.tambor_h:g} mm"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        if drum_warnings:
            warn_box = QLabel("⚠ " + "\n⚠ ".join(drum_warnings))
            warn_box.setWordWrap(True)
            warn_box.setStyleSheet(
                "background: #fdecea; color: #9c2b23; border: 1px solid #e6a8a2; "
                "border-radius: 4px; padding: 8px; font-weight: 700;"
            )
            layout.addWidget(warn_box)

        if colisiones:
            coll_box = QLabel(
                f"Ya existen {len(colisiones)} archivo(s) con el mismo nombre en destino "
                f"({', '.join(colisiones[:5])}{'…' if len(colisiones) > 5 else ''}). "
                "Si continúas, se sobrescribirán."
            )
            coll_box.setWordWrap(True)
            coll_box.setStyleSheet(
                "background: #fff6e5; color: #8c5311; border: 1px solid #e5c38b; "
                "border-radius: 4px; padding: 8px;"
            )
            layout.addWidget(coll_box)

        note = QLabel(
            "Los archivos se moverán desde la carpeta raíz hacia esta máquina. "
            "Podrás revertir esta acción desde Edición → Deshacer último envío."
        )
        note.setObjectName("mutedText")
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox()
        confirm_text = (
            "Enviar de todos modos" if drum_warnings else f"Enviar a {maquina.nombre}"
        )
        self.confirm_btn = buttons.addButton(
            confirm_text, QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.confirm_btn.setObjectName("primaryButton")
        self.cancel_btn = buttons.addButton(
            "Cancelar", QDialogButtonBox.ButtonRole.RejectRole
        )
        self.cancel_btn.setObjectName("secondaryButton")
        if drum_warnings or colisiones:
            self.cancel_btn.setDefault(True)
            self.cancel_btn.setFocus()
        else:
            self.confirm_btn.setDefault(True)
        self.confirm_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)
        layout.addWidget(buttons)


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
            if not self.isEnabled():
                return True
            self.selected.emit(self.index)
            return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self.isEnabled():
                event.accept()
                return
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
            if not self.isEnabled():
                event.accept()
                return
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
        try:
            self._normal_suggestion_duration = max(
                0.1,
                float(self.settings.value("normal_suggestion_duration_seconds", 60)),
            )
        except (TypeError, ValueError):
            self._normal_suggestion_duration = 60.0
        self._remnant_progress_dialog = None
        self._remnant_partial_count = 0
        self._retazo_visualizado = None
        self._remnant_view_active = False
        data_directory = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppLocalDataLocation
        )
        self.remnant_inventory = None
        try:
            self.remnant_inventory = InventarioRetazos(
                Path(data_directory) / "retazos.sqlite"
            )
        except (OSError, sqlite3.Error):
            _LOGGER.exception("No se pudo abrir el inventario local de retazos")
        self._updating_tree = False
        self._worker = None
        self._cancel_event = None
        self._background_worker = None
        self._background_cancel_event = None
        self._background_cache_key = None
        self._background_requested = False
        self._background_progress = ("Preparando sugerencias…", 0)
        self._close_after_cancel = False
        self._pending_folder_refresh = False
        self._suppress_folder_watcher = False
        self._ultimo_envio = None
        self._variant_batch = 0
        self._more_variants_available = False
        self._results_stale = False
        self._selection_history = []
        self._selection_history_index = -1
        self._suggestions_cache = OrderedDict()
        self._remnant_suggestions_cache = OrderedDict()
        self._active_suggestions_key = None
        self._active_remnant_suggestion_key = None
        self._calculation_paths_snapshot = []
        self._calculation_available_snapshot = []
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
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(5 * 60 * 1000)
        self._idle_timer.timeout.connect(self._iniciar_precalculo_sugerencias)
        self._precache_enabled = False
        self._precache_idle_ms = 5 * 60 * 1000
        self._sincronizar_preferencias_precache()
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self.setStyleSheet(APP_STYLESHEET)
        self._build_interface()
        self._create_actions()
        self._sincronizar_maquinas_ui()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.cargar_archivos_reales()
        self._reiniciar_temporizador_inactividad()

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

        self.progress_panel = QWidget()
        progress_layout = QHBoxLayout(self.progress_panel)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(0)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setMinimumHeight(28)
        self.progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_bar.setFormat("Preparando cálculo… · %p%")
        self.progress_bar.setStyleSheet(
            "QProgressBar { text-align: center; font-weight: 700; }"
        )
        progress_layout.addWidget(self.progress_bar, 1)
        self.progress_panel.hide()
        root.addWidget(self.progress_panel)

        content = QHBoxLayout()
        content.setSpacing(18)
        root.addLayout(content, 1)

        content.addWidget(self._build_jobs_panel(), 1)
        content.addWidget(self._build_results_panel(), 4)
        content.addWidget(self._build_controls_panel(), 1)
        self.statusBar().showMessage("Configura una carpeta RIP para comenzar.")
        self.background_indicator = QLabel("Precalculando sugerencias…")
        self.background_indicator.setObjectName("mutedText")
        self.background_indicator.hide()
        self.statusBar().addPermanentWidget(self.background_indicator)

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
        self.preview_result.resumen_requested.connect(self._mostrar_resumen_calculo)
        self.preview_result.grabado_clicked.connect(self._seleccionar_tinta_en_arbol)
        self.preview_result.remnant_mode_changed.connect(
            self._cambiar_modo_visualizacion_retazo
        )
        self.preview_result.remnant_used_requested.connect(
            self._marcar_retazo_visualizado_usado
        )
        self.preview_result.remnant_exit_requested.connect(
            self.preview_result.salir_visualizacion_retazo
        )
        self.preview_result.enviar_maquina_requested.connect(
            self._enviar_acomodo_a_maquina
        )
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

        self.remnant_search_button = QPushButton("Buscar acomodo en retazos")
        self.remnant_search_button.setObjectName("secondaryButton")
        self.remnant_search_button.setToolTip(
            "Busca por separado acomodos que quepan en los retazos disponibles."
        )
        self.remnant_search_button.setEnabled(False)
        self.remnant_search_button.clicked.connect(self.buscar_acomodos_en_retazos)
        layout.addWidget(self.remnant_search_button)

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

        note = QLabel(
            "La alternativa de retazo combina dos placas independientes, alineadas por la base."
        )
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

        remnants_menu = self.menuBar().addMenu("Retazos")
        inventory = QAction("Administrar inventario…", self)
        inventory.triggered.connect(self.abrir_inventario_retazos)
        fit = QAction("Buscar acomodo en retazos", self)
        fit.triggered.connect(self.buscar_acomodos_en_retazos)
        suggest_remnants = QAction("Sugerir combinaciones en retazos", self)
        suggest_remnants.triggered.connect(self.sugerir_combinaciones_en_retazos)
        self._remnants_inventory_action = inventory
        self._remnants_fit_action = fit
        self._remnants_suggest_action = suggest_remnants
        remnants_menu.addAction(inventory)
        remnants_menu.addAction(fit)
        remnants_menu.addAction(suggest_remnants)

        edit_menu = self.menuBar().addMenu("Edición")
        undo = QAction("Deshacer selección", self)
        undo.setShortcut(QKeySequence.StandardKey.Undo)
        undo.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        undo.triggered.connect(lambda checked=False: self._deshacer_seleccion())
        redo = QAction("Rehacer selección", self)
        redo.setShortcut(QKeySequence.StandardKey.Redo)
        redo.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        redo.triggered.connect(lambda checked=False: self._rehacer_seleccion())
        self._undo_selection_action = undo
        self._redo_selection_action = redo
        self.arbol_trabajos.addAction(undo)
        self.arbol_trabajos.addAction(redo)
        edit_menu.addAction(undo)
        edit_menu.addAction(redo)

        edit_menu.addSeparator()
        undo_dispatch = QAction("Deshacer último envío", self)
        undo_dispatch.setEnabled(False)
        undo_dispatch.triggered.connect(self._deshacer_ultimo_envio)
        self._undo_dispatch_action = undo_dispatch
        edit_menu.addAction(undo_dispatch)

        self._reiniciar_historial_seleccion()

    def cargar_archivos_reales(self):
        self._cancelar_precalculo_sugerencias()
        self._reiniciar_temporizador_inactividad()
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
            self._reiniciar_historial_seleccion()
            return

        self.route_label.setText(folder)
        jobs = escanear_carpeta_rip(folder)
        available_paths = {
            ink["ruta_completa"] for inks in jobs.values() for ink in inks
        }
        selected_paths.intersection_update(available_paths)
        materials = {}
        for job_name, inks in jobs.items():
            for ink in inks:
                material = ink["material_fotopolimero"]
                materials.setdefault(material, {}).setdefault(job_name, []).append(ink)

        material_order = {"FP45": 0, "FP67": 1, "FP112": 2, None: 3}
        ink_count = 0
        for material, jobs_by_name in sorted(
            materials.items(),
            key=lambda item: (material_order.get(item[0], 4), item[0] or ""),
        ):
            material_name = material or "Sin tipo de fotopolímero"
            material_item = QTreeWidgetItem(self.arbol_trabajos, [material_name])
            material_item.setFlags(
                material_item.flags() & ~Qt.ItemFlag.ItemIsSelectable
            )
            for job_name, inks in jobs_by_name.items():
                parent = QTreeWidgetItem(material_item, [job_name])
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
                self._actualizar_estado_trabajo(parent)
                parent.setExpanded(True)
            material_item.setExpanded(True)
        self._updating_tree = False
        self._filtrar_trabajos(self.search.text())
        self._actualizar_resumen_seleccion()
        self._reiniciar_historial_seleccion()
        self.statusBar().showMessage(
            f"{len(jobs)} trabajos · {ink_count} archivos LEN encontrados"
        )

    def _iterar_trabajos(self):
        for material_index in range(self.arbol_trabajos.topLevelItemCount()):
            material = self.arbol_trabajos.topLevelItem(material_index)
            for job_index in range(material.childCount()):
                yield material.child(job_index)

    def _iterar_tintas(self):
        for parent in self._iterar_trabajos():
            for child_index in range(parent.childCount()):
                yield parent.child(child_index)

    @staticmethod
    def _actualizar_estado_trabajo(parent):
        states = [
            parent.child(index).checkState(0)
            for index in range(parent.childCount())
        ]
        if states and all(state == Qt.CheckState.Checked for state in states):
            parent.setCheckState(0, Qt.CheckState.Checked)
        elif any(state != Qt.CheckState.Unchecked for state in states):
            parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
        else:
            parent.setCheckState(0, Qt.CheckState.Unchecked)

    def _programar_tooltip_tinta(self, item, column):
        self._hovered_ink_item = item
        self._ink_tooltip_timer.start()

    def _seleccionar_tinta_en_arbol(self, path):
        for child in self._iterar_tintas():
            if child.data(0, Qt.ItemDataRole.UserRole) != path:
                continue
            parent = child.parent()
            material = parent.parent()
            if child.isHidden() or parent.isHidden() or material.isHidden():
                self.search.clear()
            material.setExpanded(True)
            parent.setExpanded(True)
            self.arbol_trabajos.setCurrentItem(child)
            self.arbol_trabajos.scrollToItem(child)
            self.arbol_trabajos.setFocus(Qt.FocusReason.OtherFocusReason)
            return

    def _mostrar_tooltip_tinta(self):
        item = self._hovered_ink_item
        if item is None or not item.data(0, Qt.ItemDataRole.UserRole):
            return
        pointer = self.arbol_trabajos.viewport().mapFromGlobal(QCursor.pos())
        if self.arbol_trabajos.itemAt(pointer) is not item:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not path:
            return
        measurements = extraer_medidas_xmp(path)
        if measurements is None:
            tooltip = f"{item.text(0)}\nNo se pudieron leer las medidas del archivo LEN."
        else:
            lines = [
                f"{item.text(0)}",
                f"Medidas: {measurements['ancho_mm']:g} × {measurements['alto_mm']:g} mm",
            ]
            horizontal_dpi = measurements.get("resolucion_x_dpi")
            vertical_dpi = measurements.get("resolucion_y_dpi")
            if horizontal_dpi and vertical_dpi:
                resolution = (
                    f"{horizontal_dpi:g} DPI"
                    if horizontal_dpi == vertical_dpi
                    else f"{horizontal_dpi:g} × {vertical_dpi:g} DPI"
                )
                lines.append(f"Resolución: {resolution}")
            if measurements.get("tinta_nombre"):
                lines.append(f"Tinta XMP: {measurements['tinta_nombre']}")
            screen_parts = []
            if measurements.get("lineaje_lpi") is not None:
                screen_parts.append(f"{measurements['lineaje_lpi']:g} LPI")
            if measurements.get("angulo_grados") is not None:
                screen_parts.append(f"{measurements['angulo_grados']:g}°")
            if screen_parts:
                lines.append(f"Trama: {' · '.join(screen_parts)}")
            if measurements.get("punto_trama"):
                lines.append(f"Punto: {measurements['punto_trama']}")
            tooltip = "\n".join(lines)
        QToolTip.showText(QCursor.pos(), tooltip, self.arbol_trabajos.viewport())

    def eventFilter(self, watched, event):
        user_event = event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.KeyPress,
            QEvent.Type.Wheel,
            QEvent.Type.MouseMove,
        )
        suggest_activation = watched is getattr(self, "suggest_button", None) and (
            event.type() == QEvent.Type.MouseButtonPress
            or (
                event.type() == QEvent.Type.KeyPress
                and event.key()
                in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)
            )
        )
        if user_event:
            if self._background_worker is not None and not suggest_activation:
                self._cancelar_precalculo_sugerencias()
            self._reiniciar_temporizador_inactividad()
        return super().eventFilter(watched, event)

    def _sincronizar_preferencias_precache(self):
        self._precache_enabled = self.settings.value(
            "precalcular_sugerencias", False, type=bool
        )
        idle_minutes = min(
            120,
            max(1, int(self.settings.value("minutos_inactividad_precache", 5))),
        )
        self._precache_idle_ms = idle_minutes * 60 * 1000
        self._idle_timer.setInterval(self._precache_idle_ms)

    def _reiniciar_temporizador_inactividad(self):
        self._idle_timer.stop()
        if (
            getattr(self, "_precache_enabled", False)
            and self._worker is None
            and self._background_worker is None
            and not self._close_after_cancel
        ):
            self._idle_timer.setInterval(
                getattr(self, "_precache_idle_ms", 5 * 60 * 1000)
            )
            self._idle_timer.start()

    def abrir_configuracion(self):
        dialog = DialogoConfiguracion(self.settings, self)
        if dialog.exec():
            self._sincronizar_preferencias_precache()
            self._sincronizar_maquinas_ui()
            self.rotation_checkbox.setChecked(
                self.settings.value("rotacion", True, type=bool)
            )
            self.cargar_archivos_reales()

    def _cancelar_precalculo_sugerencias(self):
        if self._background_cancel_event is not None:
            self._background_cancel_event.set()
            self.background_indicator.setText("Cancelando precálculo…")

    def _iniciar_precalculo_sugerencias(self):
        if (
            not self.settings.value("precalcular_sugerencias", False, type=bool)
            or self._worker is not None
            or self._background_worker is not None
            or self._pending_folder_refresh
        ):
            return
        filenames = self._obtener_archivos_disponibles()
        if len(filenames) < 2:
            return
        config = self._configuracion_actual()
        cache_key = _suggestion_cache_key(filenames, config)
        if cache_key in self._suggestions_cache:
            self._suggestions_cache.move_to_end(cache_key)
            return

        self._background_cache_key = cache_key
        self._background_cancel_event = Event()
        self._background_requested = False
        self._background_progress = ("Preparando sugerencias…", 0)
        worker = _SuggestionWorker(
            filenames,
            config,
            self._background_cancel_event,
            background=True,
        )
        self._background_worker = worker
        worker.signals.finished.connect(self._on_background_sugerencias_terminadas)
        worker.signals.failed.connect(self._on_background_sugerencias_fallidas)
        worker.signals.progress.connect(self._on_background_sugerencias_progreso)
        worker.signals.elapsed.connect(self._registrar_duracion_sugerencias)
        self.background_indicator.setText("Precalculando sugerencias…")
        self.background_indicator.setToolTip(
            "Cálculo de baja prioridad; se cancela al usar la aplicación."
        )
        self.background_indicator.show()
        self._thread_pool.start(worker)

    def _on_background_sugerencias_progreso(self, message, percent):
        self._background_progress = (message, percent)
        if self._background_requested:
            self._actualizar_progreso(message, percent)
        elif self._worker is None:
            self.background_indicator.setText(
                f"Precalculando sugerencias · {percent}%"
            )
            self.background_indicator.setToolTip(message)

    def _on_background_sugerencias_terminadas(self, result):
        cache_key = self._background_cache_key
        requested = self._background_requested
        self._background_worker = None
        self._background_cancel_event = None
        self._background_cache_key = None
        self._background_requested = False
        self.background_indicator.hide()

        if result.get("cancelado"):
            if requested:
                self._worker = None
                self._cancel_event = None
                self._active_suggestions_key = None
                self._set_busy(False)
                self.statusBar().showMessage("Búsqueda de sugerencias cancelada")
            self._reiniciar_temporizador_inactividad()
            return

        current_key = _suggestion_cache_key(
            self._obtener_archivos_disponibles(), self._configuracion_actual()
        )
        if cache_key == current_key:
            self._suggestions_cache[cache_key] = copy.deepcopy(result)
            self._suggestions_cache.move_to_end(cache_key)
            while len(self._suggestions_cache) > 4:
                self._suggestions_cache.popitem(last=False)

        if requested:
            self._worker = None
            self._cancel_event = None
            self._active_suggestions_key = cache_key
            self._on_sugerencias_encontradas(result)
        else:
            self.statusBar().showMessage(
                "Sugerencias precalculadas; listas para consultar"
            )
        self._reiniciar_temporizador_inactividad()

    def _on_background_sugerencias_fallidas(self, message):
        requested = self._background_requested
        self._background_worker = None
        self._background_cancel_event = None
        self._background_cache_key = None
        self._background_requested = False
        self.background_indicator.hide()
        if requested:
            self._worker = None
            self._cancel_event = None
            self._active_suggestions_key = None
            self._set_busy(False)
            QMessageBox.critical(self, "Error de optimización", message)
        else:
            self.statusBar().showMessage("No se pudo completar el precálculo")
        self._reiniciar_temporizador_inactividad()

    def _watch_rip_folder(self, folder):
        watched = self._folder_watcher.directories()
        if watched:
            self._folder_watcher.removePaths(watched)
        if folder and os.path.isdir(folder):
            self._folder_watcher.addPath(folder)

    def _on_rip_directory_changed(self, folder):
        if self._suppress_folder_watcher:
            return
        self._folder_refresh_timer.start()

    def _refresh_rip_folder(self):
        if self._worker is not None:
            self._pending_folder_refresh = True
            return
        self.cargar_archivos_reales()

    def _on_item_changed(self, item, column):
        if self._updating_tree:
            return
        if item.parent() is None:
            return
        self._updating_tree = True
        if item.data(0, Qt.ItemDataRole.UserRole):
            self._actualizar_estado_trabajo(item.parent())
        else:
            checked = item.checkState(0) == Qt.CheckState.Checked
            for index in range(item.childCount()):
                item.child(index).setCheckState(
                    0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
            self._actualizar_estado_trabajo(item)
        self._updating_tree = False
        self._marcar_resultados_desactualizados()
        self._recordar_estado_seleccion()
        self._actualizar_resumen_seleccion()
        self.arbol_trabajos.setFocus(Qt.FocusReason.OtherFocusReason)

    def _seleccionar_visibles(self, selected):
        state = Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked
        self._updating_tree = True
        for parent in self._iterar_trabajos():
            material = parent.parent()
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                if not child.isHidden() and not parent.isHidden() and not material.isHidden():
                    child.setCheckState(0, state)
            self._actualizar_estado_trabajo(parent)
        self._updating_tree = False
        self._marcar_resultados_desactualizados()
        self._recordar_estado_seleccion()
        self._actualizar_resumen_seleccion()
        self.arbol_trabajos.setFocus(Qt.FocusReason.OtherFocusReason)

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
        for material_index in range(self.arbol_trabajos.topLevelItemCount()):
            material = self.arbol_trabajos.topLevelItem(material_index)
            material_matches = not query or query in material.text(0).casefold()
            visible_job = False
            for job_index in range(material.childCount()):
                parent = material.child(job_index)
                parent_matches = (
                    material_matches or not query or query in parent.text(0).casefold()
                )
                visible_child = False
                for child_index in range(parent.childCount()):
                    child = parent.child(child_index)
                    visible = (
                        parent_matches or not query or query in child.text(0).casefold()
                    )
                    child.setHidden(not visible)
                    visible_child = visible_child or visible
                parent.setHidden(not visible_child)
                parent.setExpanded(bool(query) or parent.isExpanded())
                visible_job = visible_job or visible_child
            material.setHidden(not visible_job)
            material.setExpanded(bool(query) or material.isExpanded())

    def _actualizar_resumen_seleccion(self):
        selected = self._obtener_archivos_seleccionados()
        count = len(selected)
        self.settings.setValue("archivos_seleccionados", selected)
        self.selection_count.setText(
            f"{count} tinta{'s' if count != 1 else ''} seleccionada{'s' if count != 1 else ''}"
        )
        self.calculate_button.setEnabled(
            count > 0 and self._worker is None and not self._remnant_view_active
        )
        self.remnant_search_button.setEnabled(
            count > 0
            and self._worker is None
            and self.remnant_inventory is not None
            and not self._remnant_view_active
        )
        self.suggest_button.setEnabled(
            len(self._obtener_archivos_disponibles()) > 1
            and self._worker is None
            and not self._remnant_view_active
        )
        self._actualizar_estado_historial()

    def _cambiar_modo_visualizacion_retazo(self, active):
        self._remnant_view_active = active
        enabled = not active and self._worker is None
        for widget in (
            self.widget_margenes,
            self.spin_espaciado,
            self.rotation_checkbox,
            self.calculate_button,
            self.suggest_button,
            self.remnant_search_button,
            self.options_group,
        ):
            widget.setEnabled(enabled)
        for card in self.solution_cards:
            card.setEnabled(enabled)
        self._calculate_action.setEnabled(enabled)
        self._remnants_fit_action.setEnabled(
            enabled and self.remnant_inventory is not None
        )
        self._remnants_suggest_action.setEnabled(
            enabled and self.remnant_inventory is not None
        )
        if not active:
            self._actualizar_resumen_seleccion()

    def _snapshot_seleccion(self):
        return tuple(
            sorted(
                self._obtener_archivos_seleccionados(),
                key=lambda path: os.path.normcase(os.path.abspath(path)),
            )
        )

    def _reiniciar_historial_seleccion(self):
        self._selection_history = [self._snapshot_seleccion()]
        self._selection_history_index = 0
        self._actualizar_estado_historial()

    def _recordar_estado_seleccion(self):
        snapshot = self._snapshot_seleccion()
        if (
            self._selection_history
            and snapshot == self._selection_history[self._selection_history_index]
        ):
            return
        del self._selection_history[self._selection_history_index + 1 :]
        self._selection_history.append(snapshot)
        limit = int(self.settings.value("historial_seleccion", 25))
        if len(self._selection_history) > limit + 1:
            del self._selection_history[: len(self._selection_history) - limit - 1]
        self._selection_history_index = len(self._selection_history) - 1
        self._actualizar_estado_historial()

    def _actualizar_estado_historial(self):
        if not hasattr(self, "_undo_selection_action"):
            return
        self._undo_selection_action.setEnabled(self._selection_history_index > 0)
        self._redo_selection_action.setEnabled(
            self._selection_history_index < len(self._selection_history) - 1
        )

    def _deshacer_seleccion(self):
        if self._selection_history_index <= 0:
            return
        self._selection_history_index -= 1
        self._seleccionar_rutas(
            self._selection_history[self._selection_history_index],
            record_history=False,
        )
        self._actualizar_estado_historial()

    def _rehacer_seleccion(self):
        if self._selection_history_index >= len(self._selection_history) - 1:
            return
        self._selection_history_index += 1
        self._seleccionar_rutas(
            self._selection_history[self._selection_history_index],
            record_history=False,
        )
        self._actualizar_estado_historial()

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
        self.progress_panel.setVisible(busy)
        if busy:
            self.progress_bar.setFormat(f"{busy_text} · %p%")
            self.progress_bar.setValue(0)
        for widget in (
            self.arbol_trabajos,
            self.search,
            self.refresh_button,
            self.select_visible_button,
            self.clear_button,
            self.suggest_button,
            self.remnant_search_button,
            self.settings_button,
            self.widget_margenes,
            self.spin_espaciado,
            self.rotation_checkbox,
            self.more_variants_button,
        ):
            widget.setEnabled(not busy)
        self._preferences_action.setEnabled(not busy)
        self._refresh_action.setEnabled(not busy)
        self._calculate_action.setEnabled(
            not busy and not self._remnant_view_active
        )
        self._remnants_inventory_action.setEnabled(not busy)
        self._remnants_fit_action.setEnabled(
            not busy and not self._remnant_view_active
        )
        self._remnants_suggest_action.setEnabled(
            not busy and not self._remnant_view_active
        )
        self.calculate_button.setEnabled(
            not busy
            and not self._remnant_view_active
            and bool(self._obtener_archivos_seleccionados())
        )
        self.options_group.setEnabled(not busy and not self._remnant_view_active)
        self.preview_result.set_dispatch_busy(busy)
        if hasattr(self, "_undo_dispatch_action"):
            self._undo_dispatch_action.setEnabled(
                not busy and self._ultimo_envio is not None
            )
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

    def _actualizar_progreso(self, message, percent):
        self.progress_bar.setValue(max(0, min(100, percent)))
        self.progress_bar.setFormat(f"{message} · %p%")

    def _actualizar_estado_variantes(self):
        self.more_variants_button.setEnabled(
            self._worker is None
            and bool(self.alternatives)
            and not self._results_stale
            and self._more_variants_available
        )

    def _obtener_archivos_seleccionados(self):
        selected = []
        for child in self._iterar_tintas():
            if child.checkState(0) == Qt.CheckState.Checked:
                selected.append(child.data(0, Qt.ItemDataRole.UserRole))
        return selected

    def _obtener_archivos_disponibles(self):
        available = []
        for child in self._iterar_tintas():
            path = child.data(0, Qt.ItemDataRole.UserRole)
            if path:
                available.append(path)
        return available

    def on_item_double_clicked(self, item, column):
        if item.data(0, Qt.ItemDataRole.UserRole):
            item.parent().setCheckState(0, Qt.CheckState.Checked)
        elif item.parent() is not None:
            item.setCheckState(0, Qt.CheckState.Checked)

    def _seleccionar_rutas(self, filenames, record_history=True):
        selected = set(filenames)
        self._updating_tree = True
        for parent in self._iterar_trabajos():
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                path = child.data(0, Qt.ItemDataRole.UserRole)
                child.setCheckState(
                    0,
                    Qt.CheckState.Checked if path in selected else Qt.CheckState.Unchecked,
                )
            self._actualizar_estado_trabajo(parent)
        self._updating_tree = False
        self._marcar_resultados_desactualizados()
        if record_history:
            self._recordar_estado_seleccion()
        self._actualizar_resumen_seleccion()
        self.arbol_trabajos.setFocus(Qt.FocusReason.OtherFocusReason)

    def buscar_sugerencias(self):
        filenames = self._obtener_archivos_disponibles()
        if len(filenames) < 2:
            return
        config = self._configuracion_actual()
        cache_key = _suggestion_cache_key(filenames, config)
        if cache_key in self._suggestions_cache:
            if (
                self._background_worker is not None
                and self._background_cache_key != cache_key
            ):
                self._cancelar_precalculo_sugerencias()
            self._suggestions_cache.move_to_end(cache_key)
            self._active_suggestions_key = cache_key
            self.statusBar().showMessage(
                "Archivos y parámetros sin cambios · mostrando sugerencias guardadas"
            )
            self._on_sugerencias_encontradas(
                copy.deepcopy(self._suggestions_cache[cache_key])
            )
            return
        if self._background_worker is not None:
            if self._background_cache_key == cache_key:
                self._background_requested = True
                self._active_suggestions_key = cache_key
                self._worker = self._background_worker
                self._cancel_event = self._background_cancel_event
                self.background_indicator.hide()
                self._set_busy(True, "TERMINANDO SUGERENCIAS…")
                message, percent = self._background_progress
                self._actualizar_progreso(message, percent)
                self.statusBar().showMessage(
                    "El precálculo ya estaba en marcha; mostrando su avance"
                )
                return
            self._cancelar_precalculo_sugerencias()
        self._active_suggestions_key = cache_key
        self.statusBar().showMessage(
            f"Buscando combinaciones entre {len(filenames)} archivos LEN…"
        )
        self._cancel_event = Event()
        self._worker = _SuggestionWorker(filenames, config, self._cancel_event)
        self._worker.signals.finished.connect(self._on_sugerencias_encontradas)
        self._worker.signals.failed.connect(self._on_calculation_failed)
        self._worker.signals.progress.connect(self._actualizar_progreso)
        self._worker.signals.elapsed.connect(self._registrar_duracion_sugerencias)
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
            self._active_suggestions_key = None
            self.statusBar().showMessage("Búsqueda cancelada")
            self._reiniciar_temporizador_inactividad()
            return
        if self._active_suggestions_key is not None:
            self._suggestions_cache[self._active_suggestions_key] = copy.deepcopy(result)
            self._suggestions_cache.move_to_end(self._active_suggestions_key)
            while len(self._suggestions_cache) > 4:
                self._suggestions_cache.popitem(last=False)
            self._active_suggestions_key = None
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
            self._reiniciar_temporizador_inactividad()
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
        self._reiniciar_temporizador_inactividad()

    def _registrar_duracion_sugerencias(self, duration):
        if duration <= 0:
            return
        self._normal_suggestion_duration = duration
        self.settings.setValue("normal_suggestion_duration_seconds", duration)

    def abrir_inventario_retazos(self):
        if self.remnant_inventory is None:
            QMessageBox.critical(
                self,
                "Inventario no disponible",
                "No se pudo abrir la base de datos local de retazos.",
            )
            return
        DialogoInventarioRetazos(self.remnant_inventory, self).exec()

    def buscar_acomodos_en_retazos(self):
        if self.remnant_inventory is None:
            return
        if self._worker is not None:
            return
        filenames = self._obtener_archivos_seleccionados()
        if not filenames:
            QMessageBox.information(
                self,
                "Selecciona grabados",
                "Selecciona al menos una tinta antes de buscar en retazos.",
            )
            return
        remnants = self._obtener_retazos_disponibles()
        if not remnants:
            return

        self._reiniciar_temporizador_inactividad()
        self._cancelar_precalculo_sugerencias()
        self._cancel_event = Event()
        self._worker = _RemnantWorker(
            filenames,
            remnants,
            self._configuracion_actual(),
            self._cancel_event,
        )
        self._worker.signals.finished.connect(self._on_retazo_search_finished)
        self._worker.signals.failed.connect(self._on_calculation_failed)
        self._worker.signals.progress.connect(self._actualizar_progreso)
        self._set_busy(True, "BUSCANDO ACOMODO EN RETAZOS…")
        self._thread_pool.start(self._worker)

    def sugerir_combinaciones_en_retazos(self):
        if self.remnant_inventory is None or self._worker is not None:
            return
        filenames = self._obtener_archivos_disponibles()
        if not filenames:
            QMessageBox.information(
                self, "Sin grabados", "No hay archivos LEN disponibles para buscar."
            )
            return
        remnants = self._obtener_retazos_disponibles()
        if not remnants:
            return

        self._reiniciar_temporizador_inactividad()
        self._cancelar_precalculo_sugerencias()
        config = self._configuracion_actual()
        time_budget = min(600.0, 2 * self._normal_suggestion_duration)
        cache_key = _retazo_suggestions_cache_key(
            filenames, config, remnants, time_budget
        )
        if cache_key in self._remnant_suggestions_cache:
            self._remnant_suggestions_cache.move_to_end(cache_key)
            self._active_remnant_suggestion_key = cache_key
            self.statusBar().showMessage(
                "Archivos, parámetros e inventario sin cambios · "
                "mostrando sugerencias en retazos guardadas"
            )
            self._on_retazo_suggestions_finished(
                copy.deepcopy(self._remnant_suggestions_cache[cache_key])
            )
            return
        self._active_remnant_suggestion_key = cache_key
        self._cancel_event = Event()
        self._remnant_partial_count = 0
        self._remnant_progress_dialog = _RemnantProgressDialog(self)
        self._remnant_progress_dialog.cancel_requested.connect(
            self.cancelar_calculo
        )
        self._worker = _RemnantWorker(
            filenames,
            remnants,
            config,
            self._cancel_event,
            suggest=True,
            time_budget_seconds=time_budget,
        )
        self._worker.signals.finished.connect(
            self._on_retazo_suggestions_finished
        )
        self._worker.signals.failed.connect(self._on_calculation_failed)
        self._worker.signals.progress.connect(self._actualizar_progreso)
        self._worker.signals.partial.connect(
            self._actualizar_resultados_parciales_retazos
        )
        self._set_busy(True, "SUGIRIENDO COMBINACIONES EN RETAZOS…")
        self._remnant_progress_dialog.show()
        self._thread_pool.start(self._worker)

    def _obtener_retazos_disponibles(self):
        remnants = self.remnant_inventory.listar()
        if not remnants:
            QMessageBox.information(
                self,
                "Inventario vacío",
                "Añade o importa retazos disponibles desde el menú Retazos.",
            )
            self.abrir_inventario_retazos()
            return None
        return remnants

    def _on_retazo_search_finished(self, result):
        self._worker = None
        self._cancel_event = None
        self._set_busy(False)
        if self._close_after_cancel:
            QTimer.singleShot(50, self.close)
            return
        if result.get("cancelado"):
            self.statusBar().showMessage("Búsqueda en retazos cancelada")
            self._reiniciar_temporizador_inactividad()
            return
        if result.get("errores"):
            QMessageBox.warning(self, "Archivos omitidos", "\n".join(result["errores"]))
        matches = result.get("resultados", [])
        if not matches:
            QMessageBox.information(
                self,
                "Sin acomodos en retazos",
                "Ningún retazo seleccionado admite todas las tintas con las dimensiones actuales.",
            )
        else:
            dialog = DialogoAcomodosRetazos(matches, self.remnant_inventory, self)
            if dialog.exec() and dialog.selected_result is not None:
                self._mostrar_retazo_en_lienzo(dialog.selected_result)
                self.statusBar().showMessage(
                    f"Acomodo en retazo #{dialog.selected_result['retazo_id']} · "
                    f"{dialog.selected_result['aprovechamiento_retazo']:g}% del retazo"
                )
        self._reiniciar_temporizador_inactividad()

    def _mostrar_retazo_en_lienzo(self, result):
        self._retazo_visualizado = result
        self.preview_result.mostrar_retazo(result)

    def _marcar_retazo_visualizado_usado(self):
        if self._retazo_visualizado is None or self.remnant_inventory is None:
            return
        remnant_id = self._retazo_visualizado["retazo_id"]
        try:
            action = elegir_accion_retazo_usado(
                self.remnant_inventory,
                remnant_id,
                self,
                layout_result=self._retazo_visualizado,
            )
        except (KeyError, OSError, ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "Retazo no actualizado", str(error))
            return
        if action is None:
            return
        self._retazo_visualizado = None
        self.preview_result.salir_visualizacion_retazo()
        action_text = (
            "eliminado del inventario"
            if action == "deleted"
            else "actualizado con las medidas del sobrante"
        )
        self.statusBar().showMessage(f"Retazo #{remnant_id} {action_text}")

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
            "agrupar_tamanos_cercanos": self.settings.value(
                "agrupar_tamanos_cercanos", False, type=bool
            ),
            "tolerancia_tamanos_cercanos": int(
                self.settings.value("tolerancia_tamanos_cercanos", 1)
            ),
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
        if self._remnant_view_active or self._worker is not None:
            return
        filenames = self._obtener_archivos_seleccionados()
        if not filenames:
            return
        config = self._configuracion_actual()
        self._calculation_paths_snapshot = list(filenames)
        self._calculation_available_snapshot = self._obtener_archivos_disponibles()
        self._variant_batch = 0
        self._more_variants_available = False
        self._actualizar_estado_variantes()
        self.statusBar().showMessage(
            f"Generando alternativas para {len(filenames)} archivos LEN…"
        )
        self._cancel_event = Event()
        self._worker = _OptimizationWorker(filenames, config, self._cancel_event)
        self._worker.signals.finished.connect(self._on_calculation_finished)
        self._worker.signals.failed.connect(self._on_calculation_failed)
        self._worker.signals.progress.connect(self._actualizar_progreso)
        self._set_busy(True)
        self._thread_pool.start(self._worker)

    def _mostrar_resumen_calculo(self):
        if self._remnant_view_active and self._retazo_visualizado is not None:
            selected_paths = self._retazo_visualizado.get("archivos_asignados") or [
                part.get("ruta")
                for part in self._retazo_visualizado.get("grabados", [])
                if part.get("ruta")
            ]
            available_paths = self._obtener_archivos_disponibles()
        else:
            selected_paths = self._calculation_paths_snapshot
            available_paths = self._calculation_available_snapshot
        if not selected_paths:
            return
        dialog = _DialogoResumen(
            selected_paths,
            available_paths,
            self,
        )
        dialog.exec()

    def _on_calculation_finished(self, result):
        self._worker = None
        self._cancel_event = None
        self._set_busy(False)
        if self._close_after_cancel:
            QTimer.singleShot(50, self.close)
            return
        if result.get("cancelado"):
            self.statusBar().showMessage("Cálculo cancelado")
            self._reiniciar_temporizador_inactividad()
            return
        errors = result.get("errores", [])
        alternatives = result.get("alternativas", [])
        self._mostrar_alternativas(alternatives)
        self._variant_batch = 0
        self._more_variants_available = result.get("hay_mas_variantes", False)
        self._actualizar_estado_variantes()
        self.statusBar().showMessage(
            f"Cálculo terminado · {len(alternatives)} opciones geométricas"
        )
        if errors:
            QMessageBox.warning(self, "Archivos omitidos", "\n".join(errors))
        elif not alternatives:
            QMessageBox.warning(
                self,
                "Sin acomodo viable",
                "No hay acomodos dentro del ratio máximo y los límites actuales. "
                "Revisa el ratio máximo, las dimensiones del tambor y los márgenes.",
            )
        self._reiniciar_temporizador_inactividad()

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
        self._worker.signals.progress.connect(self._actualizar_progreso)
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
            self._reiniciar_temporizador_inactividad()
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
        self._reiniciar_temporizador_inactividad()

    def _seleccionar_alternativa(self, index):
        if self._remnant_view_active or not 1 <= index <= len(self.alternatives):
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
        if self._remnant_progress_dialog is not None:
            self._remnant_progress_dialog.set_cancelling(
                self._remnant_partial_count
            )

    def closeEvent(self, event: QCloseEvent):
        self._folder_refresh_timer.stop()
        self._idle_timer.stop()
        self._cancelar_precalculo_sugerencias()
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
        self._active_suggestions_key = None
        self._active_remnant_suggestion_key = None
        if self._remnant_progress_dialog is not None:
            self._remnant_progress_dialog.close()
            self._remnant_progress_dialog.deleteLater()
            self._remnant_progress_dialog = None
        self._set_busy(False)
        if self._close_after_cancel:
            QTimer.singleShot(50, self.close)
            return
        self.statusBar().showMessage("El cálculo no pudo completarse.")
        QMessageBox.critical(self, "Error de optimización", message)
        self._reiniciar_temporizador_inactividad()

    def _on_retazo_suggestions_finished(self, result):
        self._worker = None
        self._cancel_event = None
        if self._remnant_progress_dialog is not None:
            self._remnant_progress_dialog.close()
            self._remnant_progress_dialog.deleteLater()
            self._remnant_progress_dialog = None
        self._set_busy(False)
        if self._close_after_cancel:
            QTimer.singleShot(50, self.close)
            return
        cache_key = self._active_remnant_suggestion_key
        self._active_remnant_suggestion_key = None
        if (
            cache_key is not None
            and not result.get("cancelado")
            and not result.get("tiempo_agotado")
            and not result.get("errores")
        ):
            current_key = _retazo_suggestions_cache_key(
                self._obtener_archivos_disponibles(),
                self._configuracion_actual(),
                self.remnant_inventory.listar(),
                result.get("tiempo_limite", 0),
            )
            if current_key == cache_key:
                self._remnant_suggestions_cache[cache_key] = copy.deepcopy(result)
                self._remnant_suggestions_cache.move_to_end(cache_key)
                while len(self._remnant_suggestions_cache) > 4:
                    self._remnant_suggestions_cache.popitem(last=False)
        if result.get("errores"):
            QMessageBox.warning(self, "Archivos omitidos", "\n".join(result["errores"]))
        assignments = result.get("resultados", [])
        total_count = result.get("total_grabados", 0)
        if not assignments:
            if result.get("cancelado"):
                message = "La búsqueda se canceló antes de encontrar propuestas."
            elif result.get("tiempo_agotado"):
                message = "Se alcanzó el límite de tiempo sin encontrar propuestas."
            else:
                message = "Ningún subconjunto cabe en los retazos disponibles."
            QMessageBox.information(
                self,
                "Sin acomodos en retazos",
                message,
            )
        else:
            state = "Búsqueda cancelada; " if result.get("cancelado") else ""
            if result.get("tiempo_agotado"):
                state = "Límite de tiempo alcanzado; "
            summary = (
                f"{state}{len(assignments)} propuestas para {total_count} grabados. "
                "El porcentaje usa la misma eficiencia exacta del acomodo normal."
            )
            dialog = DialogoAcomodosRetazos(
                assignments, self.remnant_inventory, self, summary=summary
            )
            if dialog.exec() and dialog.selected_result is not None:
                self._mostrar_retazo_en_lienzo(dialog.selected_result)
                self.statusBar().showMessage(
                    f"Acomodo sugerido en retazo #{dialog.selected_result['retazo_id']}"
                )
        self._reiniciar_temporizador_inactividad()

    def _actualizar_resultados_parciales_retazos(self, result):
        self._remnant_partial_count = result.get("propuestas_encontradas", 0)
        if self._remnant_progress_dialog is not None:
            self._remnant_progress_dialog.set_count(self._remnant_partial_count)

    def _obtener_maquinas_configuradas(self):
        machines = []
        for slot in (1, 2):
            machines.append(
                MaquinaDestino(
                    slot=slot,
                    nombre=str(
                        self.settings.value(f"maquina_{slot}_nombre", f"Máquina {slot}")
                    ).strip()
                    or f"Máquina {slot}",
                    ruta=str(self.settings.value(f"maquina_{slot}_ruta", "")).strip(),
                    tambor_w=float(self.settings.value(f"maquina_{slot}_tambor_w", 1067)),
                    tambor_h=float(self.settings.value(f"maquina_{slot}_tambor_h", 1270)),
                )
            )
        return machines

    def _sincronizar_maquinas_ui(self):
        self.preview_result.actualizar_maquinas(self._obtener_maquinas_configuradas())

    def _enviar_acomodo_a_maquina(self, slot):
        if self._worker is not None:
            return
        layout_data = self.preview_result.datos
        if not layout_data:
            return

        maquina = next(
            (m for m in self._obtener_maquinas_configuradas() if m.slot == slot),
            None,
        )
        if maquina is None or not maquina.configurada:
            QMessageBox.information(
                self,
                "Máquina sin configurar",
                "Configura la carpeta de destino de esta máquina en Preferencias → Máquinas.",
            )
            return

        rutas = extraer_rutas_acomodo(layout_data)
        if not rutas:
            QMessageBox.warning(
                self,
                "Sin archivos para enviar",
                "El acomodo visible no contiene rutas de archivos LEN válidas.",
            )
            return

        preflight = preparar_envio(rutas, maquina.ruta)
        if not preflight["valido"]:
            QMessageBox.warning(self, "No se puede enviar", preflight["error"])
            return

        drum_warnings = validar_acomodo_para_maquina(layout_data, maquina)
        dialog = _DialogoConfirmarEnvio(
            maquina,
            layout_data,
            rutas,
            drum_warnings,
            preflight["colisiones"],
            self,
        )
        if not dialog.exec():
            return

        was_remnant_view = self._remnant_view_active
        remnant_snapshot = self._retazo_visualizado

        self._cancelar_precalculo_sugerencias()
        self._folder_refresh_timer.stop()
        self._suppress_folder_watcher = True
        try:
            dispatch_result = mover_archivos_a_maquina(
                rutas,
                maquina.ruta,
                sobrescribir=bool(preflight["colisiones"]),
            )
        except (OSError, FileExistsError) as error:
            QMessageBox.critical(self, "Error al enviar archivos", str(error))
            return
        finally:
            self._suppress_folder_watcher = False

        self._ultimo_envio = {
            "maquina": maquina.nombre,
            "movidos": dispatch_result["movidos"],
        }
        self._undo_dispatch_action.setEnabled(True)

        if was_remnant_view and remnant_snapshot is not None:
            answer = QMessageBox.question(
                self,
                "Acomodo enviado",
                f"Se movieron {dispatch_result['cantidad']} archivo(s) a {maquina.nombre}.\n\n"
                f"¿Deseas marcar como usado el retazo #{remnant_snapshot['retazo_id']} ahora?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._marcar_retazo_visualizado_usado()

        self.cargar_archivos_reales()
        self.statusBar().showMessage(
            f"Enviados {dispatch_result['cantidad']} archivo(s) LEN a {maquina.nombre}"
        )

    def _deshacer_ultimo_envio(self):
        if self._worker is not None or self._ultimo_envio is None:
            return
        info = self._ultimo_envio
        answer = QMessageBox.question(
            self,
            "Deshacer último envío",
            f"¿Devolver los {len(info['movidos'])} archivo(s) enviados a {info['maquina']} "
            "de regreso a la carpeta raíz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._folder_refresh_timer.stop()
        self._suppress_folder_watcher = True
        try:
            count = revertir_envio(info["movidos"])
        except (OSError, FileNotFoundError, FileExistsError) as error:
            QMessageBox.warning(self, "No se pudo revertir", str(error))
            return
        finally:
            self._suppress_folder_watcher = False

        self._ultimo_envio = None
        self._undo_dispatch_action.setEnabled(False)
        self.cargar_archivos_reales()
        self.statusBar().showMessage(
            f"Se devolvieron {count} archivo(s) LEN a la carpeta raíz"
        )
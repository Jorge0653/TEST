from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont


class DialogoSugerencias(QDialog):
    def __init__(self, sugerencias, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Combinaciones sugeridas")
        self.setMinimumSize(920, 560)
        self.setStyleSheet(
            "QDialog { background: #f4f7f4; color: #263830; }"
            "QListWidget, QTreeWidget { background: #ffffff; color: #263830; border: 1px solid #cbd5ce;"
            " border-radius: 4px; padding: 4px; }"
            "QListWidget::item, QTreeWidget::item { min-height: 30px; padding: 5px; color: #263830; }"
            "QListWidget::item:selected, QTreeWidget::item:selected { background: #dceae1; color: #203b30; }"
            "QLabel#mutedText { color: #68776e; }"
        )
        self.sugerencias = sugerencias

        layout = QVBoxLayout(self)
        heading = QLabel("Propuestas de acomodo")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        intro = QLabel(
            "Selecciona una combinación para marcar sus archivos en la lista de trabajos. "
            "Después puedes revisar y calcular el acomodo."
        )
        intro.setObjectName("mutedText")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        columns = QHBoxLayout()
        self.lista_label = QLabel("Cantidad de grabados")
        self.lista_label.setObjectName("sectionTitle")
        self.archivos_label = QLabel("Grabados incluidos")
        self.archivos_label.setObjectName("sectionTitle")
        self.resolucion_label = QLabel()
        self.resolucion_label.setObjectName("mutedText")
        left_column = QVBoxLayout()
        right_column = QVBoxLayout()
        self.lista = QTreeWidget()
        self.lista.setHeaderHidden(True)
        self.lista.setUniformRowHeights(True)
        self.lista.setIndentation(18)
        self.lista.setMinimumWidth(440)
        self.archivos = QListWidget()
        left_column.addWidget(self.lista_label)
        left_column.addWidget(self.lista)
        right_column.addWidget(self.archivos_label)
        right_column.addWidget(self.resolucion_label)
        right_column.addWidget(self.archivos)
        columns.addLayout(left_column, 3)
        columns.addLayout(right_column, 2)
        layout.addLayout(columns, 1)

        options_by_count = {}
        for suggestion in sugerencias:
            options_by_count.setdefault(suggestion["cantidad"], []).append(suggestion)
        first_option = None
        for count, options in sorted(options_by_count.items()):
            group = QTreeWidgetItem([f"{count} grabados  ·  {len(options)} opciones"])
            group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            font = QFont(group.font(0))
            font.setWeight(QFont.Weight.DemiBold)
            group.setFont(0, font)
            self.lista.addTopLevelItem(group)
            for suggestion in options:
                text = (
                    f"Opción {suggestion.get('variante', 1)}     ·     "
                    f"{suggestion['eficiencia']:g}%     ·     "
                    f"{suggestion['ancho']:g} × {suggestion['alto']:g} mm"
                )
                item = QTreeWidgetItem([text])
                item.setData(0, Qt.ItemDataRole.UserRole, suggestion)
                group.addChild(item)
                if first_option is None:
                    first_option = item
            group.setExpanded(True)
        self.lista.currentItemChanged.connect(self._mostrar_archivos)

        buttons = QDialogButtonBox()
        self.use_button = buttons.addButton("Usar selección", QDialogButtonBox.ButtonRole.AcceptRole)
        self.use_button.setEnabled(first_option is not None)
        buttons.addButton("Cerrar", QDialogButtonBox.ButtonRole.RejectRole)
        self.use_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if first_option is not None:
            self.lista.setCurrentItem(first_option)

    def _mostrar_archivos(self, current, previous=None):
        self.archivos.clear()
        suggestion = (
            current.data(0, Qt.ItemDataRole.UserRole)
            if current is not None
            else None
        )
        self.use_button.setEnabled(suggestion is not None)
        if suggestion is not None:
            self.archivos.addItems(suggestion["nombres"])
            details = []
            resolution = suggestion.get("resolucion_dpi")
            if resolution is None:
                details.append("Resolución DPI no verificada")
            elif resolution[0] == resolution[1]:
                details.append(f"Resolución: {resolution[0]:g} DPI")
            else:
                details.append(
                    f"Resolución: {resolution[0]:g} × {resolution[1]:g} DPI"
                )
            photopolymer = suggestion.get("material_fotopolimero")
            if photopolymer:
                details.append(f"Fotopolímero: {photopolymer}")
            exact_matches = suggestion.get("coincidencias_exactas", 0)
            tolerance_matches = suggestion.get("coincidencias_tolerancia", 0)
            tolerance = suggestion.get("tolerancia_tamanos_mm", 0)
            if tolerance_matches:
                details.append(
                    f"Tamaños: {exact_matches} exactas · "
                    f"{tolerance_matches} dentro de ±{tolerance:g} mm"
                )
            else:
                details.append(f"Tamaños exactamente iguales: {exact_matches}")
            self.resolucion_label.setText("\n".join(details))
        else:
            self.resolucion_label.clear()

    def archivos_seleccionados(self):
        current = self.lista.currentItem()
        if current is None:
            return []
        suggestion = current.data(0, Qt.ItemDataRole.UserRole)
        return list(suggestion["archivos"]) if suggestion is not None else []
from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)
from PySide6.QtCore import QSettings


class DialogoConfiguracion(QDialog):
    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferencias de producción")
        self.setMinimumWidth(500)
        self.settings = settings or QSettings("MTRemozar", "OptimizadorCDI")

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(12)

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

        self.ruta_rip = QLineEdit(self.settings.value("ruta_rip", ""))
        self.ruta_rip.setReadOnly(True)
        browse = QPushButton("Examinar…")
        browse.clicked.connect(self.seleccionar_carpeta)
        folder_layout = QHBoxLayout()
        folder_layout.addWidget(self.ruta_rip, 1)
        folder_layout.addWidget(browse)

        form.addRow("Tolerancia de corte", self.tol_corte)
        form.addRow("Ratio máximo alto/ancho", self.ratio_maximo)
        form.addRow("Límites del tambor CDI", drum_layout)
        form.addRow("Carpeta de trabajos RIP", folder_layout)
        layout.addLayout(form)

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
            self, "Seleccionar carpeta del RIP", self.ruta_rip.text() or "C:/"
        )
        if folder:
            self.ruta_rip.setText(folder)

    def guardar_y_cerrar(self):
        self.settings.setValue("tol_corte", self.tol_corte.value())
        self.settings.setValue("ratio_maximo", self.ratio_maximo.value())
        self.settings.setValue("tambor_w", self.tambor_w.value())
        self.settings.setValue("tambor_h", self.tambor_h.value())
        self.settings.setValue("ruta_rip", self.ruta_rip.text())
        self.accept()
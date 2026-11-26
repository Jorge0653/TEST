APP_STYLESHEET = """
QMainWindow, QDialog { background: #f4f7f4; color: #263830; }
QWidget { font-family: 'Bahnschrift', 'Segoe UI', sans-serif; font-size: 10pt; color: #263830; }
QLabel#appEyebrow { color: #638174; font-size: 9pt; font-weight: 700; }
QLabel#pageTitle { color: #1e352c; font-size: 19pt; font-weight: 700; }
QLabel#sectionTitle { color: #263830; font-size: 11pt; font-weight: 700; }
QLabel#mutedText { color: #75847c; }
QLabel#resultSummary { color: #263830; font-size: 11pt; font-weight: 700; padding: 4px 2px; }
QLabel#selectionCount { color: #638174; font-size: 9pt; }
QFrame#dispatchedBanner {
    background: #e3f1e8; border: 1px solid #9ec4ae; border-radius: 5px;
}
QLabel#dispatchedBannerText { color: #1e4d3a; font-weight: 700; }
QLineEdit, QSpinBox, QDoubleSpinBox {
    background: #ffffff; border: 1px solid #cbd5ce; border-radius: 4px;
    padding: 7px 8px; selection-background-color: #3d7665;
}
QComboBox {
    background: #ffffff; border: 1px solid #cbd5ce; border-radius: 4px;
    padding: 7px 8px; selection-background-color: #3d7665;
}
QComboBox QAbstractItemView {
    background: #ffffff; color: #263830; border: 1px solid #cbd5ce;
    selection-background-color: #e1eee6; selection-color: #203b30; outline: 0;
}
QWidget#preferencesPage { background: #f4f7f4; color: #263830; }
QTabWidget::pane {
    background: #f4f7f4; border: 1px solid #cbd5ce; top: -1px;
}
QTabBar::tab {
    background: #e8efea; color: #263830; border: 1px solid #cbd5ce;
    padding: 8px 16px; min-width: 72px;
}
QTabBar::tab:selected {
    background: #f4f7f4; color: #203b30; font-weight: 700;
    border-bottom-color: #f4f7f4;
}
QTabBar::tab:hover { background: #f0f5f1; }
QTableView, QListWidget {
    background: #ffffff; color: #263830; alternate-background-color: #f1f5f1;
    border: 1px solid #cbd5ce; selection-background-color: #dceae1;
    selection-color: #203b30; outline: 0;
}
QTableView::item, QListWidget::item { color: #263830; padding: 4px; }
QTableView::item:hover, QListWidget::item:hover { background: #f0f5f1; }
QHeaderView::section {
    background: #e8efea; color: #263830; padding: 5px; border: 0;
    border-right: 1px solid #d7dfd9; border-bottom: 1px solid #cbd5ce;
}
QMenu { background: #ffffff; color: #263830; border: 1px solid #d7dfd9; }
QMenu::item { background: #ffffff; color: #263830; padding: 6px 24px 6px 12px; }
QMenu::item:selected { background: #e1eee6; color: #203b30; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #3d7665; }
QTreeWidget {
    background: #ffffff; border: 1px solid #d7dfd9; border-radius: 5px;
    outline: 0; padding: 4px;
}
QTreeWidget::item { min-height: 30px; padding: 2px 4px; }
QTreeWidget::item:hover { background: #f0f5f1; }
QTreeWidget::item:selected { background: #e1eee6; color: #203b30; }
QTreeWidget::indicator { width: 16px; height: 16px; }
QTreeWidget::indicator:unchecked { background: #ffffff; border: 1px solid #aab8af; border-radius: 3px; }
QTreeWidget::indicator:checked { background: #3d7665; border: 1px solid #3d7665; border-radius: 3px; }
QTreeWidget::indicator:indeterminate { background: #93ae9e; border: 1px solid #638174; border-radius: 3px; }
QPushButton {
    background: #ffffff; border: 1px solid #cbd5ce; border-radius: 4px;
    padding: 8px 12px; min-height: 18px;
}
QPushButton:hover { background: #edf3ee; border-color: #91a99a; }
QPushButton:pressed { background: #e2ebe4; }
QPushButton:disabled { color: #9ca9a1; background: #eef1ee; border-color: #d7dfd9; }
QPushButton#primaryButton { color: #ffffff; background: #285b49; border: 1px solid #285b49; font-weight: 700; }
QPushButton#primaryButton:hover { background: #34725d; }
QPushButton#secondaryButton { background: transparent; }

/* Botones directos de despacho a máquina: cómodos para lectura y diferenciados por tono */
QPushButton#machineButton1 {
    color: #ffffff; background: #285b49; border: 1px solid #224d3e;
    border-radius: 5px; padding: 8px 16px; font-size: 10.5pt; font-weight: 700;
}
QPushButton#machineButton1:hover { background: #33725c; border-color: #285b49; }
QPushButton#machineButton1:pressed { background: #1f4839; }
QPushButton#machineButton1:disabled { color: #9ca9a1; background: #eef1ee; border-color: #d7dfd9; }
QPushButton#machineButton1[warning="true"] { border: 2px solid #d77b5c; }

QPushButton#machineButton2 {
    color: #ffffff; background: #2a5d70; border: 1px solid #224c5c;
    border-radius: 5px; padding: 8px 16px; font-size: 10.5pt; font-weight: 700;
}
QPushButton#machineButton2:hover { background: #35738a; border-color: #2a5d70; }
QPushButton#machineButton2:pressed { background: #204958; }
QPushButton#machineButton2:disabled { color: #9ca9a1; background: #eef1ee; border-color: #d7dfd9; }
QPushButton#machineButton2[warning="true"] { border: 2px solid #d77b5c; }

QFrame#solutionCard {
    background: #ffffff; border: 1px solid #d7dfd9; border-radius: 5px;
}
QFrame#solutionCard:disabled {
    background: #edf0ed; border: 1px solid #d7dfd9;
}
QFrame#solutionCard:disabled QLabel { color: #87928b; }
QFrame#solutionCard[selected="true"] { border: 2px solid #3d7665; }
QScrollArea { background: transparent; border: 0; }
QGroupBox {
    border: 1px solid #d7dfd9; border-radius: 5px; margin-top: 12px;
    padding: 14px 10px 10px; font-weight: 700;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #587365; }
QStatusBar { color: #718077; background: #edf2ee; border-top: 1px solid #d7dfd9; }
QProgressBar { background: #dce5de; border: 0; border-radius: 3px; }
QProgressBar::chunk { background: #d77b5c; border-radius: 3px; }
QMenuBar { background: #edf2ee; color: #263830; }
QMenuBar::item:selected, QMenu::item:selected { background: #e1eee6; }
"""
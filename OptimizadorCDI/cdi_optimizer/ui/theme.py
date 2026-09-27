APP_STYLESHEET = """
QMainWindow, QDialog { background: #f4f7f4; color: #263830; }
QWidget { font-family: 'Bahnschrift', 'Segoe UI', sans-serif; font-size: 10pt; color: #263830; }
QLabel#appEyebrow { color: #638174; font-size: 9pt; font-weight: 700; }
QLabel#pageTitle { color: #1e352c; font-size: 19pt; font-weight: 700; }
QLabel#sectionTitle { color: #263830; font-size: 11pt; font-weight: 700; }
QLabel#mutedText { color: #75847c; }
QLabel#resultSummary { color: #263830; font-size: 11pt; font-weight: 700; padding: 4px 2px; }
QLabel#selectionCount { color: #638174; font-size: 9pt; }
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
QPushButton:disabled { color: #9ca9a1; background: #eef1ee; }
QPushButton#primaryButton { color: #ffffff; background: #285b49; border: 1px solid #285b49; font-weight: 700; }
QPushButton#primaryButton:hover { background: #34725d; }
QPushButton#secondaryButton { background: transparent; }
QFrame#solutionCard {
    background: #ffffff; border: 1px solid #d7dfd9; border-radius: 5px;
}
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
QMenu { background: #ffffff; border: 1px solid #d7dfd9; }
"""
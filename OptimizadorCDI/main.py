import sys
from PySide6.QtWidgets import QApplication
from cdi_optimizer.ui.main_window import OptimizadorCDI

def main():
    app = QApplication(sys.argv)
    
    # Configuramos la información de la app para que QSettings sepa dónde guardar los datos en el registro de Windows
    app.setOrganizationName("MTRemozar")
    app.setApplicationName("OptimizadorCDI")
    
    ventana = OptimizadorCDI()
    ventana.showMaximized()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
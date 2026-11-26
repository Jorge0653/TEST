from multiprocessing import freeze_support
import sys
from pathlib import Path
from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QApplication
from cdi_optimizer.ui.main_window import OptimizadorCDI
from cdi_optimizer.logging_config import configure_logging_safely


def _install_exception_logging(logger):
    def log_uncaught_exception(exception_type, exception, traceback):
        logger.critical(
            "Excepción no controlada",
            exc_info=(exception_type, exception, traceback),
        )
        sys.__excepthook__(exception_type, exception, traceback)

    sys.excepthook = log_uncaught_exception

def main():
    app = QApplication(sys.argv)
    
    # Configuramos la información de la app para que QSettings sepa dónde guardar los datos en el registro de Windows
    app.setOrganizationName("MTRemozar")
    app.setApplicationName("OptimizadorCDI")
    data_directory = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    )
    logger = configure_logging_safely(Path(data_directory) / "logs")
    _install_exception_logging(logger)
    
    ventana = OptimizadorCDI()
    ventana.showMaximized()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    freeze_support()
    main()
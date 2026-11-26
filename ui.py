"""Compatibility facade for the modular PySide6 interface."""

from cdi_optimizer.ui.canvas import CanvasPrevisualizacion, ResultadoTab
from cdi_optimizer.ui.main_window import OptimizadorCDI, PanelMargenes
from cdi_optimizer.ui.preferences import DialogoConfiguracion

__all__ = [
    "CanvasPrevisualizacion",
    "DialogoConfiguracion",
    "OptimizadorCDI",
    "PanelMargenes",
    "ResultadoTab",
]
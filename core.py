"""Compatibility facade for older imports; implementation lives in cdi_optimizer."""

from cdi_optimizer.core import escanear_carpeta_rip, extraer_medidas_xmp
from cdi_optimizer.core.optimizer import optimizar_archivos


def calcular_acomodo_basico(lista_archivos, config):
    """Return the single-plate layout using the current optimizer."""
    return optimizar_archivos(lista_archivos, config)["placa_unica"]
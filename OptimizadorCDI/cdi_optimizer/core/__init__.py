"""Lectura de trabajos RIP y optimizacion geometrica."""

from .metadata import extraer_medidas_xmp
from .optimizer import generar_alternativas_archivos, optimizar_archivos
from .packing import optimizar_piezas
from .recommender import sugerir_combinaciones
from .scanner import (
	escanear_carpeta_rip,
	extraer_codigo_fotopolimero,
	extraer_material_fotopolimero,
)

__all__ = [
	"escanear_carpeta_rip",
	"extraer_codigo_fotopolimero",
	"extraer_material_fotopolimero",
	"extraer_medidas_xmp",
	"optimizar_archivos",
	"generar_alternativas_archivos",
	"optimizar_piezas",
	"sugerir_combinaciones",
]
"""Read engraving dimensions embedded in LEN XMP metadata."""

import math
import os
import re
from functools import lru_cache


_XMP_PATTERN = re.compile(rb"<x:xmpmeta\b.*?</x:xmpmeta>", re.DOTALL | re.IGNORECASE)
_SIZE_PATTERNS = {
    "alto_mm": re.compile(
        rb"<(?:[\w.-]+:)?vsize\b[^>]*>\s*([0-9]+(?:\.[0-9]*)?(?:[eE][+-]?\d+)?)\s*</",
        re.IGNORECASE,
    ),
    "ancho_mm": re.compile(
        rb"<(?:[\w.-]+:)?hsize\b[^>]*>\s*([0-9]+(?:\.[0-9]*)?(?:[eE][+-]?\d+)?)\s*</",
        re.IGNORECASE,
    ),
}
_RESOLUTION_PATTERNS = {
    "resolucion_y_dpi": re.compile(
        rb"<(?:[\w.-]+:)?vresolution\b[^>]*>\s*([0-9]+(?:\.[0-9]*)?)\s*</",
        re.IGNORECASE,
    ),
    "resolucion_x_dpi": re.compile(
        rb"<(?:[\w.-]+:)?hresolution\b[^>]*>\s*([0-9]+(?:\.[0-9]*)?)\s*</",
        re.IGNORECASE,
    ),
}


def extraer_medidas_xmp(ruta_archivo):
    """Return cached dimensions while the source LEN file remains unchanged."""
    try:
        path = os.path.normcase(os.path.abspath(os.fspath(ruta_archivo)))
        stat = os.stat(path)
    except (OSError, TypeError, ValueError):
        return None

    measurements = _extraer_medidas_xmp_cache(
        path,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )
    return dict(measurements) if measurements is not None else None


@lru_cache(maxsize=2048)
def _extraer_medidas_xmp_cache(path, file_size, modified_ns, changed_ns):
    return _leer_medidas_xmp(path)


def _leer_medidas_xmp(ruta_archivo):
    """Return height and width in mm from an XMP block near a LEN file edge."""
    try:
        with open(ruta_archivo, "rb") as file:
            start = file.read(256_000)
            file.seek(0, os.SEEK_END)
            size = file.tell()
            if size > 256_000:
                file.seek(max(0, size - 256_000))
                end = file.read(256_000)
            else:
                end = b""

        blocks = [match.group(0) for match in _XMP_PATTERN.finditer(start)]
        blocks.extend(match.group(0) for match in _XMP_PATTERN.finditer(end))
        for block in blocks:
            measurements = {}
            for name, pattern in _SIZE_PATTERNS.items():
                match = pattern.search(block)
                if not match:
                    break
                value = float(match.group(1))
                if not math.isfinite(value) or value <= 0:
                    break
                measurements[name] = value
            if len(measurements) == 2:
                for name, pattern in _RESOLUTION_PATTERNS.items():
                    match = pattern.search(block)
                    if match:
                        resolution = float(match.group(1))
                        if math.isfinite(resolution) and resolution > 0:
                            measurements[name] = resolution
                return measurements
    except (OSError, ValueError):
        return None
    return None
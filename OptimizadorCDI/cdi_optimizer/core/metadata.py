"""Read engraving dimensions and screening metadata embedded in LEN XMP blocks."""

import html
import math
import os
import re
from functools import lru_cache

_FLOAT_EXPR = rb"([0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?\d+)?"
_XMP_PATTERN = re.compile(rb"<x:xmpmeta\b.*?</x:xmpmeta>", re.DOTALL | re.IGNORECASE)
_UNITS_PATTERN = re.compile(
    rb"<(?:[\w.-]+:)?units\b[^>]*>\s*([a-zA-Z]+)\s*</",
    re.IGNORECASE,
)
_SIZE_PATTERNS = {
    "alto_mm": re.compile(
        rb"<(?:[\w.-]+:)?vsize\b[^>]*>\s*" + _FLOAT_EXPR + rb"\s*</",
        re.IGNORECASE,
    ),
    "ancho_mm": re.compile(
        rb"<(?:[\w.-]+:)?hsize\b[^>]*>\s*" + _FLOAT_EXPR + rb"\s*</",
        re.IGNORECASE,
    ),
}
_RESOLUTION_PATTERNS = {
    "resolucion_y_dpi": re.compile(
        rb"<(?:[\w.-]+:)?vresolution\b[^>]*>\s*" + _FLOAT_EXPR + rb"\s*</",
        re.IGNORECASE,
    ),
    "resolucion_x_dpi": re.compile(
        rb"<(?:[\w.-]+:)?hresolution\b[^>]*>\s*" + _FLOAT_EXPR + rb"\s*</",
        re.IGNORECASE,
    ),
}
_INK_NAME_PATTERNS = (
    re.compile(rb"<(?:[\w.-]+:)?egname\b[^>]*>\s*([^<]+?)\s*</", re.IGNORECASE),
    re.compile(rb"<egInk:name\b[^>]*>\s*([^<]+?)\s*</", re.IGNORECASE),
    re.compile(rb"<(?:[\w.-]+:)?inkname\b[^>]*>\s*([^<]+?)\s*</", re.IGNORECASE),
)
_RGB_PATTERNS = (
    re.compile(rb"<egInk:r\b[^>]*>\s*" + _FLOAT_EXPR + rb"\s*</", re.IGNORECASE),
    re.compile(rb"<egInk:g\b[^>]*>\s*" + _FLOAT_EXPR + rb"\s*</", re.IGNORECASE),
    re.compile(rb"<egInk:b\b[^>]*>\s*" + _FLOAT_EXPR + rb"\s*</", re.IGNORECASE),
)
# Prioriza frequencyreq y anglereq (valores nominales de preprensa) sobre los calculados por el RIP
_FREQUENCY_PATTERNS = (
    re.compile(rb"<(?:[\w.-]+:)?frequencyreq\b[^>]*>\s*" + _FLOAT_EXPR + rb"\s*</", re.IGNORECASE),
    re.compile(rb"<(?:[\w.-]+:)?frequency\b[^>]*>\s*" + _FLOAT_EXPR + rb"\s*</", re.IGNORECASE),
)
_ANGLE_PATTERNS = (
    re.compile(rb"<(?:[\w.-]+:)?anglereq\b[^>]*>\s*" + _FLOAT_EXPR + rb"\s*</", re.IGNORECASE),
    re.compile(rb"<(?:[\w.-]+:)?angle\b[^>]*>\s*" + _FLOAT_EXPR + rb"\s*</", re.IGNORECASE),
)
_DOT_PATTERNS = (
    re.compile(rb"<(?:[\w.-]+:)?dotname\b[^>]*>\s*([^<]+?)\s*</", re.IGNORECASE),
    re.compile(rb"<(?:[\w.-]+:)?dotshapereq\b[^>]*>\s*([^<]+?)\s*</", re.IGNORECASE),
    re.compile(rb"<(?:[\w.-]+:)?dotshape\b[^>]*>\s*([^<]+?)\s*</", re.IGNORECASE),
)

_UNIT_FACTORS_TO_MM = {
    "mm": 1.0,
    "millimeter": 1.0,
    "millimeters": 1.0,
    "cm": 10.0,
    "centimeter": 10.0,
    "centimeters": 10.0,
    "in": 25.4,
    "inch": 25.4,
    "inches": 25.4,
    "pt": 25.4 / 72.0,
    "point": 25.4 / 72.0,
    "points": 25.4 / 72.0,
    "um": 0.001,
    "micron": 0.001,
    "microns": 0.001,
}


def extraer_medidas_xmp(ruta_archivo):
    """Return cached dimensions and extended metadata while the source LEN file remains unchanged."""
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


def _extraer_texto(block, patterns):
    for pattern in patterns:
        match = pattern.search(block)
        if match:
            try:
                text = html.unescape(match.group(1).decode("utf-8", errors="replace")).strip()
            except Exception:
                continue
            if text:
                return text
    return None


def _extraer_flotante(block, patterns, allow_zero=False):
    for pattern in patterns:
        match = pattern.search(block)
        if match:
            try:
                value = float(match.group(0).split(b">", 1)[1].split(b"<", 1)[0].strip())
            except (IndexError, ValueError):
                continue
            if math.isfinite(value) and (value >= 0 if allow_zero else value > 0):
                return value
    return None


def _extraer_color_rgb(block):
    channels = []
    for pattern in _RGB_PATTERNS:
        match = pattern.search(block)
        if not match:
            return None
        try:
            raw_text = match.group(0).split(b">", 1)[1].split(b"<", 1)[0].strip()
            value = float(raw_text)
        except (IndexError, ValueError):
            return None
        if not math.isfinite(value) or value < 0:
            return None
        channels.append(value)
    scale = 255.0 if max(channels) <= 1.0 else 1.0
    return tuple(max(0, min(255, round(channel * scale))) for channel in channels)


def _leer_medidas_xmp(ruta_archivo):
    """Return height, width in mm and extended screening info from a LEN XMP block."""
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
            unit_factor = 1.0
            unit_match = _UNITS_PATTERN.search(block)
            if unit_match:
                unit_name = unit_match.group(1).decode("ascii", errors="ignore").strip().lower()
                unit_factor = _UNIT_FACTORS_TO_MM.get(unit_name, 1.0)

            measurements = {}
            for name, pattern in _SIZE_PATTERNS.items():
                match = pattern.search(block)
                if not match:
                    break
                raw_val = match.group(0).split(b">", 1)[1].split(b"<", 1)[0].strip()
                value = float(raw_val) * unit_factor
                if not math.isfinite(value) or value <= 0:
                    break
                measurements[name] = round(value, 6)

            if len(measurements) == 2:
                for name, pattern in _RESOLUTION_PATTERNS.items():
                    match = pattern.search(block)
                    if match:
                        raw_res = match.group(0).split(b">", 1)[1].split(b"<", 1)[0].strip()
                        resolution = float(raw_res)
                        if math.isfinite(resolution) and resolution > 0:
                            measurements[name] = resolution

                ink_name = _extraer_texto(block, _INK_NAME_PATTERNS)
                if ink_name is not None:
                    measurements["tinta_nombre"] = ink_name

                color_rgb = _extraer_color_rgb(block)
                if color_rgb is not None:
                    measurements["color_rgb"] = color_rgb

                frequency = _extraer_flotante(block, _FREQUENCY_PATTERNS, allow_zero=False)
                if frequency is not None:
                    measurements["lineaje_lpi"] = round(frequency, 2)

                angle = _extraer_flotante(block, _ANGLE_PATTERNS, allow_zero=True)
                if angle is not None:
                    measurements["angulo_grados"] = round(angle, 2)

                dot_shape = _extraer_texto(block, _DOT_PATTERNS)
                if dot_shape is not None:
                    measurements["punto_trama"] = dot_shape

                return measurements
    except (OSError, ValueError):
        return None
    return None
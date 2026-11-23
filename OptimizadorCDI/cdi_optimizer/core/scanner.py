"""Discover LEN separations stored in a RIP output directory."""

import os
import re


_FOTOPOLIMERO_PATTERN = re.compile(r"^(?P<trabajo>.+)_(?P<material>FP(?:45|67|112))$", re.IGNORECASE)
_FOTOPOLIMERO_CODE_PATTERN = re.compile(
    r"(?<![A-Z0-9])(?:FP\s*)?(?P<codigo>45|67|112)(?![A-Z0-9])",
    re.IGNORECASE,
)


def _descomponer_nombre_len(filename):
    """Return job name, ink filename, and optional photopolymer type."""
    name_without_extension = os.path.splitext(os.path.basename(filename))[0]
    job_and_material, separator, ink = name_without_extension.rpartition("_")
    if not separator:
        return name_without_extension, filename, None

    material_match = _FOTOPOLIMERO_PATTERN.fullmatch(job_and_material)
    if material_match:
        return (
            material_match.group("trabajo"),
            ink + ".len",
            material_match.group("material").upper(),
        )
    return job_and_material, ink + ".len", None


def extraer_material_fotopolimero(filename):
    """Read FP45, FP67, or FP112 from a supported LEN filename."""
    return _descomponer_nombre_len(filename)[2]


def extraer_codigo_fotopolimero(value):
    """Return the supported numeric material code from a filename or label."""
    match = _FOTOPOLIMERO_CODE_PATTERN.search(str(value or ""))
    return match.group("codigo") if match else None


def escanear_carpeta_rip(ruta_carpeta):
    """Group LEN files by the job name before the final underscore."""
    if not os.path.isdir(ruta_carpeta):
        return {}

    jobs = {}
    try:
        filenames = sorted(os.listdir(ruta_carpeta), key=str.casefold)
    except OSError:
        return {}

    for filename in filenames:
        if not filename.lower().endswith(".len"):
            continue
        job_name, ink_name, material = _descomponer_nombre_len(filename)
        jobs.setdefault(job_name, []).append(
            {
                "nombre_tinta": ink_name,
                "ruta_completa": os.path.join(ruta_carpeta, filename),
                "material_fotopolimero": material,
            }
        )
    return jobs
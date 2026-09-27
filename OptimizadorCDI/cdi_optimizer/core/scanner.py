"""Discover LEN separations stored in a RIP output directory."""

import os


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
        name_without_extension = os.path.splitext(filename)[0]
        parts = name_without_extension.rsplit("_", 1)
        if len(parts) == 2:
            job_name, ink_name = parts[0], parts[1] + ".len"
        else:
            job_name, ink_name = name_without_extension, filename
        jobs.setdefault(job_name, []).append(
            {
                "nombre_tinta": ink_name,
                "ruta_completa": os.path.join(ruta_carpeta, filename),
            }
        )
    return jobs
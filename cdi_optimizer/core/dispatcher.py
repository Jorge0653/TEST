"""Despacho seguro de archivos LEN desde la carpeta raíz hacia carpetas de máquinas CDI."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any


@dataclass(frozen=True)
class MaquinaDestino:
    """Configuración de una máquina CDI de destino."""

    slot: int
    nombre: str
    ruta: str
    tambor_w: float
    tambor_h: float

    @property
    def configurada(self) -> bool:
        return bool(self.ruta and os.path.isdir(self.ruta))


def extraer_rutas_acomodo(layout_data: dict[str, Any] | None) -> list[str]:
    """Devuelve las rutas únicas y ordenadas de los archivos LEN del acomodo visible."""
    if not layout_data:
        return []

    raw_paths: list[str] = []
    if layout_data.get("archivos_asignados"):
        raw_paths.extend(str(p) for p in layout_data["archivos_asignados"] if p)
    else:
        for part in layout_data.get("grabados", []):
            path = part.get("ruta")
            if path:
                raw_paths.append(str(path))

    seen: set[str] = set()
    unique: list[str] = []
    for path in raw_paths:
        norm = os.path.normcase(os.path.abspath(path))
        if norm not in seen:
            seen.add(norm)
            unique.append(path)
    return unique


def validar_acomodo_para_maquina(
    layout_data: dict[str, Any] | None,
    maquina: MaquinaDestino,
) -> list[str]:
    """Valida si las placas del acomodo caben en el tambor máximo de la máquina destino."""
    if not layout_data:
        return ["No hay un acomodo activo para validar."]

    warnings: list[str] = []
    max_w = float(maquina.tambor_w)
    max_h = float(maquina.tambor_h)

    plates = layout_data.get("placas")
    if plates:
        for index, plate in enumerate(plates, start=1):
            pw = float(plate.get("ancho", 0))
            ph = float(plate.get("alto", 0))
            if pw > max_w + 1e-6 or ph > max_h + 1e-6:
                warnings.append(
                    f"La placa {index} ({pw:g} × {ph:g} mm) excede el tambor máximo "
                    f"de {maquina.nombre} ({max_w:g} × {max_h:g} mm)."
                )
    else:
        # En vista de retazo valida el corte propuesto (ancho_acomodo/alto_acomodo) si existe
        pw = float(layout_data.get("ancho_acomodo", layout_data.get("ancho", 0)))
        ph = float(layout_data.get("alto_acomodo", layout_data.get("alto", 0)))
        if pw > max_w + 1e-6 or ph > max_h + 1e-6:
            warnings.append(
                f"La placa ({pw:g} × {ph:g} mm) excede el tambor máximo "
                f"de {maquina.nombre} ({max_w:g} × {max_h:g} mm)."
            )

    return warnings


def preparar_envio(rutas_origen: list[str], destino_dir: str) -> dict[str, Any]:
    """Ejecuta validaciones previas al movimiento sin modificar ningún archivo."""
    destino = Path(destino_dir)
    if not destino_dir or not destino.is_dir():
        return {
            "valido": False,
            "error": f"La carpeta de destino no existe o no es accesible:\n{destino_dir}",
            "faltantes": [],
            "colisiones": [],
            "mismo_directorio": [],
        }

    if not os.access(destino, os.W_OK):
        return {
            "valido": False,
            "error": f"No hay permisos de escritura en la carpeta destino:\n{destino_dir}",
            "faltantes": [],
            "colisiones": [],
            "mismo_directorio": [],
        }

    destino_norm = os.path.normcase(os.path.abspath(destino))
    faltantes: list[str] = []
    colisiones: list[str] = []
    mismo_directorio: list[str] = []

    for ruta in rutas_origen:
        src = Path(ruta)
        if not src.is_file():
            faltantes.append(src.name)
            continue
        src_parent_norm = os.path.normcase(os.path.abspath(src.parent))
        if src_parent_norm == destino_norm:
            mismo_directorio.append(src.name)
            continue
        target = destino / src.name
        if target.exists():
            colisiones.append(src.name)

    if faltantes:
        return {
            "valido": False,
            "error": "Algunos archivos LEN ya no existen en la carpeta raíz:\n• "
            + "\n• ".join(faltantes),
            "faltantes": faltantes,
            "colisiones": colisiones,
            "mismo_directorio": mismo_directorio,
        }

    if mismo_directorio:
        return {
            "valido": False,
            "error": "La carpeta destino coincide con la carpeta actual de los archivos.",
            "faltantes": faltantes,
            "colisiones": colisiones,
            "mismo_directorio": mismo_directorio,
        }

    return {
        "valido": True,
        "error": None,
        "faltantes": [],
        "colisiones": colisiones,
        "mismo_directorio": [],
    }


def _mover_archivo_atomico(origen: Path, destino: Path) -> None:
    """Mueve un archivo usando os.replace en el mismo volumen o copia temporal + replace entre volúmenes."""
    try:
        os.replace(origen, destino)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise

    # Respaldo seguro entre volúmenes distintos: copia a archivo temporal en destino y renombra
    fd, tmp_name = tempfile.mkstemp(prefix=".cdi_tmp_", dir=str(destino.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        shutil.copy2(origen, tmp_path)
        os.replace(tmp_path, destino)
        origen.unlink()
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def mover_archivos_a_maquina(
    rutas_origen: list[str],
    destino_dir: str,
    *,
    sobrescribir: bool = False,
) -> dict[str, Any]:
    """Mueve un lote de archivos LEN al destino con rollback automático ante error parcial."""
    preflight = preparar_envio(rutas_origen, destino_dir)
    if not preflight["valido"]:
        raise OSError(preflight["error"])
    if preflight["colisiones"] and not sobrescribir:
        raise FileExistsError(
            "Ya existen archivos con el mismo nombre en destino: "
            + ", ".join(preflight["colisiones"])
        )

    destino = Path(destino_dir)
    movidos: list[tuple[str, str]] = []
    try:
        for ruta in rutas_origen:
            src = Path(ruta)
            dst = destino / src.name
            _mover_archivo_atomico(src, dst)
            movidos.append((str(src), str(dst)))
    except Exception as exc:
        # Intentar revertir los archivos ya movidos para no dejar el trabajo partido
        rollback_errors: list[str] = []
        for src_str, dst_str in reversed(movidos):
            try:
                _mover_archivo_atomico(Path(dst_str), Path(src_str))
            except OSError as rb_exc:
                rollback_errors.append(f"{Path(dst_str).name}: {rb_exc}")
        if rollback_errors:
            raise OSError(
                f"Falló el envío ({exc}) y no se pudieron revertir todos los archivos:\n"
                + "\n".join(rollback_errors)
            ) from exc
        raise OSError(
            f"Falló el envío ({exc}). Se revirtieron los archivos a la carpeta raíz."
        ) from exc

    return {
        "movidos": movidos,
        "destino": str(destino),
        "cantidad": len(movidos),
    }


def revertir_envio(movidos: list[tuple[str, str]]) -> int:
    """Devuelve a su carpeta original los archivos de un envío previo."""
    if not movidos:
        return 0

    for src_str, dst_str in movidos:
        src = Path(src_str)
        dst = Path(dst_str)
        if not dst.is_file():
            raise FileNotFoundError(
                f"No se encontró '{dst.name}' en la carpeta de la máquina para revertirlo."
            )
        if src.exists():
            raise FileExistsError(
                f"Ya existe '{src.name}' en la carpeta raíz; no se sobrescribirá."
            )

    reverted: list[tuple[str, str]] = []
    try:
        for src_str, dst_str in movidos:
            _mover_archivo_atomico(Path(dst_str), Path(src_str))
            reverted.append((src_str, dst_str))
    except Exception as exc:
        for src_str, dst_str in reversed(reverted):
            try:
                _mover_archivo_atomico(Path(src_str), Path(dst_str))
            except OSError:
                pass
        raise OSError(f"No se pudo revertir el envío: {exc}") from exc

    return len(reverted)
"""Build optimized plate alternatives from selected LEN files."""

import logging
import os
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

from .metadata import extraer_medidas_xmp
from .packing import optimizar_piezas
from .scanner import extraer_material_fotopolimero

_MAX_VISIBLE_ALTERNATIVES = 10
_MAX_ALTERNATIVES_PER_BATCH = 2
_PARALLEL_PROFILE_MIN_PIECES = 16
_MAX_PARALLEL_PROFILE_WORKERS = 4
_LOGGER = logging.getLogger("cdi_optimizer")

_EXTENDED_META_KEYS = (
    "tinta_nombre",
    "color_rgb",
    "lineaje_lpi",
    "angulo_grados",
    "punto_trama",
)


def _variant_profiles(max_ratio, allow_rotation):
    return (
        ("sin_giro_individual", min(1.5, max_ratio), 50, True, False),
        ("ratio_compacto", min(1.25, max_ratio), 30, False, allow_rotation),
        ("ratio_equilibrado", min(1.75, max_ratio), 55, True, allow_rotation),
        ("ratio_alargado", min(2.25, max_ratio), 75, False, allow_rotation),
        ("ratio_alargado_agrupado", min(2.5, max_ratio), 70, True, allow_rotation),
    )


def _cancelled(cancel_event):
    return cancel_event is not None and cancel_event.is_set()


def _optimizar_perfil_en_proceso(arguments):
    pieces, profile_config = arguments
    return optimizar_piezas(pieces, profile_config)


def _evaluar_perfiles_secuencialmente(
    pieces, profile_configs, cancel_event, progress_callback=None
):
    results = []
    for index, profile_config in enumerate(profile_configs, start=1):
        if _cancelled(cancel_event):
            return None
        results.append(
            optimizar_piezas(pieces, profile_config, cancel_event=cancel_event)
        )
        if progress_callback:
            percent = 20 + round(index / len(profile_configs) * 75)
            progress_callback(percent, f"Perfil {index}/{len(profile_configs)} completado")
    return results


def _evaluar_perfiles(
    pieces, profile_configs, cancel_event=None, progress_callback=None
):
    if _cancelled(cancel_event):
        return None
    if (
        len(pieces) < _PARALLEL_PROFILE_MIN_PIECES
        or len(profile_configs) < 2
        or (os.cpu_count() or 1) < 2
    ):
        return _evaluar_perfiles_secuencialmente(
            pieces, profile_configs, cancel_event, progress_callback
        )
    worker_count = min(
        _MAX_PARALLEL_PROFILE_WORKERS,
        len(profile_configs),
        os.cpu_count() or 1,
    )
    results = [None] * len(profile_configs)
    try:
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    _optimizar_perfil_en_proceso, (pieces, profile_config)
                ): index
                for index, profile_config in enumerate(profile_configs)
            }
            pending = set(futures)
            completed_count = 0
            while pending:
                if _cancelled(cancel_event):
                    for future in pending:
                        future.cancel()
                    return None
                completed, pending = wait(
                    pending,
                    timeout=0.1,
                    return_when=FIRST_COMPLETED,
                )
                for future in completed:
                    results[futures[future]] = future.result()
                    completed_count += 1
                    if progress_callback:
                        percent = 20 + round(
                            completed_count / len(profile_configs) * 75
                        )
                        progress_callback(
                            percent,
                            f"Perfiles completados: {completed_count}/{len(profile_configs)}",
                        )
    except Exception:
        if _cancelled(cancel_event):
            return None
        _LOGGER.exception(
            "No se pudieron evaluar perfiles en paralelo; se reintenta en serie"
        )
        return _evaluar_perfiles_secuencialmente(
            pieces, profile_configs, cancel_event, progress_callback
        )
    return results


def _leer_piezas(lista_archivos, cancel_event=None, progress_callback=None):
    piezas = []
    errores = []
    if _cancelled(cancel_event):
        return piezas, errores, True
    total = len(lista_archivos)
    for index, ruta in enumerate(lista_archivos, start=1):
        if _cancelled(cancel_event):
            return piezas, errores, True
        medidas = extraer_medidas_xmp(ruta)
        if medidas is None:
            errores.append(
                f"No se pudieron leer las dimensiones de {os.path.basename(ruta)}."
            )
        else:
            pieza = {
                "name": os.path.basename(ruta),
                "path": ruta,
                "width_mm": medidas["ancho_mm"],
                "height_mm": medidas["alto_mm"],
                "material_fotopolimero": extraer_material_fotopolimero(ruta),
                "resolucion_dpi": (
                    medidas.get("resolucion_x_dpi"),
                    medidas.get("resolucion_y_dpi"),
                )
                if "resolucion_x_dpi" in medidas
                and "resolucion_y_dpi" in medidas
                else None,
            }
            for extra_key in _EXTENDED_META_KEYS:
                if extra_key in medidas:
                    pieza[extra_key] = medidas[extra_key]
            piezas.append(pieza)
        if progress_callback:
            progress_callback(
                round(index / max(1, total) * 20),
                f"Leyendo metadatos: {index}/{total} archivos",
            )
    return piezas, errores, False


def _validar_resoluciones(piezas):
    resolutions = {piece["resolucion_dpi"] for piece in piezas}
    return len(piezas) <= 1 or (None not in resolutions and len(resolutions) <= 1)


def _validar_materiales_fotopolimero(piezas):
    materials = {piece.get("material_fotopolimero") for piece in piezas}
    return len(materials) <= 1


def _error_resolucion():
    return [
        "No se pueden combinar trabajos con distintas resoluciones DPI "
        "o sin resolución verificable."
    ]


def _error_material_fotopolimero():
    return [
        "No se pueden combinar trabajos con tipos de fotopolímero distintos "
        "o con archivos sin tipo verificable."
    ]


def optimizar_archivos(lista_archivos, config=None, cancel_event=None):
    piezas, errores, cancelled = _leer_piezas(lista_archivos, cancel_event)
    if cancelled:
        return {
            "placa_unica": None,
            "placas_dobles": None,
            "errores": [],
            "cancelado": True,
        }
    if not _validar_resoluciones(piezas):
        return {
            "placa_unica": None,
            "placas_dobles": None,
            "errores": _error_resolucion(),
        }
    if not _validar_materiales_fotopolimero(piezas):
        return {
            "placa_unica": None,
            "placas_dobles": None,
            "errores": _error_material_fotopolimero(),
        }
    resultado = optimizar_piezas(piezas, config, cancel_event=cancel_event)
    resultado["errores"] = errores + resultado["errores"]
    return resultado


def _geometry_signature(solution):
    placements = tuple(
        sorted(
            (
                round(part["x"], 1),
                round(part["y"], 1),
                round(part["w"], 1),
                round(part["h"], 1),
                bool(part.get("rotado")),
                part.get("placa", 1),
            )
            for part in solution["grabados"]
        )
    )
    panels = tuple(
        (round(panel["ancho"], 1), round(panel["alto"], 1))
        for panel in solution.get("placas", [])
    )
    return (
        solution["tipo"],
        round(solution["ancho"], 1),
        round(solution["alto"], 1),
        panels,
        placements,
    )


def generar_alternativas_archivos(
    lista_archivos,
    config=None,
    cancel_event=None,
    *,
    variant_batch=None,
    existing_alternatives=(),
    progress_callback=None,
):
    existing_alternatives = tuple(existing_alternatives)
    if _cancelled(cancel_event):
        return {"alternativas": [], "errores": [], "cancelado": True}
    if variant_batch is not None and (
        variant_batch < 0
        or len(existing_alternatives) >= _MAX_VISIBLE_ALTERNATIVES
    ):
        return {
            "alternativas": [],
            "errores": [],
            "cancelado": False,
            "hay_mas_variantes": False,
        }

    pieces, errors, cancelled = _leer_piezas(
        lista_archivos, cancel_event, progress_callback
    )
    if cancelled:
        return {"alternativas": [], "errores": [], "cancelado": True}
    if not _validar_resoluciones(pieces):
        return {"alternativas": [], "errores": _error_resolucion(), "cancelado": False}
    if not _validar_materiales_fotopolimero(pieces):
        return {
            "alternativas": [],
            "errores": _error_material_fotopolimero(),
            "cancelado": False,
        }
    if not pieces:
        return {"alternativas": [], "errores": errors, "cancelado": False}

    base_config = dict(config or {})
    max_ratio = min(10.0, max(1.0, float(base_config.get("ratio_maximo", 3.0))))
    allow_rotation = bool(base_config.get("rotacion", True))
    balanced_ratio = min(1.5, max_ratio)
    initial_profiles = (
        ("maximum_use", 1.0, 0, False),
        ("balanced", balanced_ratio, 50, False),
        ("drum_safe", max_ratio, 100, False),
        ("dimension_grouped", balanced_ratio, 50, True),
    )
    variant_profiles = _variant_profiles(max_ratio, allow_rotation)

    if variant_batch is None:
        profiles = tuple(
            (*profile, allow_rotation) for profile in initial_profiles
        )
        known = set()
    else:
        if (
            variant_batch < 0
            or variant_batch >= len(variant_profiles)
            or len(existing_alternatives) >= _MAX_VISIBLE_ALTERNATIVES
        ):
            return {
                "alternativas": [],
                "errores": errors,
                "cancelado": False,
                "hay_mas_variantes": False,
            }
        profiles = (variant_profiles[variant_batch],)
        known = {
            _geometry_signature(solution) for solution in existing_alternatives
        }

    profile_configs = [
        {
            **base_config,
            "prioridad_aspecto": area_tolerance,
            "ratio_objetivo": target_ratio,
            "agrupar_dimensiones": group_dimensions,
            "rotacion": profile_rotation,
            "ratio_maximo": max_ratio,
        }
        for (
            _,
            target_ratio,
            area_tolerance,
            group_dimensions,
            profile_rotation,
        ) in profiles
    ]
    profile_results = _evaluar_perfiles(
        pieces, profile_configs, cancel_event, progress_callback
    )
    if profile_results is None or _cancelled(cancel_event):
        return {"alternativas": [], "errores": [], "cancelado": True}

    alternatives = []
    seen = set(known)
    ratio_rejected = False
    for profile_index, (profile, result) in enumerate(zip(profiles, profile_results)):
        if _cancelled(cancel_event):
            return {"alternativas": [], "errores": [], "cancelado": True}
        if result.get("cancelado") or _cancelled(cancel_event):
            return {"alternativas": [], "errores": [], "cancelado": True}
        profile_name = profile[0]
        for key, kind in (("placa_unica", "single"), ("placas_dobles", "split")):
            board = result.get(key)
            if board is None:
                continue
            if board.get("ratio_fuera_limite", False):
                ratio_rejected = True
                continue
            solution = {
                **board,
                "tipo": kind,
                "perfil_interno": profile_name,
                "_perfil_indice": profile_index,
                "ratio_alto_ancho": board["alto"] / board["ancho"],
            }
            signature = _geometry_signature(solution)
            if signature not in seen:
                seen.add(signature)
                alternatives.append(solution)

    def option_rank(option):
        return (
            -option["eficiencia_exacta"],
            option["_perfil_indice"],
            abs(option["ratio_alto_ancho"] - 1.5),
        )

    if variant_batch is None:
        singles = [option for option in alternatives if option["tipo"] == "single"]
        splits = [option for option in alternatives if option["tipo"] == "split"]
        selected = []
        if singles:
            selected.append(min(singles, key=option_rank))
        if splits:
            selected.append(min(splits, key=option_rank))
        selected_signatures = {_geometry_signature(option) for option in selected}
        for option in sorted(alternatives, key=option_rank):
            signature = _geometry_signature(option)
            if signature in selected_signatures:
                continue
            selected.append(option)
            selected_signatures.add(signature)
            if len(selected) == 4:
                break
        alternatives = selected
        more_variants = bool(alternatives)
    else:
        available_slots = _MAX_VISIBLE_ALTERNATIVES - len(existing_alternatives)
        alternatives = sorted(alternatives, key=option_rank)[
            : min(_MAX_ALTERNATIVES_PER_BATCH, available_slots)
        ]
        more_variants = (
            variant_batch + 1 < len(variant_profiles)
            and len(existing_alternatives) + len(alternatives)
            < _MAX_VISIBLE_ALTERNATIVES
        )

    for option in alternatives:
        option.pop("_perfil_indice", None)

    if not alternatives and ratio_rejected:
        errors.append(
            "Los grabados no caben en el ratio máximo configurado. "
            "Aumenta el límite en Preferencias para esta selección."
        )

    return {
        "alternativas": alternatives,
        "errores": errors,
        "cancelado": _cancelled(cancel_event),
        "hay_mas_variantes": more_variants,
    }
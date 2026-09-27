"""Build optimized plate alternatives from selected LEN files."""

import os

from .metadata import extraer_medidas_xmp
from .packing import optimizar_piezas

_MAX_VISIBLE_ALTERNATIVES = 10
_MAX_ALTERNATIVES_PER_BATCH = 2


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


def _leer_piezas(lista_archivos, cancel_event=None):
    piezas = []
    errores = []
    if _cancelled(cancel_event):
        return piezas, errores, True
    for ruta in lista_archivos:
        if _cancelled(cancel_event):
            return piezas, errores, True
        medidas = extraer_medidas_xmp(ruta)
        if medidas is None:
            errores.append(f"No se pudieron leer las dimensiones de {os.path.basename(ruta)}.")
            continue
        piezas.append(
            {
                "name": os.path.basename(ruta),
                "path": ruta,
                "width_mm": medidas["ancho_mm"],
                "height_mm": medidas["alto_mm"],
                "resolucion_dpi": (
                    medidas.get("resolucion_x_dpi"),
                    medidas.get("resolucion_y_dpi"),
                )
                if "resolucion_x_dpi" in medidas and "resolucion_y_dpi" in medidas
                else None,
            }
        )
    return piezas, errores, False


def _validar_resoluciones(piezas):
    resolutions = {piece["resolucion_dpi"] for piece in piezas}
    return len(piezas) <= 1 or (None not in resolutions and len(resolutions) <= 1)


def _error_resolucion():
    return [
        "No se pueden combinar trabajos con distintas resoluciones DPI "
        "o sin resolución verificable."
    ]


def optimizar_archivos(lista_archivos, config=None, cancel_event=None):
    piezas, errores, cancelled = _leer_piezas(lista_archivos, cancel_event)
    if cancelled:
        return {"placa_unica": None, "placas_dobles": None, "errores": [], "cancelado": True}

    if not _validar_resoluciones(piezas):
        return {
            "placa_unica": None,
            "placas_dobles": None,
            "errores": _error_resolucion(),
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
    return solution["tipo"], round(solution["ancho"], 1), round(solution["alto"], 1), panels, placements


def generar_alternativas_archivos(
    lista_archivos,
    config=None,
    cancel_event=None,
    *,
    variant_batch=None,
    existing_alternatives=(),
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

    pieces, errors, cancelled = _leer_piezas(lista_archivos, cancel_event)
    if cancelled:
        return {"alternativas": [], "errores": [], "cancelado": True}
    if not _validar_resoluciones(pieces):
        return {"alternativas": [], "errores": _error_resolucion(), "cancelado": False}
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
    alternatives = []
    seen = set(known)
    ratio_rejected = False
    for profile_index, (
        profile_name,
        target_ratio,
        area_tolerance,
        group_dimensions,
        profile_rotation,
    ) in enumerate(profiles):
        if _cancelled(cancel_event):
            return {"alternativas": [], "errores": [], "cancelado": True}
        profile_config = {
            **base_config,
            "prioridad_aspecto": area_tolerance,
            "ratio_objetivo": target_ratio,
            "agrupar_dimensiones": group_dimensions,
            "rotacion": profile_rotation,
            "ratio_maximo": max_ratio,
        }
        result = optimizar_piezas(
            pieces, profile_config, cancel_event=cancel_event
        )
        if result.get("cancelado") or _cancelled(cancel_event):
            return {"alternativas": [], "errores": [], "cancelado": True}
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



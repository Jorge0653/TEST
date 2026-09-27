"""Suggest dimension-safe subsets with several bounded layout profiles."""

import os

from .metadata import extraer_medidas_xmp
from .packing import optimizar_piezas


def _dimension_key(piece):
    return tuple(
        sorted((round(piece["width_mm"], 3), round(piece["height_mm"], 3)))
    )


def _recommendation_rank(option):
    return (
        option["_eficiencia_exacta"],
        -option["_grupos_dimension"],
        option["_dimension_group_size"],
    )


def _store_option(options_by_count, option, variants_per_count=3):
    bucket = options_by_count.setdefault(option["cantidad"], [])
    signature = (
        option.get("resolucion_dpi"),
        option["_dimension_signature"],
        option["_layout_signature"],
    )
    for index, existing in enumerate(bucket):
        existing_signature = (
            existing.get("resolucion_dpi"),
            existing["_dimension_signature"],
            existing["_layout_signature"],
        )
        if existing_signature == signature:
            if _recommendation_rank(option) > _recommendation_rank(existing):
                bucket[index] = option
            break
    else:
        bucket.append(option)
    bucket.sort(key=_recommendation_rank, reverse=True)
    del bucket[variants_per_count:]


def _profile_configs(config):
    max_ratio = min(10.0, max(1.0, float(config.get("ratio_maximo", 3.0))))
    return (
        {
            **config,
            "ratio_maximo": max_ratio,
            "ratio_objetivo": 1.0,
            "prioridad_aspecto": 0,
            "agrupar_dimensiones": True,
        },
    )


def _store_subset_options(
    options_by_count,
    chosen,
    dimension_groups,
    profiles,
    cancel_event=None,
    buscar_ancho=False,
):
    if len(chosen) < 2:
        return
    dimension_signature = tuple(sorted(_dimension_key(item) for item in chosen))
    group_count = len(set(dimension_signature))
    largest_group = max(len(dimension_groups[key]) for key in set(dimension_signature))

    for profile in profiles:
        if cancel_event and cancel_event.is_set():
            return
        result = optimizar_piezas(
            chosen,
            profile,
            incluir_dividida=False,
            buscar_ancho=buscar_ancho,
            cancel_event=cancel_event,
        )
        board = result["placa_unica"]
        if board is None or board.get("ratio_fuera_limite", False):
            continue
        layout_signature = tuple(
            sorted(
                (
                    round(part["x"], 1),
                    round(part["y"], 1),
                    round(part["w"], 1),
                    round(part["h"], 1),
                    bool(part.get("rotado")),
                )
                for part in board["grabados"]
            )
        )
        _store_option(
            options_by_count,
            {
                "archivos": [item["path"] for item in chosen],
                "nombres": [item["name"] for item in chosen],
                "cantidad": len(chosen),
                "eficiencia": board["eficiencia"],
                "_eficiencia_exacta": board["eficiencia_exacta"],
                "ancho": board["ancho"],
                "alto": board["alto"],
                "resolucion_dpi": chosen[0].get("resolucion_dpi"),
                "_grupos_dimension": group_count,
                "_dimension_group_size": largest_group,
                "_dimension_signature": dimension_signature,
                "_layout_signature": layout_signature,
            },
        )


def _best_options_for_group(pieces, config, cancel_event=None):
    sort_orders = (
        lambda item: item["width_mm"] * item["height_mm"],
        lambda item: max(item["width_mm"], item["height_mm"]),
        lambda item: item["height_mm"] / item["width_mm"],
        lambda item: item["width_mm"],
        lambda item: item["height_mm"],
    )
    dimension_groups = {}
    for piece in pieces:
        dimension_groups.setdefault(_dimension_key(piece), []).append(piece)

    profiles = _profile_configs(config)
    subset_candidates = {}

    def retain_subset(chosen):
        if len(chosen) < 2:
            return
        signature = tuple(sorted(_dimension_key(item) for item in chosen))
        bucket = subset_candidates.setdefault(len(chosen), [])
        if any(candidate[0] == signature for candidate in bucket):
            return
        group_count = len(set(signature))
        largest_group = max(len(dimension_groups[key]) for key in set(signature))
        bucket.append((signature, list(chosen), group_count, largest_group))
        bucket.sort(key=lambda candidate: (candidate[2], -candidate[3]))
        del bucket[3:]

    probe_profile = profiles[0]
    full_search_counts = set()
    for sort_key in sort_orders:
        if cancel_event and cancel_event.is_set():
            return {}
        ordered = sorted(pieces, key=sort_key, reverse=True)
        chosen = []
        for piece in ordered:
            if cancel_event and cancel_event.is_set():
                return {}
            trial = chosen + [piece]
            probe = optimizar_piezas(
                trial,
                probe_profile,
                incluir_dividida=False,
                buscar_ancho=False,
                cancel_event=cancel_event,
            )
            board = probe["placa_unica"]
            if board is None:
                if len(trial) in full_search_counts:
                    continue
                full_search_counts.add(len(trial))
                probe = optimizar_piezas(
                    trial,
                    probe_profile,
                    incluir_dividida=False,
                    buscar_ancho=True,
                    cancel_event=cancel_event,
                )
                board = probe["placa_unica"]
            if board is None:
                continue
            chosen = trial
            retain_subset(chosen)

    for group in dimension_groups.values():
        if cancel_event and cancel_event.is_set():
            return {}
        for count in range(2, len(group) + 1):
            retain_subset(group[:count])

    for base_index, base_group in enumerate(dimension_groups.values()):
        for other_index, other_group in enumerate(dimension_groups.values()):
            if cancel_event and cancel_event.is_set():
                return {}
            if base_index == other_index:
                continue
            for added_count in range(1, len(other_group) + 1):
                retain_subset([*base_group, *other_group[:added_count]])
    retain_subset(pieces)

    options_by_count = {}
    for candidates in subset_candidates.values():
        for _, chosen, _, _ in candidates:
            if cancel_event and cancel_event.is_set():
                return {}
            _store_subset_options(
                options_by_count,
                chosen,
                dimension_groups,
                profiles,
                cancel_event,
                buscar_ancho=False,
            )
    return options_by_count


def sugerir_combinaciones(
    lista_archivos,
    config=None,
    por_categoria=None,
    max_sugerencias=60,
    cancel_event=None,
):
    if por_categoria is not None:
        max_sugerencias = min(max_sugerencias, max(1, int(por_categoria) * 3))
    search_config = dict(config or {})
    pieces = []
    errors = []
    for path in lista_archivos:
        if cancel_event and cancel_event.is_set():
            return {"sugerencias": [], "errores": [], "cancelado": True}
        measurements = extraer_medidas_xmp(path)
        if measurements is None:
            errors.append(f"No se pudieron leer las dimensiones de {os.path.basename(path)}.")
            continue
        pieces.append(
            {
                "name": os.path.basename(path),
                "path": path,
                "width_mm": measurements["ancho_mm"],
                "height_mm": measurements["alto_mm"],
                "resolucion_dpi": (
                    measurements["resolucion_x_dpi"],
                    measurements["resolucion_y_dpi"],
                )
                if "resolucion_x_dpi" in measurements
                and "resolucion_y_dpi" in measurements
                else None,
            }
        )

    if len(pieces) < 2:
        return {"sugerencias": [], "errores": errors}

    options_by_count = {}
    resolution_groups = {}
    for piece in pieces:
        resolution_groups.setdefault(piece["resolucion_dpi"], []).append(piece)
    for group in resolution_groups.values():
        if cancel_event and cancel_event.is_set():
            return {"sugerencias": [], "errores": [], "cancelado": True}
        options = _best_options_for_group(group, search_config, cancel_event)
        for candidates in options.values():
            for option in candidates:
                _store_option(options_by_count, option)

    if not options_by_count:
        errors.append("No se encontraron propuestas dentro del ratio máximo configurado.")
        return {"sugerencias": [], "errores": errors}

    counts = sorted(options_by_count)
    limit = min(max(1, int(max_sugerencias)), len(counts))
    preferred = [count for count in counts if count <= 10][:limit]
    remaining = [count for count in counts if count not in preferred]
    slots = limit - len(preferred)
    if slots >= len(remaining):
        selected_counts = preferred + remaining
    elif slots == 1:
        selected_counts = preferred + [remaining[-1]]
    elif slots > 1:
        indices = [
            round(index * (len(remaining) - 1) / (slots - 1))
            for index in range(slots)
        ]
        selected_counts = preferred + [remaining[index] for index in indices]
    else:
        selected_counts = preferred

    suggestions = []
    for count in sorted(selected_counts):
        for variant_index in range(3):
            options = options_by_count[count]
            if variant_index >= len(options):
                continue
            option = dict(options[variant_index])
            for key in (
                "_eficiencia_exacta",
                "_grupos_dimension",
                "_dimension_group_size",
                "_dimension_signature",
                "_layout_signature",
            ):
                option.pop(key, None)
            option["variante"] = variant_index + 1
            suggestions.append(option)
            if len(suggestions) >= max_sugerencias:
                return {"sugerencias": suggestions, "errores": errors}

    return {"sugerencias": suggestions, "errores": errors}
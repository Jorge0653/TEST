"""Suggest dimension-safe subsets with several bounded layout profiles."""

import os
from collections import Counter

from .metadata import extraer_medidas_xmp
from .packing import optimizar_piezas
from .scanner import extraer_material_fotopolimero

def _dimension_key(piece):
    return tuple(
        sorted((round(piece["width_mm"], 3), round(piece["height_mm"], 3)))
    )


def _dimension_match_counts(pieces, tolerance_mm=0.0):
    counts = Counter(_dimension_key(piece) for piece in pieces)
    if not counts:
        return 0, 0
    if tolerance_mm <= 0:
        return max(counts.values()), 0

    matches = []
    for anchor, exact_count in counts.items():
        near_count = sum(
            count
            for candidate, count in counts.items()
            if candidate != anchor
            and max(
                abs(anchor[0] - candidate[0]),
                abs(anchor[1] - candidate[1]),
            )
            <= tolerance_mm
        )
        matches.append((exact_count + near_count, exact_count, near_count))
    _, exact_count, near_count = max(matches)
    return exact_count, near_count


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
        option.get("material_fotopolimero"),
        option["_dimension_signature"],
        option["_layout_signature"],
    )
    for index, existing in enumerate(bucket):
        existing_signature = (
            existing.get("resolucion_dpi"),
            existing.get("material_fotopolimero"),
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
    balanced_ratio = min(1.5, max_ratio)
    profiles = (
        (1.0, 0, False),
        (balanced_ratio, 50, False),
        (max_ratio, 100, False),
        (balanced_ratio, 50, True),
    )
    return tuple(
        {
            **config,
            "ratio_maximo": max_ratio,
            "ratio_objetivo": ratio,
            "prioridad_aspecto": priority,
            "agrupar_dimensiones": group_dimensions,
        }
        for ratio, priority, group_dimensions in profiles
    )


def _limite_candidatos_sugerencia(piece_count):
    if piece_count <= 128:
        return 16
    if piece_count <= 256:
        return 9
    return 3


def _store_subset_options(
    options_by_count,
    chosen,
    dimension_groups,
    profiles,
    cancel_event=None,
    buscar_ancho=False,
    dimension_tolerance=0.0,
    candidate_callback=None,
    allow_singletons=False,
):
    if not chosen or (len(chosen) < 2 and not allow_singletons):
        return
    dimension_signature = tuple(sorted(_dimension_key(item) for item in chosen))
    group_count = len(set(dimension_signature))
    largest_group = max(len(dimension_groups[key]) for key in set(dimension_signature))
    exact_count, tolerance_count = _dimension_match_counts(
        chosen, dimension_tolerance
    )

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
                "material_fotopolimero": chosen[0].get("material_fotopolimero"),
                "coincidencias_exactas": exact_count,
                "coincidencias_tolerancia": tolerance_count,
                "tolerancia_tamanos_mm": dimension_tolerance,
                "_grupos_dimension": group_count,
                "_dimension_group_size": largest_group,
                "_dimension_signature": dimension_signature,
                "_layout_signature": layout_signature,
            },
        )
        if candidate_callback:
            candidate_callback(chosen, board)


def _best_options_for_group(
    pieces,
    config,
    cancel_event=None,
    progress_callback=None,
    candidate_callback=None,
    allow_singletons=False,
    candidate_limit=3,
):
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
    dimension_tolerance = (
        float(config.get("tolerancia_tamanos_cercanos", 1.0))
        if config.get("agrupar_tamanos_cercanos", False)
        else 0.0
    )

    profiles = _profile_configs(config)
    subset_candidates = {}
    minimum_subset_size = 1 if allow_singletons else 2

    def retain_subset(chosen, tolerance_candidate=False):
        if len(chosen) < minimum_subset_size:
            return
        signature = tuple(sorted(_dimension_key(item) for item in chosen))
        bucket = subset_candidates.setdefault(
            (len(chosen), tolerance_candidate), []
        )
        if any(candidate[0] == signature for candidate in bucket):
            return
        group_count = len(set(signature))
        largest_group = max(len(dimension_groups[key]) for key in set(signature))
        bucket.append((signature, list(chosen), group_count, largest_group))
        bucket.sort(key=lambda candidate: (candidate[2], -candidate[3]))
        del bucket[max(1, int(candidate_limit)) :]

    probe_profile = profiles[0]
    full_search_counts = set()
    for sort_index, sort_key in enumerate(sort_orders):
        if cancel_event and cancel_event.is_set():
            return {}
        ordered = sorted(pieces, key=sort_key, reverse=True)
        chosen = []
        for piece_index, piece in enumerate(ordered, start=1):
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
            if progress_callback:
                fraction = (sort_index + piece_index / len(ordered)) / len(sort_orders)
                progress_callback(
                    round(10 + fraction * 45),
                    f"Probando acomodos: orden {sort_index + 1}/{len(sort_orders)}, "
                    f"{piece_index}/{len(ordered)} grabados",
                )
            if board is None:
                continue
            chosen = trial
            retain_subset(chosen)

    dimension_group_values = list(dimension_groups.values())
    for group_index, group in enumerate(dimension_group_values, start=1):
        if cancel_event and cancel_event.is_set():
            return {}
        for count in range(minimum_subset_size, len(group) + 1):
            retain_subset(group[:count])
        if progress_callback:
            progress_callback(
                55 + round(group_index / max(1, len(dimension_group_values)) * 6),
                f"Agrupando tamaños: {group_index}/{len(dimension_group_values)}",
            )

    total_pairs = max(1, len(dimension_group_values) * (len(dimension_group_values) - 1))
    pair_index = 0
    for base_index, base_group in enumerate(dimension_group_values):
        for other_index, other_group in enumerate(dimension_group_values):
            if cancel_event and cancel_event.is_set():
                return {}
            if base_index == other_index:
                continue
            for added_count in range(1, len(other_group) + 1):
                retain_subset([*base_group, *other_group[:added_count]])
            pair_index += 1
            if progress_callback:
                progress_callback(
                    61 + round(pair_index / total_pairs * 16),
                    f"Combinando tamaños: {pair_index}/{total_pairs}",
                )

    if dimension_tolerance > 0:
        for anchor_group in dimension_group_values:
            anchor = _dimension_key(anchor_group[0])
            nearby_groups = [
                (
                    max(
                        abs(anchor[0] - candidate_key[0]),
                        abs(anchor[1] - candidate_key[1]),
                    ),
                    candidate_group,
                )
                for candidate_key, candidate_group in dimension_groups.items()
                if candidate_key != anchor
                and max(
                    abs(anchor[0] - candidate_key[0]),
                    abs(anchor[1] - candidate_key[1]),
                )
                <= dimension_tolerance
            ]
            nearby_groups.sort(
                key=lambda match: (match[0], _dimension_key(match[1][0]))
            )
            chosen = list(anchor_group)
            for _, nearby_group in nearby_groups:
                for added_count in range(1, len(nearby_group) + 1):
                    retain_subset(
                        [*chosen, *nearby_group[:added_count]],
                        tolerance_candidate=True,
                    )
                chosen.extend(nearby_group)

    retain_subset(pieces)

    options_by_count = {}
    candidates_to_pack = [
        candidate
        for candidates in subset_candidates.values()
        for candidate in candidates
    ]
    for candidate_index, candidate in enumerate(candidates_to_pack, start=1):
        if cancel_event and cancel_event.is_set():
            return {}
        chosen = candidate[1]
        _store_subset_options(
            options_by_count,
            chosen,
            dimension_groups,
            profiles,
            cancel_event,
            buscar_ancho=False,
            dimension_tolerance=dimension_tolerance,
            candidate_callback=candidate_callback,
            allow_singletons=allow_singletons,
        )
        if progress_callback:
            progress_callback(
                77 + round(candidate_index / max(1, len(candidates_to_pack)) * 21),
                f"Validando propuestas: {candidate_index}/{len(candidates_to_pack)}",
            )
    return options_by_count


def sugerir_combinaciones(
    lista_archivos,
    config=None,
    por_categoria=None,
    max_sugerencias=60,
    cancel_event=None,
    progress_callback=None,
):
    if por_categoria is not None:
        max_sugerencias = min(max_sugerencias, max(1, int(por_categoria) * 3))
    search_config = dict(config or {})
    pieces = []
    errors = []
    total_files = len(lista_archivos)
    for index, path in enumerate(lista_archivos, start=1):
        if cancel_event and cancel_event.is_set():
            return {"sugerencias": [], "errores": [], "cancelado": True}
        measurements = extraer_medidas_xmp(path)
        if measurements is None:
            errors.append(f"No se pudieron leer las dimensiones de {os.path.basename(path)}.")
        else:
            pieces.append(
                {
                    "name": os.path.basename(path),
                    "path": path,
                    "width_mm": measurements["ancho_mm"],
                    "height_mm": measurements["alto_mm"],
                    "material_fotopolimero": extraer_material_fotopolimero(path),
                    "resolucion_dpi": (
                        measurements["resolucion_x_dpi"],
                        measurements["resolucion_y_dpi"],
                    )
                    if "resolucion_x_dpi" in measurements
                    and "resolucion_y_dpi" in measurements
                    else None,
                }
            )
        if progress_callback:
            progress_callback(
                round(index / max(1, total_files) * 15),
                f"Leyendo archivos: {index}/{total_files}",
            )

    if len(pieces) < 2:
        if progress_callback:
            progress_callback(100, "No hay suficientes archivos para sugerir combinaciones")
        return {"sugerencias": [], "errores": errors}

    options_by_count = {}
    compatibility_groups = {}
    for piece in pieces:
        compatibility_groups.setdefault(
            (piece["resolucion_dpi"], piece["material_fotopolimero"]), []
        ).append(piece)
    if progress_callback:
        progress_callback(16, "Agrupando archivos por resolución y material")
    compatibility_groups_list = list(compatibility_groups.values())
    for group_index, group in enumerate(compatibility_groups_list):
        if cancel_event and cancel_event.is_set():
            return {"sugerencias": [], "errores": [], "cancelado": True}

        def report_group_progress(local_percent, message):
            if progress_callback:
                fraction = (group_index + local_percent / 100) / max(
                    1, len(compatibility_groups_list)
                )
                progress_callback(
                    min(98, 16 + round(fraction * 81)),
                    f"Grupo {group_index + 1}/{len(compatibility_groups_list)} · {message}",
                )

        options = _best_options_for_group(
            group,
            search_config,
            cancel_event,
            progress_callback=report_group_progress,
            candidate_limit=_limite_candidatos_sugerencia(len(group)),
        )
        for candidates in options.values():
            for option in candidates:
                _store_option(options_by_count, option)

    if not options_by_count:
        errors.append("No se encontraron propuestas dentro del ratio máximo configurado.")
        if progress_callback:
            progress_callback(100, "No se encontraron combinaciones viables")
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
                if progress_callback:
                    progress_callback(100, "Sugerencias listas")
                return {"sugerencias": suggestions, "errores": errors}

    if progress_callback:
        progress_callback(100, "Sugerencias listas")
    return {"sugerencias": suggestions, "errores": errors}
"""Heuristic rectangle packing for CDI plate layouts."""

from __future__ import annotations

import math

_METADATA_KEYS = (
    "tinta_nombre",
    "color_rgb",
    "lineaje_lpi",
    "angulo_grados",
    "punto_trama",
)


def _extra_item_metadata(item):
    return {key: item[key] for key in _METADATA_KEYS if key in item and item[key] is not None}


def _intersects(first, second):
    return not (
        first[0] >= second[0] + second[2]
        or first[0] + first[2] <= second[0]
        or first[1] >= second[1] + second[3]
        or first[1] + first[3] <= second[1]
    )


def _contains(first, second):
    return (
        first[0] <= second[0]
        and first[1] <= second[1]
        and first[0] + first[2] >= second[0] + second[2]
        and first[1] + first[3] >= second[1] + second[3]
    )


def _overlaps_parts(first, second, spacing=0):
    return (
        first["x"] < second["x"] + second["w"] + spacing
        and first["x"] + first["w"] > second["x"] - spacing
        and first["y"] < second["y"] + second["h"] + spacing
        and first["y"] + first["h"] > second["y"] - spacing
    )


def _compact_bottom_left(parts, width, height, spacing):
    """Slide placements down and left without changing their dimensions."""
    compacted = [dict(part) for part in parts]
    for _ in range(max(1, len(compacted) * 2)):
        changed = False
        for part in sorted(compacted, key=lambda item: (-item["y"], item["x"])):
            px, pw = part["x"], part["w"]
            # Solo las piezas que se solapan horizontalmente pueden colisionar al deslizar en Y
            x_neighbors = [
                other
                for other in compacted
                if other is not part
                and px < other["x"] + other["w"] + spacing
                and px + pw > other["x"] - spacing
            ]
            y_candidates = {part["y"], height - part["h"]}
            for other in x_neighbors:
                if px < other["x"] + other["w"] and px + pw > other["x"]:
                    y_candidates.update(
                        (
                            other["y"] - part["h"] - spacing,
                            other["y"] + other["h"] + spacing,
                        )
                    )
            valid_y = []
            for candidate_y in y_candidates:
                if (
                    candidate_y < part["y"] - 1e-7
                    or candidate_y < 0
                    or candidate_y + part["h"] > height + 1e-7
                ):
                    continue
                candidate = {**part, "y": candidate_y}
                if not any(_overlaps_parts(candidate, other, spacing) for other in x_neighbors):
                    valid_y.append(candidate_y)
            if valid_y:
                new_y = max(valid_y)
                changed = changed or new_y > part["y"] + 1e-7
                part["y"] = new_y

        for part in sorted(compacted, key=lambda item: (item["y"], item["x"])):
            py, ph = part["y"], part["h"]
            # Solo las piezas que se solapan verticalmente pueden colisionar al deslizar en X
            y_neighbors = [
                other
                for other in compacted
                if other is not part
                and py < other["y"] + other["h"] + spacing
                and py + ph > other["y"] - spacing
            ]
            x_candidates = {part["x"], 0.0}
            for other in y_neighbors:
                if py < other["y"] + other["h"] and py + ph > other["y"]:
                    x_candidates.update(
                        (
                            other["x"] - part["w"] - spacing,
                            other["x"] + other["w"] + spacing,
                        )
                    )
            valid_x = []
            for candidate_x in x_candidates:
                if (
                    candidate_x > part["x"] + 1e-7
                    or candidate_x < 0
                    or candidate_x + part["w"] > width + 1e-7
                ):
                    continue
                candidate = {**part, "x": candidate_x}
                if not any(_overlaps_parts(candidate, other, spacing) for other in y_neighbors):
                    valid_x.append(candidate_x)
            if valid_x:
                new_x = min(valid_x)
                changed = changed or new_x < part["x"] - 1e-7
                part["x"] = new_x
        if not changed:
            break
    return compacted


def _split_free_rectangles(free_rectangles, used):
    used_x, used_y, used_w, used_h = used
    used_x2 = used_x + used_w
    used_y2 = used_y + used_h

    untouched = []
    new_splits = []

    for free in free_rectangles:
        free_x, free_y, free_w, free_h = free
        free_x2 = free_x + free_w
        free_y2 = free_y + free_h

        if (
            free_x >= used_x2
            or free_x2 <= used_x
            or free_y >= used_y2
            or free_y2 <= used_y
        ):
            untouched.append((free_x, free_y, free_w, free_h, free_x2, free_y2))
            continue

        if used_x < free_x2 and used_x2 > free_x:
            if used_y > free_y and used_y < free_y2:
                h = used_y - free_y
                if h > 0:
                    new_splits.append((free_x, free_y, free_w, h, free_x2, used_y))
            if used_y2 < free_y2:
                h = free_y2 - used_y2
                if h > 0:
                    new_splits.append((free_x, used_y2, free_w, h, free_x2, free_y2))
        if used_y < free_y2 and used_y2 > free_y:
            if used_x > free_x and used_x < free_x2:
                w = used_x - free_x
                if w > 0:
                    new_splits.append((free_x, free_y, w, free_h, used_x, free_y2))
            if used_x2 < free_x2:
                w = free_x2 - used_x2
                if w > 0:
                    new_splits.append((used_x2, free_y, w, free_h, free_x2, free_y2))

    if not new_splits:
        return [(r[0], r[1], r[2], r[3]) for r in untouched]

    # Ordenar recortes nuevos por área descendente: cualquier recorte contenido
    # en otro tendrá área menor o igual y aparecerá después.
    new_splits.sort(key=lambda r: r[2] * r[3], reverse=True)
    accepted_new = []
    for rect in new_splits:
        rx, ry, _, _, rx2, ry2 = rect
        contained = False
        for ox, oy, _, _, ox2, oy2 in untouched:
            if ox <= rx and oy <= ry and ox2 >= rx2 and oy2 >= ry2:
                contained = True
                break
        if contained:
            continue
        for ox, oy, _, _, ox2, oy2 in accepted_new:
            if ox <= rx and oy <= ry and ox2 >= rx2 and oy2 >= ry2:
                contained = True
                break
        if not contained:
            accepted_new.append(rect)

    pruned = [(r[0], r[1], r[2], r[3]) for r in untouched]
    pruned.extend((r[0], r[1], r[2], r[3]) for r in accepted_new)
    return pruned


def _sort_items(items, order):
    if order == "given":
        return list(items)
    keys = {
        "area": lambda item: item["width_mm"] * item["height_mm"],
        "long_side": lambda item: max(item["width_mm"], item["height_mm"]),
        "short_side": lambda item: min(item["width_mm"], item["height_mm"]),
        "width": lambda item: item["width_mm"],
        "height": lambda item: item["height_mm"],
    }
    keys["dimensions"] = lambda item: (
        round(item["width_mm"], 3),
        round(item["height_mm"], 3),
    )
    return sorted(items, key=lambda item: keys[order](item), reverse=True)


def _pack_order(
    items,
    bin_width,
    bin_height,
    spacing,
    allow_rotation,
    order,
    target_ratio=1.0,
    cancel_event=None,
    priority=0,
):
    free_rectangles = [(0.0, 0.0, bin_width, bin_height)]
    placed = []
    used_width = 0.0
    used_height = 0.0
    aspect_weight = 0.03 * (priority / 100.0) if priority > 0 else 0.0

    for item in _sort_items(items, order):
        if cancel_event and cancel_event.is_set():
            return None
        width = float(item["width_mm"]) + 2 * spacing
        height = float(item["height_mm"]) + 2 * spacing
        candidates = [(width, height, False)]
        if allow_rotation and width != height:
            candidates.append((height, width, True))
        best = None
        for free_index, free in enumerate(free_rectangles):
            for candidate_width, candidate_height, rotated in candidates:
                leftover_width = free[2] - candidate_width
                leftover_height = free[3] - candidate_height
                if leftover_width < -1e-7 or leftover_height < -1e-7:
                    continue
                bounds_width = max(used_width, free[0] + candidate_width)
                bounds_height = max(used_height, free[1] + candidate_height)
                aspect_ratio = bounds_height / bounds_width
                ratio_distance = abs(math.log(aspect_ratio / target_ratio))
                envelope_cost = (bounds_width * bounds_height) * (
                    1.0 + aspect_weight * min(1.0, ratio_distance)
                )
                score = (
                    envelope_cost,
                    ratio_distance,
                    min(leftover_width, leftover_height),
                    max(leftover_width, leftover_height),
                    free[1],
                    free[0],
                    int(rotated),
                )
                if best is None or score < best[0]:
                    best = (
                        score,
                        free_index,
                        candidate_width,
                        candidate_height,
                        rotated,
                        free,
                    )
        if best is None:
            return None
        _, _, placed_width, placed_height, rotated, free = best
        used = (free[0], free[1], placed_width, placed_height)
        free_rectangles = _split_free_rectangles(free_rectangles, used)
        actual_width = float(item["height_mm"] if rotated else item["width_mm"])
        actual_height = float(item["width_mm"] if rotated else item["height_mm"])
        used_width = max(used_width, used[0] + placed_width)
        used_height = max(used_height, used[1] + placed_height)
        placed.append(
            {
                "nombre": item.get("name", "Grabado"),
                "ruta": item.get("path"),
                "x": used[0],
                "y": used[1],
                "w": actual_width + 2 * spacing,
                "h": actual_height + 2 * spacing,
                "rotado": rotated,
                **_extra_item_metadata(item),
            }
        )

    used_width = max(part["x"] + part["w"] for part in placed)
    used_height = max(part["y"] + part["h"] for part in placed)
    placed = _compact_bottom_left(placed, used_width, used_height, 0)
    used_width = max(part["x"] + part["w"] for part in placed)
    used_height = max(part["y"] + part["h"] for part in placed)
    actual_parts = [
        {
            **part,
            "x": part["x"] + spacing,
            "y": part["y"] + spacing,
            "w": part["w"] - 2 * spacing,
            "h": part["h"] - 2 * spacing,
        }
        for part in placed
    ]
    return {
        "grabados": actual_parts,
        "ancho_util": used_width,
        "alto_util": used_height,
    }


def _area_without_spacing(packed):
    cached = packed.get("_area_sin_espaciado")
    if cached is not None:
        return cached
    parts = packed["grabados"]
    if not parts:
        packed["_area_sin_espaciado"] = 0
        return 0
    compacted = _compact_bottom_left(
        parts, packed["ancho_util"], packed["alto_util"], 0
    )
    left = min(part["x"] for part in compacted)
    top = min(part["y"] for part in compacted)
    right = max(part["x"] + part["w"] for part in compacted)
    bottom = max(part["y"] + part["h"] for part in compacted)
    area = (right - left) * (bottom - top)
    packed["_area_sin_espaciado"] = area
    return area


def _identical_grid_candidates(
    items, max_width, max_height, spacing, allow_rotation, cancel_event=None
):
    if not items:
        return []
    first = items[0]
    if any(
        abs(item["width_mm"] - first["width_mm"]) > 1e-3
        or abs(item["height_mm"] - first["height_mm"]) > 1e-3
        for item in items[1:]
    ):
        return []
    orientations = [(first["width_mm"], first["height_mm"], False)]
    if allow_rotation and abs(first["width_mm"] - first["height_mm"]) > 1e-9:
        orientations.append((first["height_mm"], first["width_mm"], True))
    candidates = []
    for width, height, rotated in orientations:
        padded_width = width + 2 * spacing
        padded_height = height + 2 * spacing
        for columns in range(1, len(items) + 1):
            if cancel_event and cancel_event.is_set():
                return candidates
            rows = math.ceil(len(items) / columns)
            used_width = columns * padded_width
            used_height = rows * padded_height
            if used_width > max_width + 1e-7 or used_height > max_height + 1e-7:
                continue
            placed = []
            for index, item in enumerate(items):
                if cancel_event and cancel_event.is_set():
                    return candidates
                column = index % columns
                row = index // columns
                placed.append(
                    {
                        "nombre": item.get("name", "Grabado"),
                        "ruta": item.get("path"),
                        "x": column * padded_width + spacing,
                        "y": row * padded_height + spacing,
                        "w": width,
                        "h": height,
                        "rotado": rotated,
                        **_extra_item_metadata(item),
                    }
                )
            candidates.append(
                {
                    "grabados": placed,
                    "ancho_util": used_width,
                    "alto_util": used_height,
                }
            )
    return candidates


def _transpose_packed(packed, spacing=0):
    if packed is None:
        return None
    width = packed["alto_util"]
    height = packed["ancho_util"]
    transposed = [
        {
            **part,
            "x": width - part["y"] - part["h"],
            "y": part["x"],
            "w": part["h"],
            "h": part["w"],
            "rotado": not part["rotado"],
        }
        for part in packed["grabados"]
    ]
    padded = [
        {
            **part,
            "x": part["x"] - spacing,
            "y": part["y"] - spacing,
            "w": part["w"] + 2 * spacing,
            "h": part["h"] + 2 * spacing,
        }
        for part in transposed
    ]
    compacted = _compact_bottom_left(padded, width, height, 0)
    return {
        "grabados": [
            {
                **part,
                "x": part["x"] + spacing,
                "y": part["y"] + spacing,
                "w": part["w"] - 2 * spacing,
                "h": part["h"] - 2 * spacing,
            }
            for part in compacted
        ],
        "ancho_util": width,
        "alto_util": height,
    }


def _candidate_widths(items, max_width, spacing, allow_rotation):
    lower_bound = (
        max(
            min(item["width_mm"], item["height_mm"])
            if allow_rotation
            else item["width_mm"]
            for item in items
        )
        + 2 * spacing
    )
    if lower_bound > max_width:
        return []
    candidates = {float(max_width), float(lower_bound)}
    for fraction in (0.25, 0.4, 0.55, 0.7, 0.85):
        candidates.add(lower_bound + (max_width - lower_bound) * fraction)
    candidates.update(
        min(max_width, float(item["width_mm"]) + 2 * spacing) for item in items
    )
    repeated_widths = {}
    for item in items:
        orientations = [(item["width_mm"], item["height_mm"])]
        if allow_rotation and item["width_mm"] != item["height_mm"]:
            orientations.append((item["height_mm"], item["width_mm"]))
        for width, _ in orientations:
            padded_width = width + 2 * spacing
            repeated_widths[padded_width] = repeated_widths.get(padded_width, 0) + 1
    for width, repetitions in repeated_widths.items():
        for columns in range(2, min(repetitions, len(items)) + 1):
            candidates.add(width * columns)
    return sorted(width for width in candidates if lower_bound <= width <= max_width)


def _best_pack(
    items,
    max_width,
    max_height,
    spacing,
    allow_rotation,
    width_search=True,
    priority=0,
    target_ratio=1.0,
    group_dimensions=False,
    cancel_event=None,
    max_ratio=10.0,
):
    if not items:
        return None
    widths = (
        _candidate_widths(items, max_width, spacing, allow_rotation)
        if width_search
        else [max_width]
    )
    if not widths:
        return None
    candidates = []
    orderings = ("area", "long_side", "short_side", "width", "height")
    if group_dimensions:
        orderings += ("dimensions",)
    priority_passes = (0, priority) if priority > 0 else (0,)
    for width in widths:
        if cancel_event and cancel_event.is_set():
            return None
        for order in orderings:
            for pass_priority in priority_passes:
                packed = _pack_order(
                    items,
                    width,
                    max_height,
                    spacing,
                    allow_rotation,
                    order,
                    target_ratio,
                    cancel_event,
                    priority=pass_priority,
                )
                if packed is None:
                    continue
                area = packed["ancho_util"] * packed["alto_util"]
                aspect_ratio = packed["alto_util"] / packed["ancho_util"]
                candidates.append(
                    (area, abs(math.log(aspect_ratio / target_ratio)), packed)
                )
    for packed in _identical_grid_candidates(
        items, max_width, max_height, spacing, allow_rotation, cancel_event
    ):
        area = packed["ancho_util"] * packed["alto_util"]
        aspect_ratio = packed["alto_util"] / packed["ancho_util"]
        candidates.append(
            (area, abs(math.log(aspect_ratio / target_ratio)), packed)
        )
    if not candidates:
        return None
    ratio_safe_candidates = [
        candidate
        for candidate in candidates
        if max(
            candidate[2]["alto_util"] / candidate[2]["ancho_util"],
            candidate[2]["ancho_util"] / candidate[2]["alto_util"],
        )
        <= max_ratio + 1e-9
    ]
    if ratio_safe_candidates:
        candidates = ratio_safe_candidates
    minimum_area = min(candidate[0] for candidate in candidates)
    area_tolerance = 0.03 * priority / 100
    eligible = [
        candidate
        for candidate in candidates
        if candidate[0] <= minimum_area * (1 + area_tolerance) + 1e-9
    ]
    best = min(eligible, key=lambda candidate: (candidate[1], candidate[0]))[2]
    if allow_rotation and any(
        item["width_mm"] != item["height_mm"] for item in items
    ):
        total_item_area = sum(item["width_mm"] * item["height_mm"] for item in items)
        if _area_without_spacing(best) > total_item_area + 1e-6:
            baseline = _best_pack(
                items,
                max_width,
                max_height,
                spacing,
                False,
                width_search,
                priority,
                target_ratio,
                group_dimensions,
                cancel_event,
                max_ratio,
            )
            if (
                baseline
                and _area_without_spacing(best) > _area_without_spacing(baseline) + 1e-9
            ):
                return baseline
    return best


def _as_board(packed, config):
    left = config["margen_izquierdo"]
    top = config["margen_superior"]
    right = config["margen_derecho"]
    clamp = config["tolerancia"]
    grip = config["pinza"]
    positioned = [
        {**part, "x": round(part["x"] + left, 2), "y": round(part["y"] + top, 2)}
        for part in packed["grabados"]
    ]
    width = round(packed["ancho_util"] + left + right + clamp, 2)
    height = round(packed["alto_util"] + top + grip + clamp, 2)
    item_area = sum(part["w"] * part["h"] for part in positioned)
    usable_area = max(0, packed["ancho_util"]) * max(0, packed["alto_util"])
    efficiency_area = _area_without_spacing(packed)
    efficiency = item_area / efficiency_area * 100 if efficiency_area else 0
    return {
        "ancho": width,
        "alto": height,
        "pinza": grip,
        "tolerancia": clamp,
        "area_utilizable": round(usable_area, 2),
        "margenes": {"superior": top, "izquierdo": left, "derecho": right},
        "grabados": positioned,
        "eficiencia": round(efficiency, 1),
        "eficiencia_exacta": efficiency,
        "area_aprovechable": round(efficiency_area, 2),
    }


def _two_plate_pack(items, config, max_width, max_height, cancel_event=None):
    if len(items) < 2:
        return None
    spacing = config["espaciado"]
    allow_rotation = config["rotacion"]
    priority = config["prioridad_aspecto"]
    target_ratio = config["target_ratio"]
    orderings = ("area", "long_side", "short_side", "width", "height")
    if config["agrupar_dimensiones"]:
        orderings += ("dimensions",)
    candidates = []
    strategies = [_sort_items(items, order) for order in orderings]
    dimension_groups = {}
    for item in items:
        dimensions = tuple(
            sorted((round(item["width_mm"], 3), round(item["height_mm"], 3)))
        )
        dimension_groups.setdefault(dimensions, []).append(item)
    groups = list(dimension_groups.values())

    def score_bins(bins):
        if len(bins) != 2 or not bins[0] or not bins[1]:
            return
        packed_bins = [
            _best_pack(
                bin_items,
                max_width,
                max_height,
                spacing,
                allow_rotation,
                priority=priority,
                target_ratio=target_ratio,
                group_dimensions=config["agrupar_dimensiones"],
                cancel_event=cancel_event,
                max_ratio=config["ratio_maximo"],
            )
            for bin_items in bins
        ]
        if any(packed is None for packed in packed_bins):
            return
        boards = [_as_board(packed, config) for packed in packed_bins]
        if any(
            (config["pinza"] > 0 and board["ancho"] > board["alto"] + 1e-7)
            or max(board["alto"] / board["ancho"], board["ancho"] / board["alto"])
            > config["ratio_maximo"] + 1e-9
            for board in boards
        ):
            return
        usable_area = sum(board["area_aprovechable"] for board in boards)
        shape_ratio = max(board["alto"] for board in boards) / sum(
            board["ancho"] for board in boards
        )
        if (
            not 1 / config["ratio_maximo"] - 1e-9
            <= shape_ratio
            <= config["ratio_maximo"] + 1e-9
        ):
            return
        mixing_penalty = sum(
            len(
                {
                    tuple(
                        sorted(
                            (round(item["width_mm"], 3), round(item["height_mm"], 3))
                        )
                    )
                    for item in bin_items
                }
            )
            - 1
            for bin_items in bins
        )
        candidates.append(
            (
                usable_area,
                mixing_penalty,
                abs(math.log(shape_ratio / target_ratio)),
                boards,
            )
        )

    if 1 < len(groups) <= 8:
        for mask in range(1 << (len(groups) - 1)):
            if cancel_event and cancel_event.is_set():
                return None
            first_bin = [*groups[0]]
            second_bin = []
            for index, group in enumerate(groups[1:]):
                if mask & (1 << index):
                    first_bin.extend(group)
                else:
                    second_bin.extend(group)
            if second_bin:
                score_bins([first_bin, second_bin])
    elif len(groups) > 8:
        all_items = list(items)
        for group in groups:
            if cancel_event and cancel_event.is_set():
                return None
            group_ids = {id(item) for item in group}
            remaining = [item for item in all_items if id(item) not in group_ids]
            score_bins([list(group), remaining])

    for ordered in strategies:
        if cancel_event and cancel_event.is_set():
            return None
        bins = [[ordered[0]], []]
        for item in ordered[1:]:
            if cancel_event and cancel_event.is_set():
                return None
            options = []
            for bin_index in range(2):
                trial = bins[bin_index] + [item]
                packed = _pack_order(
                    trial,
                    max_width,
                    max_height,
                    spacing,
                    allow_rotation,
                    "area",
                    target_ratio,
                    cancel_event,
                )
                if packed:
                    area = packed["ancho_util"] * packed["alto_util"]
                    options.append((area, bin_index))
            if not options:
                bins = []
                break
            _, bin_index = min(options)
            bins[bin_index].append(item)
        score_bins(bins)

    if not candidates:
        return None
    minimum_area = min(candidate[0] for candidate in candidates)
    area_tolerance = 0.03 * priority / 100
    eligible = [
        candidate
        for candidate in candidates
        if candidate[0] <= minimum_area * (1 + area_tolerance) + 1e-9
    ]
    _, _, _, boards = min(
        eligible, key=lambda candidate: (candidate[1], candidate[2], candidate[0])
    )
    assembled_width = round(sum(board["ancho"] for board in boards), 2)
    assembled_height = max(board["alto"] for board in boards)
    drawings = []
    offset_x = 0.0
    for plate_index, board in enumerate(boards, start=1):
        for part in board["grabados"]:
            drawings.append(
                {
                    **part,
                    "x": round(part["x"] + offset_x, 2),
                    "y": round(part["y"] + assembled_height - board["alto"], 2),
                    "placa": plate_index,
                }
            )
        offset_x += board["ancho"]
    area = sum(part["w"] * part["h"] for part in drawings)
    material_area = sum(board["area_aprovechable"] for board in boards)
    efficiency = area / material_area * 100 if material_area else 0
    return {
        "ancho": assembled_width,
        "alto": assembled_height,
        "pinza": config["pinza"],
        "tolerancia": config["tolerancia"],
        "margenes": {
            "superior": config["margen_superior"],
            "izquierdo": config["margen_izquierdo"],
            "derecho": config["margen_derecho"],
        },
        "grabados": drawings,
        "placas": boards,
        "eficiencia": round(efficiency, 1),
        "eficiencia_exacta": efficiency,
    }


def optimizar_piezas(
    piezas,
    config=None,
    incluir_dividida=True,
    buscar_ancho=True,
    cancel_event=None,
):
    """Return best single-plate and two-plate layouts for measured rectangles."""
    if not piezas:
        return {"placa_unica": None, "placas_dobles": None, "errores": []}
    if cancel_event and cancel_event.is_set():
        return {
            "placa_unica": None,
            "placas_dobles": None,
            "errores": [],
            "cancelado": True,
        }
    config = config or {}
    base_margin = float(config.get("margen", 7))
    settings = {
        "margen_izquierdo": float(config.get("margen_izquierdo", base_margin)),
        "margen_derecho": float(config.get("margen_derecho", base_margin)),
        "margen_superior": float(config.get("margen_superior", base_margin)),
        "pinza": float(config.get("pinza", 25)),
        "tolerancia": float(config.get("tolerancia", 3)),
        "espaciado": float(config.get("espaciado", 3)),
        "rotacion": bool(config.get("rotacion", True)),
        "prioridad_aspecto": min(100, max(0, int(config.get("prioridad_aspecto", 0)))),
        "ratio_maximo": min(10.0, max(1.0, float(config.get("ratio_maximo", 3.0)))),
        "agrupar_dimensiones": bool(config.get("agrupar_dimensiones", False)),
    }
    max_width = float(config.get("tambor_w", 1067))
    max_height = float(config.get("tambor_h", 1270))
    usable_width = (
        max_width
        - settings["margen_izquierdo"]
        - settings["margen_derecho"]
        - settings["tolerancia"]
    )
    usable_height = (
        max_height
        - settings["margen_superior"]
        - settings["pinza"]
        - settings["tolerancia"]
    )

    pieces = []
    for piece in piezas:
        try:
            width = float(piece.get("width_mm", 0))
            height = float(piece.get("height_mm", 0))
        except (AttributeError, TypeError, ValueError):
            continue
        if (
            not math.isfinite(width)
            or not math.isfinite(height)
            or width <= 0
            or height <= 0
        ):
            continue
        pieces.append({**piece, "width_mm": width, "height_mm": height})

    errores = []
    if len(pieces) < len(piezas):
        errores.append("Se omitieron piezas con dimensiones inválidas.")
    if not pieces:
        return {
            "placa_unica": None,
            "placas_dobles": None,
            "errores": ["Se omitieron piezas con dimensiones inválidas."],
        }
    if usable_width <= 0 or usable_height <= 0:
        return {
            "placa_unica": None,
            "placas_dobles": None,
            "errores": ["Los márgenes exceden los límites del tambor."],
        }

    narrowest_item_bound = (
        max(
            min(piece["width_mm"], piece["height_mm"])
            if settings["rotacion"]
            else piece["width_mm"]
            for piece in pieces
        )
        + 2 * settings["espaciado"]
    )
    maximum_aspect = min(
        settings["ratio_maximo"],
        max(1.0, usable_height / narrowest_item_bound),
    )
    requested_ratio = config.get("ratio_objetivo")
    settings["target_ratio"] = (
        min(maximum_aspect, max(1.0, float(requested_ratio)))
        if requested_ratio is not None
        else math.exp(
            (settings["prioridad_aspecto"] / 100) ** 1.2 * math.log(maximum_aspect)
        )
    )

    unique = (
        _best_pack(
            pieces,
            usable_width,
            usable_height,
            settings["espaciado"],
            settings["rotacion"],
            priority=settings["prioridad_aspecto"],
            target_ratio=settings["target_ratio"],
            group_dimensions=settings["agrupar_dimensiones"],
            cancel_event=cancel_event,
            max_ratio=settings["ratio_maximo"],
        )
        if buscar_ancho
        else None
    )
    portrait_search = _best_pack(
        pieces,
        usable_height,
        usable_width,
        settings["espaciado"],
        settings["rotacion"],
        width_search=buscar_ancho,
        priority=settings["prioridad_aspecto"],
        target_ratio=1 / settings["target_ratio"],
        group_dimensions=settings["agrupar_dimensiones"],
        cancel_event=cancel_event,
        max_ratio=settings["ratio_maximo"],
    )

    portrait_candidates = []
    for candidate in (
        unique,
        _transpose_packed(unique, settings["espaciado"]),
        _transpose_packed(portrait_search, settings["espaciado"]),
    ):
        if candidate is None:
            continue
        board = _as_board(candidate, settings)
        if (
            (settings["pinza"] <= 0 or board["ancho"] <= board["alto"] + 1e-7)
            and board["ancho"] <= max_width + 1e-7
            and board["alto"] <= max_height + 1e-7
        ):
            portrait_candidates.append((candidate, board))

    ratio_safe_candidates = [
        candidate
        for candidate in portrait_candidates
        if max(
            candidate[1]["alto"] / candidate[1]["ancho"],
            candidate[1]["ancho"] / candidate[1]["alto"],
        )
        <= settings["ratio_maximo"] + 1e-9
    ]
    if not ratio_safe_candidates and settings["ratio_maximo"] > 1:
        safe_target = min(1.5, settings["ratio_maximo"])
        for width, height, ratio in (
            (usable_width, usable_height, safe_target),
            (usable_height, usable_width, 1 / safe_target),
        ):
            candidate = _best_pack(
                pieces,
                width,
                height,
                settings["espaciado"],
                settings["rotacion"],
                width_search=buscar_ancho,
                priority=0,
                target_ratio=ratio,
                group_dimensions=settings["agrupar_dimensiones"],
                cancel_event=cancel_event,
                max_ratio=settings["ratio_maximo"],
            )
            for option in (
                candidate,
                _transpose_packed(candidate, settings["espaciado"]),
            ):
                if option is None:
                    continue
                board = _as_board(option, settings)
                if (
                    (settings["pinza"] <= 0 or board["ancho"] <= board["alto"] + 1e-7)
                    and board["ancho"] <= max_width + 1e-7
                    and board["alto"] <= max_height + 1e-7
                ):
                    portrait_candidates.append((option, board))
                    if (
                        max(
                            board["alto"] / board["ancho"],
                            board["ancho"] / board["alto"],
                        )
                        <= settings["ratio_maximo"] + 1e-9
                    ):
                        ratio_safe_candidates.append((option, board))

    if cancel_event and cancel_event.is_set():
        return {
            "placa_unica": None,
            "placas_dobles": None,
            "errores": [],
            "cancelado": True,
        }

    active_candidates = ratio_safe_candidates or portrait_candidates
    ratio_out_of_bounds = bool(portrait_candidates and not ratio_safe_candidates)
    single_plate = None
    if active_candidates:
        min_area = min(
            candidate[1]["area_aprovechable"] for candidate in active_candidates
        )
        area_tolerance = 0.03 * settings["prioridad_aspecto"] / 100
        eligible = [
            candidate
            for candidate in active_candidates
            if candidate[1]["area_aprovechable"]
            <= min_area * (1 + area_tolerance) + 1e-9
        ]
        _, single_plate = min(
            eligible,
            key=lambda item: (
                abs(
                    math.log(
                        (item[1]["alto"] / item[1]["ancho"])
                        / settings["target_ratio"]
                    )
                ),
                item[1]["area_aprovechable"],
                item[1]["ancho"] * item[1]["alto"],
            ),
        )
        single_plate = {
            **single_plate,
            "ratio_alto_ancho": max(
                single_plate["alto"] / single_plate["ancho"],
                single_plate["ancho"] / single_plate["alto"],
            ),
            "ratio_fuera_limite": ratio_out_of_bounds,
        }

    split_plate = None
    if incluir_dividida and single_plate is not None:
        split_candidate = _two_plate_pack(
            pieces,
            settings,
            usable_width,
            usable_height,
            cancel_event=cancel_event,
        )
        if (
            split_candidate is not None
            and split_candidate["eficiencia_exacta"] + 3.0
            >= single_plate["eficiencia_exacta"] - 1e-9
        ):
            split_plate = split_candidate

    return {
        "placa_unica": single_plate,
        "placas_dobles": split_plate,
        "errores": errores,
    }
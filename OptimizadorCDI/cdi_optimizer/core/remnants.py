"""Local storage and packing search for measured rectangular remnants."""

from __future__ import annotations

import csv
from contextlib import contextmanager
import math
import os
import sqlite3
from pathlib import Path
from time import monotonic

from .metadata import extraer_medidas_xmp
from .optimizer import _validar_resoluciones
from .packing import optimizar_piezas
from .recommender import _best_options_for_group, _profile_configs
from .scanner import extraer_codigo_fotopolimero


class InventarioRetazos:
    CSV_HEADERS = ("id", "ancho_mm", "alto_mm", "material", "ubicacion", "notas", "creado_en")

    def __init__(self, database_path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS retazos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ancho_mm REAL NOT NULL CHECK (ancho_mm > 0),
                    alto_mm REAL NOT NULL CHECK (alto_mm > 0),
                    material TEXT NOT NULL,
                    ubicacion TEXT NOT NULL DEFAULT '',
                    notas TEXT NOT NULL DEFAULT '',
                    creado_en TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS catalogo_materiales (
                    nombre TEXT NOT NULL,
                    clave TEXT NOT NULL UNIQUE
                )
                """
            )
            legacy_materials = connection.execute(
                "SELECT id, material FROM retazos WHERE TRIM(material) != '' ORDER BY id"
            ).fetchall()
            for row in legacy_materials:
                original_name = str(row[1])
                name = original_name.strip()
                key = name.casefold()
                existing = connection.execute(
                    "SELECT nombre FROM catalogo_materiales WHERE clave=?", (key,)
                ).fetchone()
                if existing is None:
                    connection.execute(
                        "INSERT INTO catalogo_materiales (nombre, clave) VALUES (?, ?)",
                        (name, key),
                    )
                    canonical_name = name
                else:
                    canonical_name = existing[0]
                if original_name != canonical_name:
                    connection.execute(
                        "UPDATE retazos SET material=? WHERE id=?",
                        (canonical_name, row[0]),
                    )

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def listar(self):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM retazos ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def materiales(self):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT nombre FROM catalogo_materiales ORDER BY nombre COLLATE NOCASE"
            ).fetchall()
        return [row[0] for row in rows]

    def ubicaciones(self):
        """Return distinct non-empty storage locations for autocomplete."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT TRIM(ubicacion) AS ubicacion
                FROM retazos
                WHERE TRIM(ubicacion) != ''
                ORDER BY ubicacion COLLATE NOCASE
                """
            ).fetchall()
        return [row[0] for row in rows if row[0]]

    @staticmethod
    def _normalizar_material(material):
        name = str(material or "").strip()
        if not name:
            raise ValueError("Selecciona un material del catálogo.")
        return name, name.casefold()

    @classmethod
    def _resolver_material(cls, connection, material):
        name, key = cls._normalizar_material(material)
        row = connection.execute(
            "SELECT nombre FROM catalogo_materiales WHERE clave=?", (key,)
        ).fetchone()
        if row is None:
            raise ValueError(
                f"El material '{name}' no está dado de alta en el catálogo."
            )
        return row[0]

    def agregar_material(self, material):
        name, key = self._normalizar_material(material)
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM catalogo_materiales WHERE clave=?", (key,)
            ).fetchone():
                raise ValueError(f"El material '{name}' ya está en el catálogo.")
            connection.execute(
                "INSERT INTO catalogo_materiales (nombre, clave) VALUES (?, ?)",
                (name, key),
            )
        return name

    def actualizar_material(self, material_actual, nuevo_nombre):
        old_name, old_key = self._normalizar_material(material_actual)
        new_name, new_key = self._normalizar_material(nuevo_nombre)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT nombre FROM catalogo_materiales WHERE clave=?", (old_key,)
            ).fetchone()
            if row is None:
                raise KeyError(f"No existe el material '{old_name}'.")
            canonical_old = row[0]
            conflict = connection.execute(
                "SELECT nombre FROM catalogo_materiales WHERE clave=?", (new_key,)
            ).fetchone()
            if conflict is not None and new_key != old_key:
                raise ValueError(f"El material '{conflict[0]}' ya está en el catálogo.")
            connection.execute(
                "UPDATE catalogo_materiales SET nombre=?, clave=? WHERE clave=?",
                (new_name, new_key, old_key),
            )
            connection.execute(
                "UPDATE retazos SET material=? WHERE material=?",
                (new_name, canonical_old),
            )
        return new_name

    def eliminar_material(self, material):
        name, key = self._normalizar_material(material)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT nombre FROM catalogo_materiales WHERE clave=?", (key,)
            ).fetchone()
            if row is None:
                raise KeyError(f"No existe el material '{name}'.")
            canonical_name = row[0]
            used_count = connection.execute(
                "SELECT COUNT(*) FROM retazos WHERE material=?", (canonical_name,)
            ).fetchone()[0]
            if used_count:
                raise ValueError(
                    f"No se puede eliminar '{canonical_name}': está asignado a "
                    f"{used_count} retazo(s)."
                )
            connection.execute(
                "DELETE FROM catalogo_materiales WHERE clave=?", (key,)
            )

    def agregar(self, ancho_mm, alto_mm, material, ubicacion="", notas=""):
        ids = self.agregar_lote(
            ancho_mm=ancho_mm,
            alto_mm=alto_mm,
            material=material,
            ubicacion=ubicacion,
            notas=notas,
            cantidad=1,
        )
        return ids[0]

    def agregar_lote(
        self, ancho_mm, alto_mm, material, ubicacion="", notas="", cantidad=1
    ):
        """Insert one or more identical remnants atomically and return their assigned IDs."""
        width = float(ancho_mm)
        height = float(alto_mm)
        count = int(cantidad)
        if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
            raise ValueError("Las dimensiones deben ser números positivos en milímetros.")
        if count < 1 or count > 100:
            raise ValueError("La cantidad de copias debe estar entre 1 y 100.")
        clean_location = str(ubicacion or "").strip()
        clean_notes = str(notas or "").strip()
        inserted_ids = []
        with self._connect() as connection:
            canonical_material = self._resolver_material(connection, material)
            for _ in range(count):
                cursor = connection.execute(
                    """INSERT INTO retazos (ancho_mm, alto_mm, material, ubicacion, notas)
                       VALUES (?, ?, ?, ?, ?)""",
                    (width, height, canonical_material, clean_location, clean_notes),
                )
                inserted_ids.append(int(cursor.lastrowid))
        return inserted_ids

    def actualizar(self, remnant_id, ancho_mm, alto_mm, material, ubicacion="", notas=""):
        width = float(ancho_mm)
        height = float(alto_mm)
        if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
            raise ValueError("Las dimensiones deben ser números positivos en milímetros.")
        with self._connect() as connection:
            material = self._resolver_material(connection, material)
            cursor = connection.execute(
                """UPDATE retazos SET ancho_mm=?, alto_mm=?, material=?, ubicacion=?, notas=?
                   WHERE id=?""",
                (
                    width,
                    height,
                    material,
                    str(ubicacion).strip(),
                    str(notas).strip(),
                    int(remnant_id),
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"No existe el retazo {remnant_id}.")

    def eliminar(self, remnant_id):
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM retazos WHERE id=?", (int(remnant_id),)
            )
            if cursor.rowcount == 0:
                raise KeyError(f"No existe el retazo {remnant_id}.")

    def exportar_csv(self, csv_path):
        """Export current remnants (or a ready-to-fill header template if empty) to CSV."""
        remnants = self.listar()
        destination = Path(csv_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with open(destination, "w", encoding="utf-8-sig", newline="") as target:
            writer = csv.writer(target, delimiter=";")
            writer.writerow(("id", "ancho_mm", "alto_mm", "material", "ubicacion", "notas", "creado_en"))
            for item in remnants:
                writer.writerow(
                    (
                        item["id"],
                        f"{float(item['ancho_mm']):g}",
                        f"{float(item['alto_mm']):g}",
                        item["material"],
                        item.get("ubicacion", ""),
                        item.get("notas", ""),
                        item.get("creado_en", ""),
                    )
                )
        return len(remnants)

    def importar_csv(self, csv_path):
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as source:
            sample = source.read(4096)
            source.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;")
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(source, dialect=dialect)
            headers = {}
            for header in reader.fieldnames or []:
                normalized = str(header or "").strip().casefold().replace(" ", "_")
                if not normalized:
                    raise ValueError("El CSV contiene un encabezado vacío.")
                if normalized in headers:
                    raise ValueError(f"El CSV repite el encabezado '{header}'.")
                headers[normalized] = header

            width_header = headers.get("ancho_mm") or headers.get("width_mm")
            height_header = headers.get("alto_mm") or headers.get("height_mm")
            material_header = headers.get("material")
            if width_header is None or height_header is None or material_header is None:
                raise ValueError(
                    "El CSV debe incluir las columnas ancho_mm, alto_mm y material. "
                    "El orden de las columnas es libre."
                )
            location_header = headers.get("ubicacion") or headers.get("location")
            notes_header = headers.get("notas") or headers.get("notes")
            rows = []
            errors = []
            for line_number, row in enumerate(reader, start=2):
                if not any(str(value or "").strip() for value in row.values()):
                    continue
                if None in row:
                    errors.append(f"Fila {line_number}: hay más valores que encabezados.")
                    continue
                try:
                    width = self._parse_number(row.get(width_header))
                    height = self._parse_number(row.get(height_header))
                    material = str(row.get(material_header) or "").strip()
                    location = str(row.get(location_header) or "").strip()
                    notes = str(row.get(notes_header) or "").strip()
                    if not math.isfinite(width) or not math.isfinite(height):
                        raise ValueError("Las medidas deben ser finitas.")
                    if width <= 0 or height <= 0:
                        raise ValueError("Las medidas deben ser mayores que cero.")
                    if not material:
                        raise ValueError("Falta el material.")
                    rows.append(
                        (line_number, width, height, material, location, notes)
                    )
                except (TypeError, ValueError):
                    errors.append(
                        f"Fila {line_number}: ancho_mm y alto_mm deben ser números "
                        "positivos y material no puede quedar vacío."
                    )
            if errors:
                raise ValueError("CSV no importado. " + " ".join(errors[:20]))
            if not rows:
                raise ValueError("El CSV no contiene retazos válidos para importar.")

        with self._connect() as connection:
            resolved_rows = []
            for line_number, width, height, material, location, notes in rows:
                try:
                    canonical_material = self._resolver_material(connection, material)
                except ValueError as error:
                    errors.append(f"Fila {line_number}: {error}")
                    continue
                resolved_rows.append(
                    (width, height, canonical_material, location, notes)
                )
            if errors:
                raise ValueError("CSV no importado. " + " ".join(errors[:20]))
            connection.executemany(
                """INSERT INTO retazos (ancho_mm, alto_mm, material, ubicacion, notas)
                   VALUES (?, ?, ?, ?, ?)""",
                resolved_rows,
            )
        return len(resolved_rows)

    @staticmethod
    def _parse_number(value):
        text = str(value or "").strip().replace(" ", "")
        if "," in text and "." not in text:
            text = text.replace(",", ".")
        return float(text)


def calcular_cortes_sobrantes_retazo(remnant, layout_result=None):
    """Calculate remaining rectangle dimensions after a horizontal or vertical guillotine cut."""
    if not layout_result:
        return None
    try:
        raw_w = float(remnant["ancho_mm"])
        raw_h = float(remnant["alto_mm"])
        plate_w, plate_h = sorted((raw_w, raw_h))
        cut_w = float(layout_result.get("ancho_acomodo", layout_result.get("ancho", 0)))
        cut_h = float(layout_result.get("alto_acomodo", layout_result.get("alto", 0)))
    except (KeyError, TypeError, ValueError):
        return None

    if cut_w <= 0 or cut_h <= 0 or cut_w > plate_w + 1e-6 or cut_h > plate_h + 1e-6:
        return None

    rem_w_vertical = round(max(0.0, plate_w - cut_w), 1)
    rem_h_vertical = round(plate_h, 1)
    area_vertical = rem_w_vertical * rem_h_vertical

    rem_w_horizontal = round(plate_w, 1)
    rem_h_horizontal = round(max(0.0, plate_h - cut_h), 1)
    area_horizontal = rem_w_horizontal * rem_h_horizontal

    return {
        "placa_orientada_ancho": round(plate_w, 1),
        "placa_orientada_alto": round(plate_h, 1),
        "corte_ancho": round(cut_w, 1),
        "corte_alto": round(cut_h, 1),
        "corte_vertical": (
            {
                "ancho_mm": rem_w_vertical,
                "alto_mm": rem_h_vertical,
                "area_m2": round(area_vertical / 1_000_000.0, 4),
            }
            if rem_w_vertical >= 1.0 and rem_h_vertical >= 1.0
            else None
        ),
        "corte_horizontal": (
            {
                "ancho_mm": rem_w_horizontal,
                "alto_mm": rem_h_horizontal,
                "area_m2": round(area_horizontal / 1_000_000.0, 4),
            }
            if rem_w_horizontal >= 1.0 and rem_h_horizontal >= 1.0
            else None
        ),
    }


def _resultado_sobre_retazo(board, remnant, config, pieces):
    physical_width = float(remnant["ancho_mm"])
    physical_height = float(remnant["alto_mm"])
    width, height = sorted((physical_width, physical_height))
    usable_width = max(
        0,
        width
        - float(config["margen_izquierdo"])
        - float(config["margen_derecho"])
        - float(config["tolerancia"]),
    )
    usable_height = max(
        0,
        height
        - float(config["margen_superior"])
        - float(config["pinza"])
        - float(config["tolerancia"]),
    )
    usable_area = usable_width * usable_height
    item_area = sum(piece["width_mm"] * piece["height_mm"] for piece in pieces)
    efficiency_exact = 100 * item_area / usable_area if usable_area else 0

    parts = board["grabados"]
    shift_x = float(config["margen_izquierdo"]) - min(
        (part["x"] for part in parts), default=float(config["margen_izquierdo"])
    )
    usable_bottom = height - float(config["pinza"]) - float(config["tolerancia"])
    current_bottom = max(
        (part["y"] + part["h"] for part in parts),
        default=usable_bottom,
    )
    shift_y = max(0.0, usable_bottom - current_bottom)
    positioned_parts = [
        {
            **part,
            "x": round(part["x"] + shift_x, 2),
            "y": round(part["y"] + shift_y, 2),
        }
        for part in parts
    ]

    return {
        **board,
        "grabados": positioned_parts,
        "ancho_acomodo": board["ancho"],
        "alto_acomodo": board["alto"],
        "eficiencia_acomodo": board["eficiencia"],
        "eficiencia_exacta_acomodo": board["eficiencia_exacta"],
        "ancho": width,
        "alto": height,
        "retazo_ancho": physical_width,
        "retazo_alto": physical_height,
        "retazo_rotado": physical_width > physical_height,
        "area_utilizable": round(usable_area, 2),
        "area_aprovechable": round(usable_area, 2),
        "eficiencia": round(efficiency_exact, 1),
        "eficiencia_exacta": efficiency_exact,
        "aprovechamiento_retazo": round(efficiency_exact, 1),
        "aprovechamiento_retazo_exacto": efficiency_exact,
    }


def _retazo_admite_material(remnant, material_fotopolimero):
    if material_fotopolimero is None:
        return True
    return extraer_codigo_fotopolimero(remnant["material"]) == extraer_codigo_fotopolimero(
        material_fotopolimero
    )


def _mejor_acomodo_retazo(pieces, config, cancel_event=None):
    boards = []
    for profile in _profile_configs(config):
        if cancel_event and cancel_event.is_set():
            return None
        board = optimizar_piezas(
            pieces,
            profile,
            incluir_dividida=False,
            buscar_ancho=True,
            cancel_event=cancel_event,
        )["placa_unica"]
        if board is not None:
            boards.append(board)
    if not boards:
        return None
    return max(
        boards,
        key=lambda board: (
            board["eficiencia_exacta"],
            -(board["ancho"] * board["alto"]),
        ),
    )


def buscar_acomodos_en_retazos(
    lista_archivos, retazos, config=None, cancel_event=None, progress_callback=None
):
    """Find one-plate layouts that fit inside each remnant's usable dimensions."""
    from .optimizer import _leer_piezas

    pieces, errors, cancelled = _leer_piezas(lista_archivos, cancel_event)
    if cancelled:
        return {"resultados": [], "errores": [], "cancelado": True}
    if not pieces:
        return {"resultados": [], "errores": errors, "cancelado": False}

    config = config or {}
    base_margin = float(config.get("margen", 7))
    remnant_config = {
        **config,
        "margen_izquierdo": float(config.get("margen_izquierdo", base_margin)),
        "margen_derecho": float(config.get("margen_derecho", base_margin)),
        "margen_superior": float(config.get("margen_superior", base_margin)),
        "pinza": float(config.get("pinza", 25)),
        "tolerancia": float(config.get("tolerancia", 3)),
    }
    compatibility_groups = {}
    for piece in pieces:
        compatibility_groups.setdefault(
            (piece["resolucion_dpi"], piece["material_fotopolimero"]), []
        ).append(piece)

    results = []
    total = len(retazos) * len(compatibility_groups)
    checked = 0
    for (_, material), group in compatibility_groups.items():
        if not _validar_resoluciones(group):
            message = (
                "No se pueden combinar trabajos con distintas resoluciones DPI "
                "o sin resolución verificable."
            )
            if message not in errors:
                errors.append(message)
            continue

        geometry_cache = {}
        for remnant in retazos:
            if cancel_event and cancel_event.is_set():
                return {"resultados": [], "errores": [], "cancelado": True}
            checked += 1
            if not _retazo_admite_material(remnant, material):
                continue
            physical_width = float(remnant["ancho_mm"])
            physical_height = float(remnant["alto_mm"])
            width, height = sorted((physical_width, physical_height))
            geometry_key = (width, height, tuple(sorted(remnant_config.items())))
            if geometry_key not in geometry_cache:
                board_config = {
                    **remnant_config,
                    "tambor_w": width,
                    "tambor_h": height,
                }
                geometry_cache[geometry_key] = _mejor_acomodo_retazo(
                    group,
                    board_config,
                    cancel_event=cancel_event,
                )
            board = geometry_cache[geometry_key]
            if board is not None:
                remnant_result = _resultado_sobre_retazo(
                    board, remnant, remnant_config, group
                )
                results.append(
                    {
                        **remnant_result,
                        "tipo": "single",
                        "retazo_id": remnant["id"],
                        "material": remnant["material"],
                        "material_fotopolimero": material,
                        "trabajos": sorted(
                            {piece.get("trabajo", piece["name"]) for piece in group}
                        ),
                    }
                )
            if progress_callback:
                progress_callback(
                    round(checked / max(1, total) * 100),
                    f"Probando retazos: {checked}/{total}",
                )

    results.sort(
        key=lambda result: (
            -result["aprovechamiento_retazo"],
            -result["eficiencia_exacta"],
            result["retazo_id"],
        )
    )
    return {"resultados": results, "errores": errors, "cancelado": False}


class _DeadlineCancelEvent:
    def __init__(self, cancel_event, deadline):
        self.cancel_event = cancel_event
        self.deadline = deadline

    def is_set(self):
        return bool(self.cancel_event and self.cancel_event.is_set()) or monotonic() >= self.deadline


def _limite_candidatos_retazo(piece_count):
    if piece_count <= 128:
        return 16
    if piece_count <= 256:
        return 9
    return 3


def sugerir_combinaciones_en_retazos(
    lista_archivos,
    retazos,
    config=None,
    cancel_event=None,
    progress_callback=None,
    partial_callback=None,
    time_budget_seconds=120,
):
    """Use the normal subset recommender independently on each remnant."""
    from .optimizer import _leer_piezas

    started = monotonic()
    budget = max(0.1, float(time_budget_seconds))
    deadline = started + budget
    timed_cancel = _DeadlineCancelEvent(cancel_event, deadline)
    partial_results = {}

    def ordered_results():
        return [
            {key: value for key, value in option.items() if key != "_ranking"}
            for option in sorted(
                partial_results.values(),
                key=lambda option: (
                    -option["eficiencia_exacta"],
                    -option["cantidad"],
                    option["retazo_id"],
                ),
            )
        ]

    def make_result():
        elapsed = monotonic() - started
        return {
            "resultados": ordered_results(),
            "errores": errors,
            "cancelado": bool(cancel_event and cancel_event.is_set()),
            "tiempo_agotado": elapsed >= budget,
            "tiempo_empleado": elapsed,
            "tiempo_limite": budget,
            "total_grabados": len(pieces),
            "propuestas_encontradas": len(partial_results),
            "estrategia": "Subconjuntos del recomendador normal, adaptados a cada retazo",
        }

    def report_partial():
        if partial_callback:
            partial_callback(make_result())

    def report_progress(percent, message):
        if progress_callback:
            progress_callback(max(0, min(100, int(percent))), message)

    pieces, errors, read_cancelled = _leer_piezas(
        lista_archivos,
        timed_cancel,
        lambda percent, message: report_progress(
            round(percent * 0.1), message
        ),
    )
    if read_cancelled:
        return make_result()
    if not pieces or not retazos:
        report_progress(100, "No hay grabados o retazos para evaluar")
        return make_result()

    compatibility_groups = {}
    for piece in pieces:
        compatibility_groups.setdefault(
            (piece["resolucion_dpi"], piece["material_fotopolimero"]), []
        ).append(piece)

    base_config = dict(config or {})
    stock_width = max(
        min(float(remnant["ancho_mm"]), float(remnant["alto_mm"]))
        for remnant in retazos
    )
    stock_height = max(
        max(float(remnant["ancho_mm"]), float(remnant["alto_mm"]))
        for remnant in retazos
    )
    normal_width = min(float(base_config.get("tambor_w", 1067)), stock_width)
    normal_height = min(float(base_config.get("tambor_h", 1270)), stock_height)
    base_margin = float(base_config.get("margen", 7))
    remnant_configs = []
    for remnant in retazos:
        width, height = sorted(
            (float(remnant["ancho_mm"]), float(remnant["alto_mm"]))
        )
        remnant_configs.append(
            {
                **base_config,
                "tambor_w": width,
                "tambor_h": height,
                "margen_izquierdo": float(
                    base_config.get("margen_izquierdo", base_margin)
                ),
                "margen_derecho": float(
                    base_config.get("margen_derecho", base_margin)
                ),
                "margen_superior": float(
                    base_config.get("margen_superior", base_margin)
                ),
                "pinza": float(base_config.get("pinza", 25)),
                "tolerancia": float(base_config.get("tolerancia", 3)),
                "espaciado": float(base_config.get("espaciado", 3)),
                "rotacion": bool(base_config.get("rotacion", True)),
            }
        )

    unique_candidates = set()
    candidate_count = 0
    remnant_checks = 0
    estimated_checks = max(
        1, len(retazos) * len(pieces) * max(1, len(compatibility_groups)) * 3
    )

    for group_index, ((resolution, material), group) in enumerate(
        compatibility_groups.items(), start=1
    ):
        if timed_cancel.is_set():
            break

        def accept_candidate(chosen, _normal_board):
            nonlocal candidate_count, remnant_checks
            if timed_cancel.is_set():
                return
            signature = tuple(sorted(piece["path"] for piece in chosen))
            if signature in unique_candidates:
                return
            unique_candidates.add(signature)
            candidate_count += 1
            for remnant_index, (remnant, remnant_config) in enumerate(
                zip(retazos, remnant_configs), start=1
            ):
                if timed_cancel.is_set():
                    return
                if not _retazo_admite_material(remnant, material):
                    continue
                board = optimizar_piezas(
                    chosen,
                    remnant_config,
                    incluir_dividida=False,
                    buscar_ancho=False,
                    cancel_event=timed_cancel,
                )["placa_unica"]
                remnant_checks += 1
                if board is not None:
                    result_key = (remnant["id"], resolution, material)
                    remnant_board = _resultado_sobre_retazo(
                        board, remnant, remnant_config, chosen
                    )
                    previous = partial_results.get(result_key)
                    score = (remnant_board["eficiencia_exacta"], len(chosen))
                    if previous is None or score > previous["_ranking"]:
                        partial_results[result_key] = {
                            **remnant_board,
                            "tipo": "single",
                            "sugerencia": True,
                            "retazo_id": remnant["id"],
                            "material": remnant["material"],
                            "archivos_asignados": [
                                piece["path"] for piece in chosen
                            ],
                            "nombres_asignados": [
                                piece["name"] for piece in chosen
                            ],
                            "cantidad": len(chosen),
                            "resolucion_dpi": resolution,
                            "material_fotopolimero": material,
                            "trabajos": sorted(
                                {
                                    piece.get("trabajo", piece["name"])
                                    for piece in chosen
                                }
                            ),
                            "_ranking": score,
                        }
                        report_partial()
                report_progress(
                    min(99, 60 + round(remnant_checks / estimated_checks * 39)),
                    f"Subconjunto {candidate_count} · retazo "
                    f"{remnant_index}/{len(retazos)} · "
                    f"propuestas: {len(partial_results)}",
                )

        def report_group_progress(local_percent, message):
            group_fraction = (
                group_index - 1 + max(0, min(100, local_percent)) / 100
            ) / max(1, len(compatibility_groups))
            report_progress(
                round(10 + group_fraction * 50),
                f"Generando subconjuntos normales · {message}",
            )

        _best_options_for_group(
            group,
            {
                **base_config,
                "tambor_w": normal_width,
                "tambor_h": normal_height,
            },
            timed_cancel,
            progress_callback=report_group_progress,
            candidate_callback=accept_candidate,
            allow_singletons=True,
            candidate_limit=_limite_candidatos_retazo(len(group)),
        )

    final_result = make_result()
    if partial_callback:
        partial_callback(final_result)
    report_progress(
        100,
        "Tiempo máximo alcanzado" if final_result["tiempo_agotado"] else "Sugerencias listas",
    )
    return final_result


def sugerir_distribucion_en_retazos(*args, **kwargs):
    """Compatibility name for the per-remnant combination suggester."""
    return sugerir_combinaciones_en_retazos(*args, **kwargs)
#!/usr/bin/env python3
"""Benchmark y validador de regresiones de calidad para Optimizador CDI.

Flujo de uso recomendado:
  1. Antes de aplicar cambios (capturar línea base):
     python benchmark_calidad.py --save-baseline baseline_actual.json

  2. Después de aplicar cambios (comparar calidad y velocidad):
     python benchmark_calidad.py --compare baseline_actual.json

Opciones útiles:
  --rip-dir "C:/ruta/a/carpeta/rip"   Sobrescribe la carpeta RIP de QSettings
  --retazos-db "C:/ruta/retazos.sqlite" Sobrescribe la BD de retazos
  --solo-sintetico                    Omite archivos .len reales
  --max-real-pieces 24                Máximo de piezas por grupo real (defecto: 24)
  --remnant-budget 15                 Segundos máx. para sugerencias en retazos (defecto: 15)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

from cdi_optimizer.core import (
    escanear_carpeta_rip,
    extraer_medidas_xmp,
    generar_alternativas_archivos,
    optimizar_piezas,
    sugerir_combinaciones,
)
from cdi_optimizer.core.remnants import (
    InventarioRetazos,
    buscar_acomodos_en_retazos,
    sugerir_combinaciones_en_retazos,
)

DEFAULT_CONFIG: dict[str, Any] = {
    "margen_izquierdo": 7.0,
    "margen_derecho": 7.0,
    "margen_superior": 7.0,
    "pinza": 25.0,
    "espaciado": 3.0,
    "tolerancia": 3.0,
    "ratio_maximo": 3.0,
    "tambor_w": 1067.0,
    "tambor_h": 1270.0,
    "rotacion": True,
    "agrupar_tamanos_cercanos": False,
    "tolerancia_tamanos_cercanos": 1,
}


def cargar_configuracion_app(
    rip_dir_override: str | None = None,
    retazos_db_override: str | None = None,
) -> tuple[dict[str, Any], str | None, Path | None]:
    """Lee parámetros reales, ruta RIP y BD de retazos desde QSettings si está disponible."""
    config = dict(DEFAULT_CONFIG)
    rip_dir = rip_dir_override
    retazos_db = Path(retazos_db_override) if retazos_db_override else None

    try:
        from PySide6.QtCore import QSettings, QStandardPaths

        settings = QSettings("MTRemozar", "OptimizadorCDI")
        config.update(
            {
                "margen_izquierdo": float(settings.value("margen_izquierdo", 7)),
                "margen_derecho": float(settings.value("margen_derecho", 7)),
                "margen_superior": float(settings.value("margen_superior", 7)),
                "pinza": float(settings.value("pinza", 25)),
                "espaciado": float(settings.value("espaciado", 3)),
                "tolerancia": float(settings.value("tol_corte", 3)),
                "ratio_maximo": float(settings.value("ratio_maximo", 3.0)),
                "tambor_w": float(settings.value("tambor_w", 1067)),
                "tambor_h": float(settings.value("tambor_h", 1270)),
                "rotacion": bool(settings.value("rotacion", True, type=bool)),
                "agrupar_tamanos_cercanos": bool(
                    settings.value("agrupar_tamanos_cercanos", False, type=bool)
                ),
                "tolerancia_tamanos_cercanos": int(
                    settings.value("tolerancia_tamanos_cercanos", 1)
                ),
            }
        )
        if not rip_dir:
            stored_rip = str(settings.value("ruta_rip", "") or "").strip()
            if stored_rip and os.path.isdir(stored_rip):
                rip_dir = stored_rip
        if retazos_db is None:
            local_app = os.environ.get("LOCALAPPDATA")
            if local_app:
                candidate = Path(local_app) / "MTRemozar" / "OptimizadorCDI" / "retazos.sqlite"
                if candidate.is_file():
                    retazos_db = candidate
            else:
                data_dir = QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppLocalDataLocation
                )
                candidate = Path(data_dir) / "retazos.sqlite"
                if candidate.is_file():
                    retazos_db = candidate
    except Exception:
        pass

    return config, rip_dir, retazos_db


def validar_invariantes_placa(
    board: dict[str, Any],
    expected_count: int,
    config: dict[str, Any],
    *,
    is_remnant: bool = False,
) -> list[str]:
    """Verifica que la solución respete todas las restricciones físicas de producción."""
    errors: list[str] = []
    parts = board.get("grabados", [])
    if len(parts) != expected_count:
        errors.append(f"Cantidad de piezas incorrecta: {len(parts)} != {expected_count}")

    spacing = float(config.get("espaciado", 3.0))
    left = float(config.get("margen_izquierdo", 7.0))
    right = float(config.get("margen_derecho", 7.0))
    top = float(config.get("margen_superior", 7.0))
    pinza = float(config.get("pinza", 25.0))
    tol = float(config.get("tolerancia", 3.0))
    max_w = float(config.get("tambor_w", 1067.0))
    max_h = float(config.get("tambor_h", 1270.0))
    max_ratio = float(config.get("ratio_maximo", 3.0))

    plates = board.get("placas") or [board]
    for idx, plate in enumerate(plates, start=1):
        pw = float(plate["ancho"])
        ph = float(plate["alto"])
        if not is_remnant:
            if pw > max_w + 1e-5 or ph > max_h + 1e-5:
                errors.append(f"Placa {idx} excede tambor: {pw}x{ph} > {max_w}x{max_h}")
            if pinza > 0 and pw > ph + 1e-5:
                errors.append(f"Placa {idx} no está en retrato con pinza>0: {pw}x{ph}")
            if not board.get("ratio_fuera_limite", False):
                ratio = max(ph / pw, pw / ph)
                if ratio > max_ratio + 1e-5:
                    errors.append(f"Placa {idx} excede ratio máximo: {ratio:.3f} > {max_ratio}")

    # Validar solapamiento entre piezas dentro de la misma placa (tolerancia 0.05 mm por redondeo a 2 dec.)
    eps = 0.05
    by_plate: dict[int, list[dict[str, Any]]] = {}
    for p in parts:
        by_plate.setdefault(int(p.get("placa", 1)), []).append(p)

    for plate_id, group in by_plate.items():
        for i in range(len(group)):
            a = group[i]
            ax1, ay1 = float(a["x"]), float(a["y"])
            ax2, ay2 = ax1 + float(a["w"]), ay1 + float(a["h"])
            if not is_remnant and len(plates) == 1:
                if ax1 < left - eps or ay1 < top - eps:
                    errors.append(f"Pieza fuera de margen sup/izq: ({ax1}, {ay1})")
                if ax2 > float(board["ancho"]) - right - tol + eps:
                    errors.append(f"Pieza excede borde derecho: {ax2}")
                if ay2 > float(board["alto"]) - pinza - tol + eps:
                    errors.append(f"Pieza invade zona de pinza: {ay2}")
            for j in range(i + 1, len(group)):
                b = group[j]
                bx1, by1 = float(b["x"]), float(b["y"])
                bx2, by2 = bx1 + float(b["w"]), by1 + float(b["h"])
                min_gap = 2.0 * spacing - eps
                if (
                    ax1 < bx2 + min_gap
                    and ax2 + min_gap > bx1
                    and ay1 < by2 + min_gap
                    and ay2 + min_gap > by1
                ):
                    errors.append(
                        f"Solapamiento o violación de espaciado en placa {plate_id} "
                        f"entre '{a.get('nombre')}' y '{b.get('nombre')}'"
                    )
                    break

    return errors


def construir_casos_sinteticos() -> list[tuple[str, list[dict[str, Any]], dict[str, Any]]]:
    """Genera casos sintéticos deterministas cubriendo los principales escenarios del motor."""
    def make_pieces(dims: list[tuple[float, float]], prefix: str = "P") -> list[dict[str, Any]]:
        return [
            {
                "name": f"{prefix}_{i:02d}.len",
                "path": f"/virtual/{prefix}_{i:02d}.len",
                "width_mm": float(w),
                "height_mm": float(h),
                "resolucion_dpi": (2540.0, 2540.0),
                "material_fotopolimero": "FP67",
            }
            for i, (w, h) in enumerate(dims, start=1)
        ]

    return [
        (
            "sintetico_rejilla_identica_12",
            make_pieces([(150, 210)] * 12, "GRID"),
            {},
        ),
        (
            "sintetico_rotacion_critica_10",
            make_pieces(
                [
                    (320, 95), (320, 95), (210, 140), (210, 140),
                    (180, 110), (140, 260), (95, 310), (120, 120),
                    (250, 85), (160, 190),
                ],
                "ROT",
            ),
            {},
        ),
        (
            "sintetico_lote_grande_30",
            make_pieces(
                [
                    (180, 120), (180, 120), (180, 120), (180, 120),
                    (240, 90), (240, 90), (240, 90), (240, 90),
                    (135, 205), (135, 205), (135, 205), (135, 205),
                    (95, 160), (95, 160), (95, 160), (95, 160),
                    (310, 75), (310, 75), (150, 150), (150, 150),
                    (110, 220), (110, 220), (200, 130), (200, 130),
                    (85, 145), (85, 145), (165, 95), (165, 95),
                    (125, 175), (125, 175),
                ],
                "BIG",
            ),
            {},
        ),
        (
            "sintetico_placas_dobles_grupos",
            make_pieces([(280, 190)] * 4 + [(140, 310)] * 4, "SPLIT"),
            {},
        ),
        (
            "sintetico_sin_rotacion",
            make_pieces(
                [(220, 130), (180, 240), (300, 95), (150, 150), (220, 130), (180, 240)],
                "NOROT",
            ),
            {"rotacion": False},
        ),
    ]


def resumir_placa(board: dict[str, Any] | None) -> dict[str, Any] | None:
    if board is None:
        return None
    return {
        "ancho": round(float(board["ancho"]), 2),
        "alto": round(float(board["alto"]), 2),
        "area_total": round(float(board["ancho"]) * float(board["alto"]), 2),
        "area_aprovechable": round(float(board.get("area_aprovechable", 0.0)), 2),
        "eficiencia_exacta": round(float(board["eficiencia_exacta"]), 6),
        "grabados": len(board.get("grabados", [])),
        "ratio_fuera_limite": bool(board.get("ratio_fuera_limite", False)),
    }


def ejecutar_suite(
    config: dict[str, Any],
    rip_dir: str | None,
    retazos_db: Path | None,
    *,
    solo_sintetico: bool = False,
    max_real_pieces: int = 24,
    remnant_budget: float = 15.0,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "config": config,
        "rip_dir": rip_dir,
        "cases": {},
    }

    # 1. Casos sintéticos con optimizar_piezas
    for name, pieces, override in construir_casos_sinteticos():
        case_cfg = {**config, **override}
        t0 = perf_counter()
        res = optimizar_piezas(pieces, case_cfg)
        elapsed_ms = (perf_counter() - t0) * 1000.0

        inv_errors: list[str] = []
        if res["placa_unica"] is not None:
            inv_errors.extend(validar_invariantes_placa(res["placa_unica"], len(pieces), case_cfg))
        if res["placas_dobles"] is not None:
            inv_errors.extend(validar_invariantes_placa(res["placas_dobles"], len(pieces), case_cfg))

        report["cases"][name] = {
            "kind": "optimizar_piezas",
            "piece_count": len(pieces),
            "elapsed_ms": round(elapsed_ms, 2),
            "placa_unica": resumir_placa(res["placa_unica"]),
            "placas_dobles": resumir_placa(res["placas_dobles"]),
            "invariant_errors": inv_errors,
        }

    # 2. Casos reales con los .len de la carpeta RIP
    if not solo_sintetico and rip_dir and os.path.isdir(rip_dir):
        jobs = escanear_carpeta_rip(rip_dir)
        all_paths = [ink["ruta_completa"] for inks in jobs.values() for ink in inks]

        # Agrupar archivos reales por (DPI, material)
        groups: dict[tuple[Any, Any], list[str]] = {}
        for path in all_paths:
            meta = extraer_medidas_xmp(path)
            if meta is None:
                continue
            dpi = (
                (meta.get("resolucion_x_dpi"), meta.get("resolucion_y_dpi"))
                if "resolucion_x_dpi" in meta and "resolucion_y_dpi" in meta
                else None
            )
            # Buscar material ya parseado por el scanner
            mat = None
            for inks in jobs.values():
                for ink in inks:
                    if ink["ruta_completa"] == path:
                        mat = ink["material_fotopolimero"]
                        break
            groups.setdefault((dpi, mat), []).append(path)

        for idx, ((dpi, mat), paths) in enumerate(sorted(groups.items(), key=lambda kv: str(kv[0])), start=1):
            sample_paths = paths[:max_real_pieces]
            if not sample_paths:
                continue
            case_name = f"real_alternativas_g{idx}_{mat or 'SIN_FP'}_{len(sample_paths)}pzas"
            t0 = perf_counter()
            res = generar_alternativas_archivos(sample_paths, config)
            elapsed_ms = (perf_counter() - t0) * 1000.0
            alts = res.get("alternativas", [])
            inv_errors = []
            for alt in alts:
                inv_errors.extend(validar_invariantes_placa(alt, len(sample_paths), config))

            best_eff = max((float(a["eficiencia_exacta"]) for a in alts), default=0.0)
            best_area = min((float(a["ancho"]) * float(a["alto"]) for a in alts), default=0.0)
            report["cases"][case_name] = {
                "kind": "generar_alternativas_archivos",
                "piece_count": len(sample_paths),
                "dpi": list(dpi) if dpi else None,
                "material": mat,
                "elapsed_ms": round(elapsed_ms, 2),
                "alternatives_count": len(alts),
                "best_eficiencia_exacta": round(best_eff, 6),
                "best_area_total": round(best_area, 2),
                "invariant_errors": inv_errors,
            }

        # Sugerencias globales sobre la carpeta RIP real
        if len(all_paths) >= 2:
            t0 = perf_counter()
            sug_res = sugerir_combinaciones(all_paths, config)
            elapsed_ms = (perf_counter() - t0) * 1000.0
            sugs = sug_res.get("sugerencias", [])
            top_by_count: dict[str, float] = {}
            for s in sugs:
                key = str(s["cantidad"])
                eff = float(s["eficiencia"])
                if key not in top_by_count or eff > top_by_count[key]:
                    top_by_count[key] = eff
            report["cases"]["real_sugerir_combinaciones"] = {
                "kind": "sugerir_combinaciones",
                "total_files": len(all_paths),
                "elapsed_ms": round(elapsed_ms, 2),
                "suggestions_count": len(sugs),
                "top_efficiency_by_count": top_by_count,
                "invariant_errors": [],
            }

        # Retazos reales si existe la BD SQLite
        if retazos_db and retazos_db.is_file() and all_paths:
            inventory = InventarioRetazos(retazos_db)
            remnants = inventory.listar()
            if remnants:
                t0 = perf_counter()
                rem_res = sugerir_combinaciones_en_retazos(
                    all_paths,
                    remnants,
                    config,
                    time_budget_seconds=remnant_budget,
                )
                elapsed_ms = (perf_counter() - t0) * 1000.0
                matches = rem_res.get("resultados", [])
                best_per_remnant = {
                    str(m["retazo_id"]): round(float(m["eficiencia_exacta"]), 6)
                    for m in matches
                }
                report["cases"]["real_sugerir_retazos"] = {
                    "kind": "sugerir_combinaciones_en_retazos",
                    "total_files": len(all_paths),
                    "total_remnants": len(remnants),
                    "elapsed_ms": round(elapsed_ms, 2),
                    "tiempo_agotado": bool(rem_res.get("tiempo_agotado", False)),
                    "proposals_count": len(matches),
                    "best_per_remnant": best_per_remnant,
                    "invariant_errors": [],
                }

    return report


def comparar_reportes(baseline: dict[str, Any], current: dict[str, Any]) -> tuple[bool, list[str]]:
    """Compara línea base vs ejecución actual mostrando calidad y velocidad lado a lado."""
    ok = True
    lines: list[str] = []
    tol = 1e-6

    all_names = sorted(set(baseline["cases"]) | set(current["cases"]))
    total_base_ms = 0.0
    total_curr_ms = 0.0

    for name in all_names:
        base = baseline["cases"].get(name)
        curr = current["cases"].get(name)
        if base is None:
            lines.append(f"[NUEVO ] {name}: {curr['elapsed_ms']:.1f} ms")
            continue
        if curr is None:
            ok = False
            lines.append(f"[FALTA ] {name}: presente en baseline pero no en ejecución actual")
            continue

        b_ms = float(base["elapsed_ms"])
        c_ms = float(curr["elapsed_ms"])
        total_base_ms += b_ms
        total_curr_ms += c_ms
        speedup = (b_ms / c_ms) if c_ms > 0 else float("inf")
        delta_pct = ((c_ms - b_ms) / b_ms * 100.0) if b_ms > 0 else 0.0

        regressions: list[str] = []
        improvements: list[str] = []
        quality_summary = ""

        inv_count = len(curr.get("invariant_errors", []))
        if inv_count:
            regressions.append(
                f"invariantes rotas ({inv_count}): {curr['invariant_errors'][0]}"
            )

        kind = curr["kind"]
        if kind == "optimizar_piezas":
            parts_summary = []
            for label, key in (("Única", "placa_unica"), ("Doble", "placas_dobles")):
                bp = base.get(key)
                cp = curr.get(key)
                if bp is None and cp is None:
                    parts_summary.append(f"{label}: N/A")
                    continue
                if bp is not None and cp is None:
                    regressions.append(f"{key} desapareció (era {bp['eficiencia_exacta']:.4f}%)")
                    parts_summary.append(f"{label}: {bp['eficiencia_exacta']:.2f}% -> None")
                    continue
                if bp is None and cp is not None:
                    improvements.append(f"{key} nueva ({cp['eficiencia_exacta']:.4f}%)")
                    parts_summary.append(f"{label}: None -> {cp['eficiencia_exacta']:.2f}%")
                    continue

                b_eff, c_eff = bp["eficiencia_exacta"], cp["eficiencia_exacta"]
                b_dim = f"{bp['ancho']:g}x{bp['alto']:g}"
                c_dim = f"{cp['ancho']:g}x{cp['alto']:g}"
                igual = (
                    abs(c_eff - b_eff) <= tol
                    and b_dim == c_dim
                    and abs(cp["area_total"] - bp["area_total"]) <= 1e-2
                )
                tag = "IDÉNTICO" if igual else "CAMBIÓ"
                parts_summary.append(
                    f"{label}: {b_eff:.3f}% ({b_dim}) -> {c_eff:.3f}% ({c_dim}) [{tag}]"
                )
                if c_eff < b_eff - tol:
                    regressions.append(f"{key} eficiencia bajó: {b_eff:.4f}% -> {c_eff:.4f}%")
                elif c_eff > b_eff + tol:
                    improvements.append(f"{key} eficiencia subió: {b_eff:.4f}% -> {c_eff:.4f}%")
                elif cp["area_total"] > bp["area_total"] + 1e-2:
                    regressions.append(
                        f"{key} área total creció a igual eficiencia: "
                        f"{bp['area_total']:.1f} -> {cp['area_total']:.1f} mm²"
                    )
            quality_summary = " | ".join(parts_summary) + f" | inv={inv_count}"

        elif kind == "generar_alternativas_archivos":
            b_eff, c_eff = base["best_eficiencia_exacta"], curr["best_eficiencia_exacta"]
            b_area, c_area = base["best_area_total"], curr["best_area_total"]
            b_cnt, c_cnt = base["alternatives_count"], curr["alternatives_count"]
            igual = (
                abs(c_eff - b_eff) <= tol
                and abs(c_area - b_area) <= 1e-2
                and b_cnt == c_cnt
            )
            quality_summary = (
                f"Mejor efic: {b_eff:.3f}% -> {c_eff:.3f}% | "
                f"Área mín: {b_area:.0f} -> {c_area:.0f} mm² | "
                f"Opciones: {b_cnt} -> {c_cnt} | inv={inv_count} "
                f"[{'IDÉNTICO' if igual else 'CAMBIÓ'}]"
            )
            if c_eff < b_eff - tol:
                regressions.append(f"mejor eficiencia bajó: {b_eff:.4f}% -> {c_eff:.4f}%")
            elif c_eff > b_eff + tol:
                improvements.append(f"mejor eficiencia subió: {b_eff:.4f}% -> {c_eff:.4f}%")
            if c_cnt < b_cnt:
                regressions.append(f"menos alternativas: {b_cnt} -> {c_cnt}")
            if c_area > b_area + 1e-2 and c_eff <= b_eff + tol:
                regressions.append(f"área mínima empeoró: {b_area:.1f} -> {c_area:.1f} mm²")

        elif kind == "sugerir_combinaciones":
            b_map = base.get("top_efficiency_by_count", {})
            c_map = curr.get("top_efficiency_by_count", {})
            exact_matches = 0
            for cnt, b_eff in b_map.items():
                c_eff = c_map.get(cnt)
                if c_eff is None:
                    regressions.append(f"faltan sugerencias para {cnt} grabados")
                elif c_eff < b_eff - tol:
                    regressions.append(f"{cnt} grabados: {b_eff}% -> {c_eff}%")
                elif c_eff > b_eff + tol:
                    improvements.append(f"{cnt} grabados: {b_eff}% -> {c_eff}%")
                else:
                    exact_matches += 1
            b_avg = (sum(b_map.values()) / len(b_map)) if b_map else 0.0
            c_avg = (sum(c_map.values()) / len(c_map)) if c_map else 0.0
            b_cnt, c_cnt = base["suggestions_count"], curr["suggestions_count"]
            if c_cnt < b_cnt:
                regressions.append(f"menos sugerencias totales: {b_cnt} -> {c_cnt}")
            quality_summary = (
                f"Sugerencias: {b_cnt} -> {c_cnt} | "
                f"Cantidades idénticas: {exact_matches}/{len(b_map)} | "
                f"Prom. top: {b_avg:.2f}% -> {c_avg:.2f}%"
            )

        elif kind == "sugerir_combinaciones_en_retazos":
            b_map = base.get("best_per_remnant", {})
            c_map = curr.get("best_per_remnant", {})
            exact_matches = 0
            for rid, b_eff in b_map.items():
                c_eff = c_map.get(rid)
                if c_eff is None:
                    regressions.append(f"retazo #{rid} sin propuesta (era {b_eff:.2f}%)")
                elif c_eff < b_eff - tol:
                    regressions.append(f"retazo #{rid}: {b_eff:.3f}% -> {c_eff:.3f}%")
                elif c_eff > b_eff + tol:
                    improvements.append(f"retazo #{rid}: {b_eff:.3f}% -> {c_eff:.3f}%")
                else:
                    exact_matches += 1
            b_avg = (sum(b_map.values()) / len(b_map)) if b_map else 0.0
            c_avg = (sum(c_map.values()) / len(c_map)) if c_map else 0.0
            quality_summary = (
                f"Propuestas: {base['proposals_count']} -> {curr['proposals_count']} | "
                f"Retazos idénticos: {exact_matches}/{len(b_map)} | "
                f"Prom. efic: {b_avg:.2f}% -> {c_avg:.2f}%"
            )

        status = "FAIL" if regressions else ("MEJORA" if improvements else "OK")
        if regressions:
            ok = False
        line = (
            f"[{status:6}] {name:<40} | "
            f"{b_ms:8.1f} ms -> {c_ms:8.1f} ms ({delta_pct:+6.1f}%, {speedup:.2f}x)\n"
            f"         Calidad: {quality_summary}"
        )
        if regressions:
            line += "\n         REGRESIÓN: " + "; ".join(regressions)
        if improvements:
            line += "\n         MEJORA   : " + "; ".join(improvements)
        lines.append(line)

    total_delta = ((total_curr_ms - total_base_ms) / total_base_ms * 100.0) if total_base_ms > 0 else 0.0
    total_speedup = (total_base_ms / total_curr_ms) if total_curr_ms > 0 else 1.0
    lines.append("-" * 92)
    lines.append(
        f"TOTAL: {total_base_ms:.1f} ms -> {total_curr_ms:.1f} ms "
        f"({total_delta:+.1f}%, {total_speedup:.2f}x)"
    )
    return ok, lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark y validador de calidad Optimizador CDI")
    parser.add_argument("--save-baseline", help="Guarda los resultados actuales como JSON de línea base")
    parser.add_argument("--compare", help="Compara la ejecución actual contra un JSON de línea base")
    parser.add_argument("--rip-dir", help="Carpeta RIP personalizada (por defecto se lee de QSettings)")
    parser.add_argument("--retazos-db", help="Ruta a retazos.sqlite (por defecto %LOCALAPPDATA%)")
    parser.add_argument("--solo-sintetico", action="store_true", help="Ejecuta solo casos sintéticos")
    parser.add_argument("--max-real-pieces", type=int, default=24, help="Máximo de piezas reales por grupo")
    parser.add_argument("--remnant-budget", type=float, default=15.0, help="Presupuesto en segundos para retazos")
    args = parser.parse_args()

    config, rip_dir, retazos_db = cargar_configuracion_app(args.rip_dir, args.retazos_db)
    print(f"Carpeta RIP detectada : {rip_dir or '(no encontrada / solo sintético)'}")
    print(f"BD Retazos detectada  : {retazos_db or '(no encontrada)'}")
    print("Ejecutando pruebas de calidad y rendimiento...\n")

    current = ejecutar_suite(
        config,
        rip_dir,
        retazos_db,
        solo_sintetico=args.solo_sintetico,
        max_real_pieces=args.max_real_pieces,
        remnant_budget=args.remnant_budget,
    )

    if args.save_baseline:
        out_path = Path(args.save_baseline)
        out_path.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Línea base guardada en: {out_path.resolve()}\n")
        for name, data in current["cases"].items():
            inv = len(data.get("invariant_errors", []))
            print(f"  - {name:<42} | {data['elapsed_ms']:8.1f} ms | errores_invariante={inv}")
        return 0

    if args.compare:
        baseline = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        ok, lines = comparar_reportes(baseline, current)
        print("\n".join(lines))
        return 0 if ok else 1

    # Modo informativo rápido
    for name, data in current["cases"].items():
        inv = len(data.get("invariant_errors", []))
        print(f"  - {name:<42} | {data['elapsed_ms']:8.1f} ms | errores_invariante={inv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
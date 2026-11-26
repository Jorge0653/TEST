import tempfile
import unittest
import sqlite3
from threading import Event
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cdi_optimizer.core import (
    escanear_carpeta_rip,
    extraer_medidas_xmp,
    generar_alternativas_archivos,
    optimizar_piezas,
    sugerir_combinaciones,
)
from cdi_optimizer.core.optimizer import (
    _evaluar_perfiles,
    _evaluar_perfiles_secuencialmente,
    _geometry_signature,
    optimizar_archivos,
)
from cdi_optimizer.core import metadata
from cdi_optimizer.core import packing
from cdi_optimizer.core.packing import _transpose_packed
from cdi_optimizer.core.remnants import (
    InventarioRetazos,
    _limite_candidatos_retazo,
    _mejor_acomodo_retazo,
    buscar_acomodos_en_retazos,
    sugerir_combinaciones_en_retazos,
    sugerir_distribucion_en_retazos,
)
from cdi_optimizer.core.recommender import _limite_candidatos_sugerencia
from cdi_optimizer.logging_config import configure_logging, configure_logging_safely
from cdi_optimizer.ui.main_window import (
    OptimizadorCDI,
    _retazo_suggestions_cache_key,
    _suggestion_cache_key,
)


class MetadataTests(unittest.TestCase):
    def test_metadata_cache_reuses_unchanged_file_and_invalidates_on_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cached.len"
            path.write_bytes(
                b"<x:xmpmeta><egGr:vsize>51</egGr:vsize>"
                b"<egGr:hsize>240</egGr:hsize></x:xmpmeta>"
            )
            parser = metadata._leer_medidas_xmp
            with patch(
                "cdi_optimizer.core.metadata._leer_medidas_xmp",
                wraps=parser,
            ) as parse:
                first = extraer_medidas_xmp(path)
                first["ancho_mm"] = 999
                second = extraer_medidas_xmp(path)
                self.assertEqual(second["ancho_mm"], 240)
                self.assertEqual(parse.call_count, 1)

                path.write_bytes(
                    b"<x:xmpmeta><egGr:vsize>51</egGr:vsize>"
                    b"<egGr:hsize>1240</egGr:hsize></x:xmpmeta>"
                )
                updated = extraer_medidas_xmp(path)

            self.assertEqual(updated["ancho_mm"], 1240)
            self.assertEqual(parse.call_count, 2)

    def test_extracts_namespaced_dimensions_from_len_tail(self):
        xmp = (
            b'<x:xmpmeta><rdf:RDF><egGr:vsize>123.5</egGr:vsize>'
            b'<egGr:hsize>456.25</egGr:hsize></rdf:RDF></x:xmpmeta>'
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "job.len"
            path.write_bytes(b"x" * 300_000 + xmp)
            self.assertEqual(
                extraer_medidas_xmp(path),
                {"alto_mm": 123.5, "ancho_mm": 456.25},
            )

    def test_extracts_xmp_resolution(self):
        xmp = (
            b"<x:xmpmeta><egGr:vsize>51</egGr:vsize><egGr:hsize>240</egGr:hsize>"
            b"<egGr:vresolution>4000.</egGr:vresolution>"
            b"<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dpi.len"
            path.write_bytes(xmp)
            self.assertEqual(
                extraer_medidas_xmp(path),
                {
                    "alto_mm": 51.0,
                    "ancho_mm": 240.0,
                    "resolucion_y_dpi": 4000.0,
                    "resolucion_x_dpi": 4000.0,
                },
            )

    def test_extracts_dimensions_with_trailing_decimal_point(self):
        xmp = (
            b"<x:xmpmeta><egGr:vsize>93.</egGr:vsize><egGr:hsize>157.</egGr:hsize>"
            b"<egGr:vresolution>2540.</egGr:vresolution>"
            b"<egGr:hresolution>2540.</egGr:hresolution></x:xmpmeta>"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "64343_sample.len"
            path.write_bytes(xmp)
            self.assertEqual(
                extraer_medidas_xmp(path),
                {
                    "alto_mm": 93.0,
                    "ancho_mm": 157.0,
                    "resolucion_y_dpi": 2540.0,
                    "resolucion_x_dpi": 2540.0,
                },
            )

    def test_does_not_join_partial_xmp_from_separate_file_regions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "split.len"
            path.write_bytes(
                b"<x:xmpmeta><egGr:vsize>120</egGr:vsize>"
                + b"x" * 300_000
                + b"<egGr:hsize>450</egGr:hsize></x:xmpmeta>"
            )
            self.assertIsNone(extraer_medidas_xmp(path))

    def test_scanner_groups_case_insensitive_len_files(self):
        with tempfile.TemporaryDirectory() as directory:
            for filename in ("Job_A_Cyan.LEN", "Job_A_Magenta.len", "notes.txt"):
                (Path(directory) / filename).touch()
            jobs = escanear_carpeta_rip(directory)
            self.assertEqual(set(jobs), {"Job_A"})
            self.assertEqual(len(jobs["Job_A"]), 2)

    def test_scanner_extracts_photopolymer_from_new_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            for filename in (
                "64354-Ejemplo_FP67_K.len",
                "64354-Ejemplo_FP67_C.len",
            ):
                (Path(directory) / filename).touch()

            jobs = escanear_carpeta_rip(directory)

        self.assertEqual(set(jobs), {"64354-Ejemplo"})
        self.assertEqual(
            {ink["material_fotopolimero"] for ink in jobs["64354-Ejemplo"]},
            {"FP67"},
        )
    
        


class SuggestionCacheTests(unittest.TestCase):
    def test_cache_key_changes_when_files_or_config_change(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "64330 Sample_C.len"
            second = Path(directory) / "64330 Sample_K.len"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            paths = [str(first), str(second)]
            config = {"ratio_maximo": 3.0, "rotacion": True}

            original = _suggestion_cache_key(paths, config)
            self.assertEqual(original, _suggestion_cache_key(paths, config))
            self.assertNotEqual(
                original,
                _suggestion_cache_key(paths, {**config, "ratio_maximo": 2.5}),
            )

            first.write_bytes(b"changed and longer")
            self.assertNotEqual(original, _suggestion_cache_key(paths, config))

    def test_remnant_cache_key_tracks_geometry_material_and_budget(self):
        paths = ["one.len", "two.len"]
        config = {"ratio_maximo": 3.0, "rotacion": True}
        remnants = [{"id": 1, "ancho_mm": 455, "alto_mm": 327, "material": "Cartón"}]
        original = _retazo_suggestions_cache_key(paths, config, remnants, 60)
        self.assertEqual(
            original, _retazo_suggestions_cache_key(paths, config, remnants, 60)
        )
        self.assertNotEqual(
            original,
            _retazo_suggestions_cache_key(
                paths, config, [{**remnants[0], "ancho_mm": 450}], 60
            ),
        )
        self.assertNotEqual(
            original,
            _retazo_suggestions_cache_key(
                paths, config, [{**remnants[0], "material": "Poliéster"}], 60
            ),
        )
        self.assertNotEqual(
            original, _retazo_suggestions_cache_key(paths, config, remnants, 120)
        )


class ParallelProfileTests(unittest.TestCase):
    def setUp(self):
        self.pieces = [
            {"name": f"Piece_{index}", "width_mm": width, "height_mm": height}
            for index, (width, height) in enumerate(
                [(40, 20)] * 4 + [(32, 18)] * 4 + [(25, 25)] * 4 + [(45, 15)] * 4
            )
        ]
        base = {
            "tambor_w": 300,
            "tambor_h": 300,
            "margen": 0,
            "pinza": 0,
            "tolerancia": 0,
            "espaciado": 1,
            "rotacion": True,
            "ratio_maximo": 3,
        }
        self.profiles = [
            {**base, "ratio_objetivo": 1.0, "prioridad_aspecto": 0},
            {
                **base,
                "ratio_objetivo": 1.75,
                "prioridad_aspecto": 50,
                "agrupar_dimensiones": True,
            },
        ]

    def test_small_batches_do_not_start_processes(self):
        with patch("cdi_optimizer.core.optimizer.ProcessPoolExecutor") as pool:
            result = _evaluar_perfiles(self.pieces[:8], self.profiles)

        self.assertEqual(len(result), len(self.profiles))
        pool.assert_not_called()

    def test_parallel_profiles_match_sequential_results(self):
        sequential = _evaluar_perfiles_secuencialmente(
            self.pieces, self.profiles, None
        )
        parallel = _evaluar_perfiles(self.pieces, self.profiles)

        self.assertEqual(parallel, sequential)

    def test_pre_cancelled_parallel_evaluation_returns_none(self):
        cancel_event = Event()
        cancel_event.set()

        self.assertIsNone(
            _evaluar_perfiles(self.pieces, self.profiles, cancel_event)
        )


class LoggingTests(unittest.TestCase):
    def test_logging_writes_traceback_once_and_unwritable_path_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            log_directory = Path(directory) / "logs"
            logger = configure_logging(log_directory)
            try:
                logger.error("benchmark traceback", exc_info=(ValueError, ValueError("sample"), None))
                for handler in logger.handlers:
                    handler.flush()
                log_path = log_directory / "optimizador.log"
                contents = log_path.read_text(encoding="utf-8")
                self.assertIn("benchmark traceback", contents)
                self.assertEqual(
                    sum(
                        1
                        for handler in logger.handlers
                        if getattr(handler, "baseFilename", None)
                        == str(log_path.resolve())
                    ),
                    1,
                )
            finally:
                for handler in list(logger.handlers):
                    if getattr(handler, "baseFilename", None) == str(log_path.resolve()):
                        logger.removeHandler(handler)
                        handler.close()

        with tempfile.TemporaryDirectory() as directory:
            not_a_directory = Path(directory) / "file"
            not_a_directory.write_text("occupied", encoding="utf-8")
            logger = configure_logging_safely(not_a_directory / "logs")
            self.assertTrue(logger.handlers)


class PackingTests(unittest.TestCase):
    def test_transposed_layout_is_compacted_to_bottom_left(self):
        packed = {
            "ancho_util": 100,
            "alto_util": 80,
            "grabados": [
                {"nombre": "A", "x": 5, "y": 65, "w": 30, "h": 10, "rotado": False},
                {"nombre": "B", "x": 45, "y": 5, "w": 50, "h": 10, "rotado": False},
            ],
        }

        transposed = _transpose_packed(packed, spacing=5)

        first, second = transposed["grabados"]
        self.assertEqual(min(part["x"] for part in transposed["grabados"]), 5)
        self.assertEqual(
            {part["y"] + part["h"] for part in transposed["grabados"]},
            {transposed["alto_util"] - 5},
        )
        self.assertEqual(second["x"] - first["x"] - first["w"], 10)

    def test_places_rectangles_without_overlap_and_reduces_area(self):
        result = optimizar_piezas(
            [
                {"name": "A", "width_mm": 400, "height_mm": 200},
                {"name": "B", "width_mm": 200, "height_mm": 200},
            ],
            {
                "tambor_w": 600,
                "tambor_h": 600,
                "margen": 0,
                "pinza": 0,
                "tolerancia": 0,
                "espaciado": 0,
                "rotacion": False,
            },
        )
        board = result["placa_unica"]
        # El motor optimizado puede devolver el contenedor en orientación horizontal o vertical equivalente
        self.assertEqual(board["ancho"] * board["alto"], 200 * 600)
        first, second = board["grabados"]
        overlaps = (
            first["x"] < second["x"] + second["w"]
            and first["x"] + first["w"] > second["x"]
            and first["y"] < second["y"] + second["h"]
            and first["y"] + first["h"] > second["y"]
        )
        self.assertFalse(overlaps)
        self.assertLess(board["ancho"] * board["alto"], 400 * 400)

    

    def test_rotates_when_needed_and_obeys_drum_limits(self):
        result = optimizar_piezas(
            [{"name": "Wide", "width_mm": 80, "height_mm": 50}],
            {
                "tambor_w": 70,
                "tambor_h": 100,
                "margen": 2,
                "pinza": 10,
                "tolerancia": 2,
                "espaciado": 0,
                "rotacion": True,
            },
        )
        board = result["placa_unica"]
        self.assertTrue(board["grabados"][0]["rotado"])
        self.assertLessEqual(board["ancho"], 70)
        self.assertLessEqual(board["alto"], 100)


    def test_split_keeps_complete_equal_size_groups_together(self):
        pieces = [
            {"name": f"Large_{index}", "width_mm": 240, "height_mm": 51}
            for index in range(6)
        ] + [
            {"name": f"Small_{index}", "width_mm": 157, "height_mm": 93}
            for index in range(2)
        ]
        result = optimizar_piezas(
            pieces,
            {
                "tambor_w": 1067,
                "tambor_h": 1270,
                "margen": 7,
                "pinza": 25,
                "tolerancia": 3,
                "espaciado": 1,
                "rotacion": True,
                "prioridad_aspecto": 50,
            },
        )
        split = result["placas_dobles"]

        self.assertIsNotNone(split)
        self.assertEqual(
            sorted(len(plate["grabados"]) for plate in split["placas"]),
            [2, 6],
        )
        for plate in split["placas"]:
            names = [part["nombre"] for part in plate["grabados"]]
            self.assertEqual(len({name.split("_")[0] for name in names}), 1)

    def test_skips_invalid_dimensions_and_reports_them(self):
        result = optimizar_piezas(
            [
                {"name": "Valid", "width_mm": 40, "height_mm": 30},
                {"name": "Broken", "width_mm": "unknown", "height_mm": 10},
            ],
            {"tambor_w": 100, "tambor_h": 100, "margen": 0, "pinza": 0, "tolerancia": 0},
        )
        self.assertEqual(len(result["placa_unica"]["grabados"]), 1)
        self.assertEqual(result["errores"], ["Se omitieron piezas con dimensiones inválidas."])

    def test_returns_error_when_every_piece_has_invalid_dimensions(self):
        result = optimizar_piezas(
            [{"name": "Broken", "width_mm": "unknown", "height_mm": 10}],
            {"tambor_w": 100, "tambor_h": 100},
        )
        self.assertIsNone(result["placa_unica"])
        self.assertEqual(result["errores"], ["Se omitieron piezas con dimensiones inválidas."])

    def test_fixed_profiles_return_unique_options_within_ratio_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(8):
                path = Path(directory) / f"Item_{index}.len"
                path.write_bytes(
                    b"<x:xmpmeta><egGr:vsize>51</egGr:vsize><egGr:hsize>240</egGr:hsize>"
                    b"<egGr:vresolution>4000.</egGr:vresolution>"
                    b"<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>"
                )
                paths.append(str(path))

            result = generar_alternativas_archivos(
                paths,
                {
                    "tambor_w": 1067,
                    "tambor_h": 1270,
                    "margen": 7,
                    "pinza": 25,
                    "tolerancia": 3,
                    "espaciado": 1,
                    "rotacion": True,
                    "ratio_maximo": 3,
                },
            )

        self.assertGreaterEqual(len(result["alternativas"]), 2)
        self.assertLessEqual(len(result["alternativas"]), 4)
        signatures = {
            (
                option["tipo"],
                option["ancho"],
                option["alto"],
                tuple((part["x"], part["y"], part["rotado"]) for part in option["grabados"]),
            )
            for option in result["alternativas"]
        }
        self.assertEqual(len(signatures), len(result["alternativas"]))
        self.assertTrue(
            all(
                max(suggestion["alto"] / suggestion["ancho"], suggestion["ancho"] / suggestion["alto"])
                <= 3
                for suggestion in result["alternativas"]
            )
        )

    def test_rejects_mixed_photopolymer_types(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for material in ("FP45", "FP67"):
                path = Path(directory) / f"64354-Ejemplo_{material}_K.len"
                path.write_bytes(
                    b"<x:xmpmeta><egGr:vsize>51</egGr:vsize>"
                    b"<egGr:hsize>240</egGr:hsize>"
                    b"<egGr:vresolution>4000.</egGr:vresolution>"
                    b"<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>"
                )
                paths.append(str(path))

            result = generar_alternativas_archivos(paths, {"tambor_w": 600, "tambor_h": 600})

        self.assertEqual(result["alternativas"], [])
        self.assertIn("fotopolímero", result["errores"][0])

    def test_suggestions_never_mix_photopolymer_types(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for material in ("FP45", "FP67"):
                for ink in ("C", "K"):
                    path = Path(directory) / f"64354-Ejemplo_{material}_{ink}.len"
                    path.write_bytes(
                        b"<x:xmpmeta><egGr:vsize>51</egGr:vsize>"
                        b"<egGr:hsize>240</egGr:hsize>"
                        b"<egGr:vresolution>4000.</egGr:vresolution>"
                        b"<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>"
                    )
                    paths.append(str(path))

            result = sugerir_combinaciones(
                paths, {"tambor_w": 600, "tambor_h": 600}
            )

        self.assertTrue(result["sugerencias"])
        for suggestion in result["sugerencias"]:
            materials = {
                Path(path).stem.split("_")[-2]
                for path in suggestion["archivos"]
            }
            self.assertEqual(len(materials), 1)

    def test_more_variant_batch_adds_new_layouts_with_a_fixed_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            sizes = [(80, 45), (73, 52), (62, 39), (48, 70), (55, 32), (41, 38)]
            paths = []
            for index, (width, height) in enumerate(sizes):
                path = Path(directory) / f"Item_{index}.len"
                path.write_bytes(
                    f"<x:xmpmeta><egGr:vsize>{height}</egGr:vsize>"
                    f"<egGr:hsize>{width}</egGr:hsize>"
                    "<egGr:vresolution>4000.</egGr:vresolution>"
                    "<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>".encode()
                )
                paths.append(str(path))

            config = {
                "tambor_w": 220,
                "tambor_h": 180,
                "margen_izquierdo": 0,
                "margen_derecho": 0,
                "margen_superior": 0,
                "pinza": 0,
                "tolerancia": 0,
                "espaciado": 0,
                "rotacion": True,
                "ratio_maximo": 3,
            }
            initial = generar_alternativas_archivos(paths, config)["alternativas"]
            batch = generar_alternativas_archivos(
                paths,
                config,
                variant_batch=0,
                existing_alternatives=initial,
            )

        self.assertGreater(len(batch["alternativas"]), 0)
        self.assertLessEqual(len(batch["alternativas"]), 2)
        self.assertTrue(batch["hay_mas_variantes"])
        initial_signatures = {_geometry_signature(option) for option in initial}
        batch_signatures = {
            _geometry_signature(option) for option in batch["alternativas"]
        }
        self.assertTrue(initial_signatures.isdisjoint(batch_signatures))

    def test_more_variant_search_stops_at_ten_visible_options(self):
        result = generar_alternativas_archivos(
            [], variant_batch=0, existing_alternatives=[{}] * 10
        )

        self.assertEqual(result["alternativas"], [])
        self.assertFalse(result["hay_mas_variantes"])

    def test_fixed_profile_generation_honors_cancellation(self):
        from threading import Event

        cancel_event = Event()
        cancel_event.set()
        result = generar_alternativas_archivos([], {}, cancel_event)
        self.assertTrue(result["cancelado"])
        self.assertEqual(result["alternativas"], [])

    def test_requested_profile_cannot_exceed_configured_maximum_ratio(self):
        pieces = [
            {"name": str(index), "width_mm": 240, "height_mm": 51}
            for index in range(2)
        ]
        board = optimizar_piezas(
            pieces,
            {
                "tambor_w": 600,
                "tambor_h": 600,
                "margen": 0,
                "pinza": 0,
                "tolerancia": 0,
                "espaciado": 0,
                "rotacion": True,
                "ratio_maximo": 3,
                "ratio_objetivo": 6,
                "prioridad_aspecto": 100,
            },
            incluir_dividida=False,
        )["placa_unica"]

        self.assertLessEqual(board["ratio_alto_ancho"], 3)
        self.assertFalse(board["ratio_fuera_limite"])

    def test_fixed_profiles_report_when_single_piece_exceeds_ratio_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Long.len"
            path.write_bytes(
                b"<x:xmpmeta><egGr:vsize>51</egGr:vsize><egGr:hsize>240</egGr:hsize>"
                b"<egGr:vresolution>4000.</egGr:vresolution>"
                b"<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>"
            )
            result = generar_alternativas_archivos(
                [str(path)],
                {
                    "tambor_w": 600,
                    "tambor_h": 600,
                    "margen": 0,
                    "pinza": 0,
                    "tolerancia": 0,
                    "espaciado": 0,
                    "rotacion": True,
                    "ratio_maximo": 3,
                },
            )

        self.assertEqual(result["alternativas"], [])
        self.assertIn("ratio máximo", result["errores"][0])

    def test_rotation_never_reduces_efficiency_for_ten_long_engravings(self):
        pieces = [
            {"name": str(index), "width_mm": 240, "height_mm": 51}
            for index in range(10)
        ]
        base = {
            "tambor_w": 1067,
            "tambor_h": 1270,
            "margen": 0,
            "pinza": 0,
            "tolerancia": 0,
            "espaciado": 1,
            "prioridad_aspecto": 100,
        }
        without_rotation = optimizar_piezas(
            pieces, {**base, "rotacion": False}, incluir_dividida=False
        )["placa_unica"]
        with_rotation = optimizar_piezas(
            pieces, {**base, "rotacion": True}, incluir_dividida=False
        )["placa_unica"]

        self.assertGreaterEqual(
            with_rotation["eficiencia_exacta"],
            without_rotation["eficiencia_exacta"],
        )

    def test_square_pieces_skip_redundant_rotation_baseline(self):
        pieces = [
            {"name": str(index), "width_mm": 20, "height_mm": 20}
            for index in range(6)
        ]
        with patch(
            "cdi_optimizer.core.packing._best_pack",
            wraps=packing._best_pack,
        ) as pack:
            with_rotation = packing._best_pack(
                pieces,
                100,
                100,
                0,
                True,
                width_search=False,
            )

        without_rotation = packing._best_pack(
            pieces,
            100,
            100,
            0,
            False,
            width_search=False,
        )
        self.assertEqual(pack.call_count, 1)
        self.assertEqual(with_rotation, without_rotation)

    def test_efficiency_uses_only_the_inner_usable_rectangle(self):
        result = optimizar_piezas(
            [{"name": "One", "width_mm": 40, "height_mm": 30}],
            {
                "tambor_w": 100,
                "tambor_h": 100,
                "margen_izquierdo": 5,
                "margen_derecho": 10,
                "margen_superior": 8,
                "pinza": 20,
                "tolerancia": 2,
                "espaciado": 0,
            },
        )
        board = result["placa_unica"]
        self.assertEqual(board["area_utilizable"], 1200)
        self.assertEqual(board["eficiencia"], 100)

    def test_efficiency_ignores_required_spacing_between_engravings(self):
        result = optimizar_piezas(
            [
                {"name": "A", "width_mm": 40, "height_mm": 20},
                {"name": "B", "width_mm": 40, "height_mm": 20},
            ],
            {
                "tambor_w": 100,
                "tambor_h": 80,
                "margen": 0,
                "pinza": 0,
                "tolerancia": 0,
                "espaciado": 2,
                "rotacion": False,
            },
        )
        board = result["placa_unica"]
        self.assertEqual(board["area_aprovechable"], 1600)
        self.assertEqual(board["eficiencia"], 100)

    def test_spacing_expands_each_engraving_on_all_sides(self):
        result = optimizar_piezas(
            [{"name": "One", "width_mm": 240, "height_mm": 51}],
            {
                "tambor_w": 300,
                "tambor_h": 300,
                "margen": 0,
                "pinza": 0,
                "tolerancia": 0,
                "espaciado": 1,
                "rotacion": True,
            },
            incluir_dividida=False,
        )
        board = result["placa_unica"]
        self.assertEqual((board["ancho"], board["alto"]), (53, 242))
        self.assertEqual((board["grabados"][0]["x"], board["grabados"][0]["y"]), (1, 1))

    @patch(
        "cdi_optimizer.core.packing._two_plate_pack",
        return_value={"eficiencia_exacta": 0.0},
    )
    def test_hides_split_when_its_inner_efficiency_is_lower(self, _split_pack):
        pieces = [{"name": "One", "width_mm": 40, "height_mm": 20}]
        result = optimizar_piezas(
            pieces,
            {
                "tambor_w": 100,
                "tambor_h": 100,
                "margen": 0,
                "pinza": 0,
                "tolerancia": 0,
                "espaciado": 0,
                "rotacion": False,
            },
        )
        self.assertIsNotNone(result["placa_unica"])
        self.assertIsNone(result["placas_dobles"])

    @patch(
        "cdi_optimizer.core.packing._two_plate_pack",
        return_value={"eficiencia_exacta": 98.0},
    )
    def test_keeps_split_within_three_efficiency_points(self, _split_pack):
        result = optimizar_piezas(
            [{"name": "One", "width_mm": 40, "height_mm": 20}],
            {
                "tambor_w": 100,
                "tambor_h": 100,
                "margen": 0,
                "pinza": 0,
                "tolerancia": 0,
                "espaciado": 0,
                "rotacion": False,
            },
        )

        self.assertIsNotNone(result["placas_dobles"])

    def test_heuristic_mode_is_reproducible(self):
        pieces = [
            {"name": str(index), "width_mm": width, "height_mm": height}
            for index, (width, height) in enumerate(
                ((80, 45), (73, 52), (62, 39), (48, 70), (55, 32), (41, 38))
            )
        ]
        config = {
            "tambor_w": 220,
            "tambor_h": 180,
            "margen": 0,
            "pinza": 0,
            "tolerancia": 0,
            "espaciado": 3,
            "rotacion": True,
            "prioridad_aspecto": 35,
        }
        first = optimizar_piezas(pieces, config)
        second = optimizar_piezas(pieces, config)
        self.assertEqual(first, second)
        self.assertIsNotNone(first["placa_unica"])

    def test_joined_plates_keep_independent_sizes_and_share_bottom_edge(self):
        result = optimizar_piezas(
            [
                {"name": "Main", "width_mm": 30, "height_mm": 30},
                {"name": "Remnant", "width_mm": 20, "height_mm": 10},
            ],
            {
                "tambor_w": 60,
                "tambor_h": 60,
                "margen": 0,
                "pinza": 0,
                "tolerancia": 0,
                "espaciado": 0,
                "rotacion": False,
            },
        )
        split = result["placas_dobles"]
        self.assertIsNotNone(split)
        self.assertNotEqual(split["placas"][0]["alto"], split["placas"][1]["alto"])
        self.assertEqual(
            {part["y"] + part["h"] for part in split["grabados"]},
            {split["alto"]},
        )
        self.assertEqual(split["eficiencia"], 100)

    def test_bottom_left_compaction_preserves_configured_spacing(self):
        result = optimizar_piezas(
            [
                {"name": "A", "width_mm": 40, "height_mm": 20},
                {"name": "B", "width_mm": 40, "height_mm": 20},
            ],
            {
                "tambor_w": 120,
                "tambor_h": 80,
                "margen": 0,
                "pinza": 0,
                "tolerancia": 0,
                "espaciado": 6,
                "rotacion": False,
            },
        )
        first, second = result["placa_unica"]["grabados"]
        x_gap = max(first["x"] - second["x"] - second["w"], second["x"] - first["x"] - first["w"], 0)
        y_gap = max(first["y"] - second["y"] - second["h"], second["y"] - first["y"] - first["h"], 0)
        self.assertGreaterEqual(max(x_gap, y_gap), 12)


class RemnantTests(unittest.TestCase):
    def test_direct_remnant_search_uses_current_tree_selection(self):
        selected_paths = ["64588_C.len", "64588_M.len"]
        fake_window = SimpleNamespace(
            remnant_inventory=object(),
            _worker=None,
            _obtener_archivos_seleccionados=lambda: selected_paths,
            _obtener_archivos_disponibles=lambda: [*selected_paths, "64580_K.len"],
            _obtener_retazos_disponibles=lambda: [{"id": 1}],
            _reiniciar_temporizador_inactividad=Mock(),
            _cancelar_precalculo_sugerencias=Mock(),
            _configuracion_actual=lambda: {"margen": 0},
            _on_retazo_search_finished=Mock(),
            _on_calculation_failed=Mock(),
            _actualizar_progreso=Mock(),
            _set_busy=Mock(),
            _thread_pool=Mock(),
        )

        with patch("cdi_optimizer.ui.main_window._RemnantWorker") as worker:
            OptimizadorCDI.buscar_acomodos_en_retazos(fake_window)

        self.assertEqual(worker.call_args.args[0], selected_paths)
        fake_window._thread_pool.start.assert_called_once_with(worker.return_value)

    def test_direct_remnant_search_uses_all_recommendation_profiles(self):
        boards = [
            {"eficiencia_exacta": 72, "ancho": 40, "alto": 40},
            {"eficiencia_exacta": 81.9, "ancho": 38, "alto": 38},
            {"eficiencia_exacta": 79, "ancho": 36, "alto": 42},
            None,
        ]
        with patch(
            "cdi_optimizer.core.remnants.optimizar_piezas",
            side_effect=[{"placa_unica": board, "placas_dobles": None} for board in boards],
        ) as optimize:
            result = _mejor_acomodo_retazo(
                [{"width_mm": 10, "height_mm": 10}],
                {
                    "tambor_w": 100,
                    "tambor_h": 100,
                    "margen": 0,
                    "pinza": 0,
                    "tolerancia": 0,
                    "espaciado": 0,
                    "ratio_maximo": 3,
                },
            )

        self.assertEqual(optimize.call_count, 4)
        self.assertEqual(result["eficiencia_exacta"], 81.9)

    def test_remnant_search_matches_photopolymer_code_in_material_name(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for ink in ("C", "K"):
                path = Path(directory) / f"64354-Ejemplo_FP67_{ink}.len"
                path.write_bytes(
                    b"<x:xmpmeta><egGr:vsize>51</egGr:vsize>"
                    b"<egGr:hsize>240</egGr:hsize>"
                    b"<egGr:vresolution>4000.</egGr:vresolution>"
                    b"<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>"
                )
                paths.append(str(path))
            result = buscar_acomodos_en_retazos(
                paths,
                [
                    {"id": 1, "ancho_mm": 600, "alto_mm": 600, "material": "FP45"},
                    {"id": 2, "ancho_mm": 600, "alto_mm": 600, "material": "67 HUAHUANG"},
                ],
                {
                    "margen": 0,
                    "pinza": 0,
                    "tolerancia": 0,
                    "espaciado": 0,
                    "rotacion": True,
                },
            )

        self.assertEqual([item["retazo_id"] for item in result["resultados"]], [2])

    def test_remnant_search_evaluates_each_photopolymer_group(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for material in ("FP67", "FP112"):
                path = Path(directory) / f"64354-Ejemplo_{material}_K.len"
                path.write_bytes(
                    b"<x:xmpmeta><egGr:vsize>10</egGr:vsize>"
                    b"<egGr:hsize>10</egGr:hsize>"
                    b"<egGr:vresolution>4000.</egGr:vresolution>"
                    b"<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>"
                )
                paths.append(str(path))
            result = buscar_acomodos_en_retazos(
                paths,
                [
                    {"id": 1, "ancho_mm": 20, "alto_mm": 20, "material": "67 HUAHUANG"},
                    {"id": 2, "ancho_mm": 20, "alto_mm": 20, "material": "112 HUAHUANG"},
                ],
                {
                    "espaciado": 0,
                    "rotacion": False,
                    "margen_izquierdo": 0,
                    "margen_derecho": 0,
                    "margen_superior": 0,
                    "pinza": 0,
                    "tolerancia": 0,
                },
            )

        self.assertEqual(
            {(item["retazo_id"], item["material_fotopolimero"]) for item in result["resultados"]},
            {(1, "FP67"), (2, "FP112")},
        )

    def test_remnant_suggestions_match_each_photopolymer_code(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for material in ("FP67", "FP112"):
                path = Path(directory) / f"64354-Ejemplo_{material}_K.len"
                path.write_bytes(
                    b"<x:xmpmeta><egGr:vsize>10</egGr:vsize>"
                    b"<egGr:hsize>10</egGr:hsize>"
                    b"<egGr:vresolution>4000.</egGr:vresolution>"
                    b"<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>"
                )
                paths.append(str(path))
            result = sugerir_combinaciones_en_retazos(
                paths,
                [
                    {"id": 1, "ancho_mm": 20, "alto_mm": 20, "material": "67 HUAHUANG"},
                    {"id": 2, "ancho_mm": 20, "alto_mm": 20, "material": "112 HUAHUANG"},
                ],
                {
                    "espaciado": 0,
                    "rotacion": False,
                    "margen_izquierdo": 0,
                    "margen_derecho": 0,
                    "margen_superior": 0,
                    "pinza": 0,
                    "tolerancia": 0,
                },
                time_budget_seconds=10,
            )

        self.assertEqual(
            {(item["retazo_id"], item["material_fotopolimero"]) for item in result["resultados"]},
            {(1, "FP67"), (2, "FP112")},
        )

    def test_remnant_candidate_budget_scales_down_with_group_size(self):
        self.assertEqual(_limite_candidatos_retazo(1), 16)
        self.assertEqual(_limite_candidatos_retazo(128), 16)
        self.assertEqual(_limite_candidatos_retazo(129), 9)
        self.assertEqual(_limite_candidatos_retazo(256), 9)
        self.assertEqual(_limite_candidatos_retazo(257), 3)
        self.assertEqual(_limite_candidatos_sugerencia(128), 16)
        self.assertEqual(_limite_candidatos_sugerencia(129), 9)
        self.assertEqual(_limite_candidatos_sugerencia(257), 3)

    def test_remnant_candidate_pool_uses_stock_envelope_not_larger_drum(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(4):
                path = Path(directory) / f"Job{index}_Ink0.len"
                path.write_bytes(
                    b"<x:xmpmeta><egGr:vsize>10</egGr:vsize>"
                    b"<egGr:hsize>10</egGr:hsize>"
                    b"<egGr:vresolution>4000.</egGr:vresolution>"
                    b"<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>"
                )
                paths.append(str(path))
            retazos = [
                {"id": 1, "ancho_mm": 40, "alto_mm": 40, "material": "Cartón"}
            ]
            common = {
                "margen_izquierdo": 0,
                "margen_derecho": 0,
                "margen_superior": 0,
                "pinza": 0,
                "tolerancia": 0,
                "espaciado": 0,
                "rotacion": False,
            }
            large_drum = sugerir_combinaciones_en_retazos(
                paths,
                retazos,
                {**common, "tambor_w": 900, "tambor_h": 1200},
            )
            small_drum = sugerir_combinaciones_en_retazos(
                paths,
                retazos,
                {**common, "tambor_w": 800, "tambor_h": 800},
            )

        signature = lambda result: [
            (
                option["retazo_id"],
                option["cantidad"],
                option["archivos_asignados"],
                option["aprovechamiento_retazo"],
            )
            for option in result["resultados"]
        ]
        self.assertEqual(signature(large_drum), signature(small_drum))

    def test_existing_inventory_materials_are_seeded_into_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy.sqlite"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    """CREATE TABLE retazos (
                        id INTEGER PRIMARY KEY,
                        ancho_mm REAL NOT NULL,
                        alto_mm REAL NOT NULL,
                        material TEXT NOT NULL,
                        ubicacion TEXT NOT NULL DEFAULT '',
                        notas TEXT NOT NULL DEFAULT '',
                        creado_en TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )"""
                )
                connection.executemany(
                    "INSERT INTO retazos (ancho_mm, alto_mm, material) VALUES (?, ?, ?)",
                    [(20, 30, "Cartón"), (25, 35, "cartón"), (30, 40, " Cartón ")],
                )
                connection.commit()
            finally:
                connection.close()

            store = InventarioRetazos(database_path)
            materials = store.materiales()
            self.assertEqual(len(materials), 1)
            self.assertEqual(
                {item["material"] for item in store.listar()}, {materials[0]}
            )

    def test_inventory_crud_and_csv_import(self):
        with tempfile.TemporaryDirectory() as directory:
            store = InventarioRetazos(Path(directory) / "inventory.sqlite")
            store.agregar_material("Cartón")
            store.agregar_material("Poliéster")
            with self.assertRaises(ValueError):
                store.agregar_material("cartón")
            remnant_id = store.agregar(20, 30, "Cartón", "Estante A", "Sin daño")
            store.actualizar(remnant_id, 22, 30, "Cartón", "Estante B", "Revisado")
            current = store.listar()
            self.assertEqual(len(current), 1)
            self.assertEqual((current[0]["ancho_mm"], current[0]["alto_mm"]), (22, 30))
            self.assertEqual(current[0]["ubicacion"], "Estante B")

            csv_path = Path(directory) / "remnants.csv"
            csv_path.write_text(
                "alto_mm;material;ancho_mm;ubicacion;notas\n"
                "30;Cartón;20,5;Estante C;CSV\n"
                "25;Poliéster;15;;CSV\n",
                encoding="utf-8-sig",
            )
            self.assertEqual(store.importar_csv(csv_path), 2)
            self.assertEqual(len(store.listar()), 3)
            self.assertEqual(store.materiales(), ["Cartón", "Poliéster"])

            invalid_csv = Path(directory) / "invalid.csv"
            invalid_csv.write_text(
                "ancho_mm,alto_mm,material\n20,not-a-number,Papel\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                store.importar_csv(invalid_csv)
            self.assertEqual(len(store.listar()), 3)
            unknown_material_csv = Path(directory) / "unknown-material.csv"
            unknown_material_csv.write_text(
                "ancho_mm,alto_mm,material\n20,30,Carton typo\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "no está dado de alta"):
                store.importar_csv(unknown_material_csv)
            self.assertEqual(len(store.listar()), 3)
            store.eliminar(remnant_id)
            self.assertEqual(len(store.listar()), 2)

    def test_material_catalog_rename_updates_remnants_and_prevents_used_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            store = InventarioRetazos(Path(directory) / "inventory.sqlite")
            store.agregar_material("Cartón")
            remnant_id = store.agregar(20, 30, "cartón")
            with self.assertRaises(ValueError):
                store.eliminar_material("Cartón")
            store.actualizar_material("Cartón", "Cartón compacto")
            self.assertEqual(store.listar()[0]["material"], "Cartón compacto")
            with self.assertRaises(ValueError):
                store.agregar_material("cartón COMPACTO")
            store.eliminar(remnant_id)
            store.eliminar_material("Cartón compacto")
            self.assertEqual(store.materiales(), [])

    def test_remnant_search_fits_exact_dimensions_without_modifying_input(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(2):
                path = Path(directory) / f"Job_Ink{index}.len"
                path.write_bytes(
                    b"<x:xmpmeta><egGr:vsize>10</egGr:vsize>"
                    b"<egGr:hsize>10</egGr:hsize>"
                    b"<egGr:vresolution>4000.</egGr:vresolution>"
                    b"<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>"
                )
                paths.append(str(path))
            remnants = [
                {"id": 1, "ancho_mm": 20, "alto_mm": 20, "material": "Cartón"},
                {"id": 2, "ancho_mm": 5, "alto_mm": 5, "material": "Cartón"},
            ]
            result = buscar_acomodos_en_retazos(
                paths,
                remnants,
                {
                    "espaciado": 0,
                    "rotacion": True,
                    "ratio_maximo": 3,
                    "margen_izquierdo": 0,
                    "margen_derecho": 0,
                    "margen_superior": 0,
                    "pinza": 0,
                    "tolerancia": 0,
                },
            )

        self.assertFalse(result["cancelado"])
        self.assertEqual([match["retazo_id"] for match in result["resultados"]], [1])
        match = result["resultados"][0]
        self.assertLessEqual(match["ancho"], remnants[0]["ancho_mm"])
        self.assertLessEqual(match["alto"], remnants[0]["alto_mm"])
        self.assertEqual(
            sorted((match["ancho_acomodo"], match["alto_acomodo"])), [10, 20]
        )
        self.assertEqual((match["ancho"], match["alto"]), (20, 20))
        self.assertEqual(match["area_utilizable"], 400)
        self.assertEqual(match["aprovechamiento_retazo"], 50)

    def test_remnant_search_rotates_physical_stock_and_applies_margins(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(2):
                path = Path(directory) / f"Job_Ink{index}.len"
                path.write_bytes(
                    b"<x:xmpmeta><egGr:vsize>10</egGr:vsize>"
                    b"<egGr:hsize>10</egGr:hsize>"
                    b"<egGr:vresolution>4000.</egGr:vresolution>"
                    b"<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>"
                )
                paths.append(str(path))
            remnants = [
                {"id": 1, "ancho_mm": 320, "alto_mm": 230, "material": "Cartón"},
                {"id": 2, "ancho_mm": 230, "alto_mm": 320, "material": "Cartón"},
            ]
            config = {
                "espaciado": 0,
                "rotacion": False,
                "margen_izquierdo": 7,
                "margen_derecho": 7,
                "margen_superior": 7,
                "pinza": 25,
                "tolerancia": 3,
            }
            result = buscar_acomodos_en_retazos(paths, remnants, config)
            margin_limited = buscar_acomodos_en_retazos(
                paths,
                [{"id": 3, "ancho_mm": 20, "alto_mm": 20, "material": "Cartón"}],
                {
                    **config,
                    "margen_izquierdo": 2,
                    "margen_derecho": 2,
                    "margen_superior": 2,
                    "pinza": 3,
                    "tolerancia": 1,
                },
            )

        self.assertEqual(
            {match["retazo_id"] for match in result["resultados"]}, {1, 2}
        )
        by_id = {match["retazo_id"]: match for match in result["resultados"]}
        self.assertTrue(by_id[1]["retazo_rotado"])
        self.assertFalse(by_id[2]["retazo_rotado"])
        for match in by_id.values():
            self.assertLessEqual(match["ancho"], match["alto"])
            self.assertLessEqual(match["ancho"], 230)
            self.assertLessEqual(match["alto"], 320)
            self.assertEqual((match["ancho"], match["alto"]), (230, 320))
            self.assertEqual(match["area_utilizable"], 213 * 285)
            self.assertEqual(
                match["aprovechamiento_retazo"], round(100 * 200 / (213 * 285), 1)
            )
            self.assertEqual(match["pinza"], 25)
            self.assertGreaterEqual(min(part["x"] for part in match["grabados"]), 7)
            self.assertGreaterEqual(min(part["y"] for part in match["grabados"]), 7)
            self.assertLessEqual(
                max(part["x"] + part["w"] for part in match["grabados"]),
                match["ancho"] - 10,
            )
            self.assertLessEqual(
                max(part["y"] + part["h"] for part in match["grabados"]),
                match["alto"] - 28,
            )
        self.assertEqual(margin_limited["resultados"], [])

    def test_remnant_efficiency_uses_full_455_by_327_useful_area(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(2):
                path = Path(directory) / f"Job_Ink{index}.len"
                path.write_bytes(
                    b"<x:xmpmeta><egGr:vsize>10</egGr:vsize>"
                    b"<egGr:hsize>10</egGr:hsize>"
                    b"<egGr:vresolution>4000.</egGr:vresolution>"
                    b"<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>"
                )
                paths.append(str(path))
            result = buscar_acomodos_en_retazos(
                paths,
                [{"id": 1, "ancho_mm": 455, "alto_mm": 327, "material": "Cartón"}],
                {
                    "espaciado": 0,
                    "rotacion": False,
                    "margen_izquierdo": 7,
                    "margen_derecho": 7,
                    "margen_superior": 7,
                    "pinza": 25,
                    "tolerancia": 3,
                },
            )

        match = result["resultados"][0]
        useful_area = (327 - 7 - 7 - 3) * (455 - 7 - 25 - 3)
        self.assertEqual((match["ancho"], match["alto"]), (327, 455))
        self.assertEqual((match["retazo_ancho"], match["retazo_alto"]), (455, 327))
        self.assertEqual(match["area_utilizable"], useful_area)
        self.assertEqual(match["aprovechamiento_retazo"], round(100 * 200 / useful_area, 1))
        self.assertEqual(min(part["x"] for part in match["grabados"]), 7)
        self.assertEqual(
            max(part["y"] + part["h"] for part in match["grabados"]),
            455 - 25 - 3,
        )

    def test_single_ink_fits_and_gets_suggested_in_remnants_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "One_Ink.len"
            path.write_bytes(
                b"<x:xmpmeta><egGr:vsize>10</egGr:vsize>"
                b"<egGr:hsize>10</egGr:hsize>"
                b"<egGr:vresolution>4000.</egGr:vresolution>"
                b"<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>"
            )
            remnant = [
                {"id": 1, "ancho_mm": 20, "alto_mm": 20, "material": "Cartón"}
            ]
            config = {
                "tambor_w": 100,
                "tambor_h": 100,
                "espaciado": 0,
                "rotacion": False,
                "margen_izquierdo": 0,
                "margen_derecho": 0,
                "margen_superior": 0,
                "pinza": 0,
                "tolerancia": 0,
            }
            direct = buscar_acomodos_en_retazos([str(path)], remnant, config)
            suggested = sugerir_combinaciones_en_retazos(
                [str(path)], remnant, config, time_budget_seconds=10
            )
            standard = sugerir_combinaciones([str(path)], config)

        self.assertEqual(len(direct["resultados"]), 1)
        self.assertEqual(len(suggested["resultados"]), 1)
        self.assertEqual(suggested["resultados"][0]["cantidad"], 1)
        self.assertEqual(suggested["resultados"][0]["archivos_asignados"], [str(path)])
        self.assertEqual(standard["sugerencias"], [])

    def test_remnant_summary_uses_visible_remnant_assignment(self):
        previous_paths = ["old-layout-ink-a.len", "old-layout-ink-b.len"]
        remnant_paths = ["retazo-choice-ink-a.len"]
        fake = SimpleNamespace(
            _remnant_view_active=True,
            _retazo_visualizado={"archivos_asignados": remnant_paths},
            _calculation_paths_snapshot=previous_paths,
            _calculation_available_snapshot=[*previous_paths, *remnant_paths],
            _obtener_archivos_disponibles=lambda: [
                *previous_paths,
                *remnant_paths,
            ],
        )
        with patch("cdi_optimizer.ui.main_window._DialogoResumen") as summary:
            OptimizadorCDI._mostrar_resumen_calculo(fake)
        summary.assert_called_once_with(remnant_paths, [*previous_paths, *remnant_paths], fake)

    def test_remnant_suggestions_return_best_subset_for_each_remnant(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(5):
                path = Path(directory) / f"Job{index}_Ink0.len"
                path.write_bytes(
                    b"<x:xmpmeta><egGr:vsize>10</egGr:vsize>"
                    b"<egGr:hsize>10</egGr:hsize>"
                    b"<egGr:vresolution>4000.</egGr:vresolution>"
                    b"<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>"
                )
                paths.append(str(path))
            remnants = [
                {"id": 1, "ancho_mm": 20, "alto_mm": 20, "material": "Cartón"},
                {"id": 2, "ancho_mm": 20, "alto_mm": 20, "material": "Cartón"},
            ]
            result = sugerir_distribucion_en_retazos(
                paths,
                remnants,
                {
                    "espaciado": 0,
                    "rotacion": False,
                    "margen_izquierdo": 0,
                    "margen_derecho": 0,
                    "margen_superior": 0,
                    "pinza": 0,
                    "tolerancia": 0,
                },
            )

        self.assertEqual(result["total_grabados"], 5)
        self.assertEqual(len(result["resultados"]), 2)
        self.assertEqual(
            {option["retazo_id"] for option in result["resultados"]}, {1, 2}
        )
        for option in result["resultados"]:
            self.assertGreaterEqual(option["cantidad"], 2)
            self.assertEqual(option["cantidad"], len(option["archivos_asignados"]))
            self.assertEqual(
                option["aprovechamiento_retazo"], option["eficiencia_exacta"]
            )

    def test_remnant_suggestion_cancellation_preserves_partial_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(5):
                path = Path(directory) / f"Job{index}_Ink0.len"
                path.write_bytes(
                    b"<x:xmpmeta><egGr:vsize>10</egGr:vsize>"
                    b"<egGr:hsize>10</egGr:hsize>"
                    b"<egGr:vresolution>4000.</egGr:vresolution>"
                    b"<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>"
                )
                paths.append(str(path))
            cancel_event = Event()
            partial_results = []

            def cancel_after_first_match(result):
                partial_results.append(result)
                if result["propuestas_encontradas"]:
                    cancel_event.set()

            result = sugerir_combinaciones_en_retazos(
                paths,
                [{"id": 1, "ancho_mm": 20, "alto_mm": 20, "material": "Cartón"}],
                {
                    "espaciado": 0,
                    "rotacion": False,
                    "margen_izquierdo": 0,
                    "margen_derecho": 0,
                    "margen_superior": 0,
                    "pinza": 0,
                    "tolerancia": 0,
                },
                cancel_event=cancel_event,
                partial_callback=cancel_after_first_match,
                time_budget_seconds=30,
            )

        self.assertTrue(result["cancelado"])
        self.assertGreater(result["propuestas_encontradas"], 0)
        self.assertTrue(result["resultados"])
        self.assertTrue(partial_results)
        self.assertTrue(partial_results[-1]["resultados"])


class RecommendationTests(unittest.TestCase):
    def test_close_dimension_tolerance_is_opt_in_and_reports_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for job_name, width in (("64001-BASE", 20.0), ("64002-NEAR", 20.5)):
                for index in range(4):
                    path = Path(directory) / f"{job_name}_Ink{index}.len"
                    path.write_bytes(
                        f"<x:xmpmeta><egGr:vsize>20</egGr:vsize>"
                        f"<egGr:hsize>{width}</egGr:hsize>"
                        "<egGr:vresolution>4000.</egGr:vresolution>"
                        "<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>".encode()
                    )
                    paths.append(str(path))
            config = {
                "tambor_w": 100,
                "tambor_h": 100,
                "margen_izquierdo": 0,
                "margen_derecho": 0,
                "margen_superior": 0,
                "pinza": 0,
                "tolerancia": 0,
                "espaciado": 0,
                "rotacion": False,
                "ratio_maximo": 3,
            }

            exact = sugerir_combinaciones(paths, config)
            tolerant = sugerir_combinaciones(
                paths,
                {
                    **config,
                    "agrupar_tamanos_cercanos": True,
                    "tolerancia_tamanos_cercanos": 1,
                },
            )

        self.assertTrue(
            all(option["coincidencias_tolerancia"] == 0 for option in exact["sugerencias"])
        )
        near_matches = [
            option
            for option in tolerant["sugerencias"]
            if option["cantidad"] == 8 and option["coincidencias_tolerancia"] == 4
        ]
        self.assertTrue(near_matches)
        self.assertEqual(near_matches[0]["coincidencias_exactas"], 4)
        self.assertEqual(near_matches[0]["tolerancia_tamanos_mm"], 1)

    def test_progress_callback_preserves_suggestions_and_finishes_at_100(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index, size in enumerate([(20, 20)] * 4 + [(30, 10)] * 3):
                path = Path(directory) / f"Job_{index}.len"
                path.write_bytes(
                    f"<x:xmpmeta><egGr:vsize>{size[1]}</egGr:vsize>"
                    f"<egGr:hsize>{size[0]}</egGr:hsize>"
                    "<egGr:vresolution>4000.</egGr:vresolution>"
                    "<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>".encode()
                )
                paths.append(str(path))
            config = {
                "tambor_w": 100,
                "tambor_h": 100,
                "margen": 0,
                "pinza": 0,
                "tolerancia": 0,
                "espaciado": 0,
                "rotacion": False,
                "ratio_maximo": 3,
            }

            without_progress = sugerir_combinaciones(paths, config)
            progress = []
            with_progress = sugerir_combinaciones(
                paths,
                config,
                progress_callback=lambda percent, message: progress.append(
                    (percent, message)
                ),
            )

        self.assertEqual(with_progress, without_progress)
        self.assertTrue(progress)
        self.assertEqual(progress[-1][0], 100)
        self.assertTrue(any("Probando acomodos" in message for _, message in progress))

    def test_suggestions_keep_equal_size_groups_together_on_efficiency_ties(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            sizes = [(20, 20)] * 6 + [(30, 10)] * 4
            sizes_by_path = {}
            for index, (width, height) in enumerate(sizes):
                path = Path(directory) / f"Job_{index}.len"
                path.write_bytes(
                    f"<x:xmpmeta><egGr:vsize>{height}</egGr:vsize>"
                    f"<egGr:hsize>{width}</egGr:hsize>"
                    f"<egGr:vresolution>4000.</egGr:vresolution>"
                    f"<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>".encode()
                )
                paths.append(str(path))
                sizes_by_path[str(path)] = (width, height)

            result = sugerir_combinaciones(
                paths,
                {
                    "tambor_w": 100,
                    "tambor_h": 100,
                    "margen": 0,
                    "pinza": 0,
                    "tolerancia": 0,
                    "espaciado": 0,
                    "rotacion": False,
                    "prioridad_aspecto": 50,
                },
            )

        for suggestion in result["sugerencias"]:
            if suggestion["cantidad"] == 6 and suggestion["variante"] == 1:
                dimensions = {
                    tuple(sorted(sizes_by_path[path]))
                    for path in suggestion["archivos"]
                }
                self.assertEqual(len(dimensions), 1)
        six_piece_options = [
            suggestion
            for suggestion in result["sugerencias"]
            if suggestion["cantidad"] == 6
        ]
        self.assertGreaterEqual(len(six_piece_options), 2)
        self.assertIn(1, {suggestion["variante"] for suggestion in six_piece_options})

    def test_suggestions_include_complete_group_plus_partial_second_group(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index, size in enumerate([(20, 20)] * 6 + [(30, 10)] * 4):
                path = Path(directory) / f"Mix_{index}.len"
                path.write_bytes(
                    f"<x:xmpmeta><egGr:vsize>{size[1]}</egGr:vsize>"
                    f"<egGr:hsize>{size[0]}</egGr:hsize>"
                    f"<egGr:vresolution>4000.</egGr:vresolution>"
                    f"<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>".encode()
                )
                paths.append(str(path))

            result = sugerir_combinaciones(
                paths,
                {
                    "tambor_w": 100,
                    "tambor_h": 100,
                    "margen": 0,
                    "pinza": 0,
                    "tolerancia": 0,
                    "espaciado": 0,
                    "rotacion": False,
                    "ratio_maximo": 3,
                },
            )

        self.assertTrue(
            any(
                option["cantidad"] == 7
                and sum(
                    int(name.split("_")[1].split(".")[0]) < 6
                    for name in option["nombres"]
                )
                == 6
                for option in result["sugerencias"]
            )
        )

    def test_ten_identical_files_have_perfect_suggestions_for_each_count(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(10):
                path = Path(directory) / f"Job_{index}.len"
                path.write_bytes(
                    b"<x:xmpmeta><egGr:vsize>20</egGr:vsize><egGr:hsize>20</egGr:hsize>"
                    b"<egGr:vresolution>4000.</egGr:vresolution>"
                    b"<egGr:hresolution>4000.</egGr:hresolution></x:xmpmeta>"
                )
                paths.append(str(path))

            result = sugerir_combinaciones(
                paths,
                {
                    "tambor_w": 200,
                    "tambor_h": 200,
                    "margen": 0,
                    "pinza": 0,
                    "tolerancia": 0,
                    "espaciado": 0,
                    "rotacion": False,
                },
            )

        self.assertEqual(
            [
                suggestion["cantidad"]
                for suggestion in result["sugerencias"]
                if suggestion["variante"] == 1
            ],
            list(range(2, 11)),
        )
        primary_options = [
            suggestion
            for suggestion in result["sugerencias"]
            if suggestion["variante"] == 1
        ]
        self.assertTrue(
            all(
                max(suggestion["alto"] / suggestion["ancho"], suggestion["ancho"] / suggestion["alto"])
                <= 3
                for suggestion in result["sugerencias"]
            )
        )
        self.assertLess(primary_options[3]["eficiencia"], 100)
        self.assertLess(primary_options[5]["eficiencia"], 100)
    def test_suggestions_never_mix_resolutions(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index, resolution in enumerate((3000, 3000, 4000, 4000)):
                path = Path(directory) / f"Sample_{index}.len"
                path.write_bytes(
                    f"<x:xmpmeta><egGr:vsize>20</egGr:vsize><egGr:hsize>20</egGr:hsize>"
                    f"<egGr:vresolution>{resolution}.</egGr:vresolution>"
                    f"<egGr:hresolution>{resolution}.</egGr:hresolution></x:xmpmeta>".encode()
                )
                paths.append(str(path))

            result = sugerir_combinaciones(
                paths,
                {
                    "tambor_w": 80,
                    "tambor_h": 80,
                    "margen": 0,
                    "pinza": 0,
                    "tolerancia": 0,
                    "espaciado": 0,
                    "rotacion": False,
                },
            )
            dimensions_by_path = {
                path: extraer_medidas_xmp(path) for path in paths
            }

        self.assertTrue(result["sugerencias"])
        for suggestion in result["sugerencias"]:
            resolutions = {
                (
                    dimensions_by_path[path]["resolucion_x_dpi"],
                    dimensions_by_path[path]["resolucion_y_dpi"],
                )
                for path in suggestion["archivos"]
            }
            self.assertEqual(len(resolutions), 1)

    def test_optimization_rejects_mixed_resolutions(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index, resolution in enumerate((3000, 4000)):
                path = Path(directory) / f"Sample_{index}.len"
                path.write_bytes(
                    f"<x:xmpmeta><egGr:vsize>20</egGr:vsize><egGr:hsize>20</egGr:hsize>"
                    f"<egGr:vresolution>{resolution}.</egGr:vresolution>"
                    f"<egGr:hresolution>{resolution}.</egGr:hresolution></x:xmpmeta>".encode()
                )
                paths.append(str(path))

            result = optimizar_archivos(paths)

        self.assertIsNone(result["placa_unica"])
        self.assertTrue(result["errores"])
        self.assertIn("distintas resoluciones DPI", result["errores"][0])

    def test_suggestions_cover_each_available_small_quantity(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(6):
                path = Path(directory) / f"Sample_{index}.len"
                path.write_bytes(
                    b"<x:xmpmeta><egGr:vsize>20</egGr:vsize>"
                    b"<egGr:hsize>20</egGr:hsize></x:xmpmeta>"
                )
                paths.append(str(path))

            result = sugerir_combinaciones(
                paths,
                {
                    "tambor_w": 70,
                    "tambor_h": 70,
                    "margen": 0,
                    "pinza": 0,
                    "tolerancia": 0,
                    "espaciado": 0,
                    "rotacion": False,
                },
            )

        self.assertEqual(
            [
                suggestion["cantidad"]
                for suggestion in result["sugerencias"]
                if suggestion["variante"] == 1
            ],
            [2, 3, 4, 5, 6],
        )

    def test_suggests_viable_subsets_from_all_len_files(self):
        with tempfile.TemporaryDirectory() as directory:
            sizes = ((75, 75), (40, 40), (35, 25), (20, 20))
            paths = []
            for index, (width, height) in enumerate(sizes):
                path = Path(directory) / f"Job_{index}.len"
                path.write_bytes(
                    f"<x:xmpmeta><egGr:vsize>{height}</egGr:vsize>".encode()
                    + f"<egGr:hsize>{width}</egGr:hsize></x:xmpmeta>".encode()
                )
                paths.append(str(path))

            result = sugerir_combinaciones(
                paths,
                {
                    "tambor_w": 110,
                    "tambor_h": 110,
                    "margen": 0,
                    "pinza": 0,
                    "tolerancia": 0,
                    "espaciado": 2,
                    "rotacion": True,
                },
                por_categoria=1,
            )
            self.assertTrue(result["sugerencias"])
            for suggestion in result["sugerencias"]:
                self.assertGreaterEqual(suggestion["cantidad"], 2)
                self.assertEqual(suggestion["cantidad"], len(suggestion["archivos"]))
                self.assertTrue(set(suggestion["archivos"]).issubset(paths))


if __name__ == "__main__":
    unittest.main()
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cdi_optimizer.core import (
    escanear_carpeta_rip,
    extraer_medidas_xmp,
    generar_alternativas_archivos,
    optimizar_piezas,
    sugerir_combinaciones,
)
from cdi_optimizer.core.optimizer import _geometry_signature, optimizar_archivos
from cdi_optimizer.core import metadata
from cdi_optimizer.core.packing import _transpose_packed


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
        self.assertEqual((board["ancho"], board["alto"]), (200, 600))
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

    def test_returns_two_rectangular_plates_for_split_alternative(self):
        pieces = [
            {"name": str(index), "width_mm": 60, "height_mm": 40}
            for index in range(4)
        ]
        result = optimizar_piezas(
            pieces,
            {
                "tambor_w": 100,
                "tambor_h": 110,
                "margen": 2,
                "pinza": 10,
                "tolerancia": 2,
                "espaciado": 2,
                "rotacion": False,
            },
        )
        split = result["placas_dobles"]
        self.assertIsNotNone(split)
        self.assertEqual(len(split["placas"]), 2)
        self.assertEqual(len(split["grabados"]), 4)
        self.assertTrue(all(plate["ancho"] <= 100 and plate["alto"] <= 110 for plate in split["placas"]))
        self.assertTrue(all(plate["ancho"] <= plate["alto"] for plate in split["placas"]))

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


class RecommendationTests(unittest.TestCase):
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
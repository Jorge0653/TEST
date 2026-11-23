import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cdi_optimizer.core.remnants import (
    InventarioRetazos,
    calcular_cortes_sobrantes_retazo,
)
from cdi_optimizer.ui.remnants import _etiqueta_fp_material, _SortableTableWidgetItem, _SORT_ROLE


class RemnantInventoryEnhancementsTests(unittest.TestCase):
    def test_batch_insert_locations_and_csv_export_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "retazos.sqlite"
            store = InventarioRetazos(db_path)
            store.agregar_material("67 HUAHUANG")
            store.agregar_material("112 MacDermid")

            ids = store.agregar_lote(
                455, 327, "67 HUAHUANG", ubicacion="Estante A", notas="Lote inicial", cantidad=3
            )
            self.assertEqual(len(ids), 3)
            store.agregar(610, 420, "112 MacDermid", ubicacion="Estante B", notas="")

            self.assertEqual(store.ubicaciones(), ["Estante A", "Estante B"])

            export_path = Path(directory) / "respaldo.csv"
            exported_count = store.exportar_csv(export_path)
            self.assertEqual(exported_count, 4)
            self.assertTrue(export_path.is_file())

            # Verificar que el CSV exportado se puede volver a importar en una base limpia
            second_store = InventarioRetazos(Path(directory) / "restaurado.sqlite")
            second_store.agregar_material("67 HUAHUANG")
            second_store.agregar_material("112 MacDermid")
            imported_count = second_store.importar_csv(export_path)
            self.assertEqual(imported_count, 4)
            self.assertEqual(len(second_store.listar()), 4)

    def test_empty_inventory_exports_header_template(self):
        with tempfile.TemporaryDirectory() as directory:
            store = InventarioRetazos(Path(directory) / "empty.sqlite")
            template_csv = Path(directory) / "plantilla.csv"
            self.assertEqual(store.exportar_csv(template_csv), 0)
            content = template_csv.read_text(encoding="utf-8-sig")
            self.assertIn("ancho_mm;alto_mm;material;ubicacion;notas", content)

    def test_assisted_guillotine_cut_calculation(self):
        remnant = {"id": 14, "ancho_mm": 455, "alto_mm": 327, "material": "67 HUAHUANG"}
        layout_result = {
            "ancho": 327,
            "alto": 455,
            "ancho_acomodo": 210,
            "alto_acomodo": 155,
        }
        cuts = calcular_cortes_sobrantes_retazo(remnant, layout_result)
        self.assertIsNotNone(cuts)
        self.assertEqual(
            (cuts["placa_orientada_ancho"], cuts["placa_orientada_alto"]),
            (327.0, 455.0),
        )
        # Corte vertical conserva tira derecha: (327 - 210) x 455 = 117 x 455
        self.assertEqual(
            (cuts["corte_vertical"]["ancho_mm"], cuts["corte_vertical"]["alto_mm"]),
            (117.0, 455.0),
        )
        # Corte horizontal conserva tira superior: 327 x (455 - 155) = 327 x 300
        self.assertEqual(
            (cuts["corte_horizontal"]["ancho_mm"], cuts["corte_horizontal"]["alto_mm"]),
            (327.0, 300.0),
        )

    def test_fp_material_tag_detection_and_numeric_sorting(self):
        self.assertEqual(_etiqueta_fp_material("67 HUAHUANG"), "FP67")
        self.assertEqual(_etiqueta_fp_material("Polímero FP112 Digital"), "FP112")
        self.assertIsNone(_etiqueta_fp_material("Cartón sin código"))

        small = _SortableTableWidgetItem("200 mm")
        small.setData(_SORT_ROLE, 200.0)
        large = _SortableTableWidgetItem("1000 mm")
        large.setData(_SORT_ROLE, 1000.0)
        self.assertTrue(small < large)


if __name__ == "__main__":
    unittest.main()
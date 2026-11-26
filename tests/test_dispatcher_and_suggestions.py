import tempfile
import unittest
from pathlib import Path

from cdi_optimizer.core import sugerir_combinaciones
from cdi_optimizer.core.dispatcher import (
    MaquinaDestino,
    extraer_rutas_acomodo,
    mover_archivos_a_maquina,
    preparar_envio,
    revertir_envio,
    validar_acomodo_para_maquina,
)


def _escribir_len_prueba(path: Path, ancho: float, alto: float, dpi: float = 2540.0) -> str:
    xmp = (
        f"<x:xmpmeta><egGr:vsize>{alto}</egGr:vsize><egGr:hsize>{ancho}</egGr:hsize>"
        f"<egGr:vresolution>{dpi}</egGr:vresolution><egGr:hresolution>{dpi}</egGr:hresolution>"
        "</x:xmpmeta>"
    ).encode("utf-8")
    path.write_bytes(xmp)
    return str(path)


class DispatcherTests(unittest.TestCase):
    def test_move_and_undo_between_sibling_folders(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            carpeta_03 = base / "03_Entrada"
            carpeta_m1 = base / "01_Maquina1"
            carpeta_03.mkdir()
            carpeta_m1.mkdir()

            f1 = carpeta_03 / "10001-Demo_FP67_C.len"
            f2 = carpeta_03 / "10001-Demo_FP67_M.len"
            f1.write_bytes(b"len-c")
            f2.write_bytes(b"len-m")

            result = mover_archivos_a_maquina([str(f1), str(f2)], str(carpeta_m1))
            self.assertEqual(result["cantidad"], 2)
            self.assertFalse(f1.exists())
            self.assertFalse(f2.exists())
            self.assertTrue((carpeta_m1 / f1.name).is_file())
            self.assertTrue((carpeta_m1 / f2.name).is_file())

            reverted = revertir_envio(result["movidos"])
            self.assertEqual(reverted, 2)
            self.assertTrue(f1.is_file())
            self.assertTrue(f2.is_file())
            self.assertFalse((carpeta_m1 / f1.name).exists())

    def test_detects_collision_and_blocks_without_overwrite(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            src_dir = base / "03_Entrada"
            dst_dir = base / "02_Maquina2"
            src_dir.mkdir()
            dst_dir.mkdir()

            src = src_dir / "job_FP45_K.len"
            dst = dst_dir / "job_FP45_K.len"
            src.write_bytes(b"new")
            dst.write_bytes(b"old")

            check = preparar_envio([str(src)], str(dst_dir))
            self.assertTrue(check["valido"])
            self.assertEqual(check["colisiones"], ["job_FP45_K.len"])

            with self.assertRaises(FileExistsError):
                mover_archivos_a_maquina([str(src)], str(dst_dir), sobrescribir=False)

            mover_archivos_a_maquina([str(src)], str(dst_dir), sobrescribir=True)
            self.assertEqual(dst.read_bytes(), b"new")
            self.assertFalse(src.exists())

    def test_validates_drum_limits_independently_per_machine(self):
        small_machine = MaquinaDestino(1, "CDI Chica", "/tmp", 533, 889)
        large_machine = MaquinaDestino(2, "CDI Grande", "/tmp", 1067, 1270)

        layout = {"ancho": 600.0, "alto": 900.0, "grabados": []}
        self.assertEqual(len(validar_acomodo_para_maquina(layout, small_machine)), 1)
        self.assertEqual(validar_acomodo_para_maquina(layout, large_machine), [])

        # En retazo valida el corte propuesto (ancho_acomodo / alto_acomodo)
        remnant_layout = {
            "ancho": 800.0,
            "alto": 1000.0,
            "ancho_acomodo": 450.0,
            "alto_acomodo": 700.0,
            "grabados": [],
        }
        self.assertEqual(validar_acomodo_para_maquina(remnant_layout, small_machine), [])

    def test_extracts_deduplicated_paths_from_layout(self):
        layout = {
            "grabados": [
                {"ruta": "/a/uno.len"},
                {"ruta": "/a/dos.len"},
                {"ruta": "/a/uno.len"},
            ]
        }
        self.assertEqual(extraer_rutas_acomodo(layout), ["/a/uno.len", "/a/dos.len"])


class MultiMaterialSuggestionsTests(unittest.TestCase):
    def test_all_materials_receive_suggestions_without_crowding_out(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = [
                # FP67 con eficiencia perfecta (idénticos)
                _escribir_len_prueba(root / "10001-A_FP67_C.len", 200, 200),
                _escribir_len_prueba(root / "10001-A_FP67_M.len", 200, 200),
                _escribir_len_prueba(root / "10001-A_FP67_Y.len", 200, 200),
                # FP45 con eficiencia menor pero válida
                _escribir_len_prueba(root / "10002-B_FP45_C.len", 210, 140),
                _escribir_len_prueba(root / "10002-B_FP45_M.len", 130, 190),
                # FP112
                _escribir_len_prueba(root / "10003-C_FP112_K.len", 180, 220),
                _escribir_len_prueba(root / "10003-C_FP112_W.len", 180, 220),
            ]
            result = sugerir_combinaciones(files, max_sugerencias=12)
            sugs = result["sugerencias"]
            materials_present = [s["material_fotopolimero"] for s in sugs]
            self.assertIn("FP45", materials_present)
            self.assertIn("FP67", materials_present)
            self.assertIn("FP112", materials_present)
            # Verificar orden canónico: FP45 antes que FP67 y FP67 antes que FP112
            self.assertLess(materials_present.index("FP45"), materials_present.index("FP67"))
            self.assertLess(materials_present.index("FP67"), materials_present.index("FP112"))


if __name__ == "__main__":
    unittest.main()
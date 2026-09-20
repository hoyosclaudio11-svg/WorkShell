"""Valida los archivos de datos de WorkShell sin tocar el escritorio.

    python -m unittest discover -s tests -v

Invariantes que workshell.py asume al cargar: si alguno se rompe (a mano o por
una edición fallida), el panel arranca raro y nadie sabe por qué. Acá quedan
escritos los supuestos.

Contra qué datos corre: si existen los de la máquina (layouts.json, etc.),
valida esos; si no —por ejemplo en la CI, donde van gitignoreados a propósito—
usa los ejemplos sanitizados de tests/fixtures/.
"""

import json
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def cargar(nombre):
    real = RAIZ / nombre
    if not real.exists():
        real = FIXTURES / nombre
    return json.loads(real.read_text(encoding="utf-8"))


class TestLayouts(unittest.TestCase):
    def setUp(self):
        self.layouts = cargar("layouts.json")

    def test_es_diccionario_con_layouts(self):
        self.assertIsInstance(self.layouts, dict)
        self.assertTrue(self.layouts, "layouts.json vacío: no hay ninguna disposición")

    def test_cada_layout_tiene_paneles_y_ventanas(self):
        for nombre, layout in self.layouts.items():
            self.assertIn("paneles", layout, f"{nombre}: falta 'paneles'")
            self.assertIn("ventanas", layout, f"{nombre}: falta 'ventanas'")
            self.assertIsInstance(layout["paneles"], list, nombre)
            self.assertIsInstance(layout["ventanas"], list, nombre)

    def test_coordinares_en_porcentaje_valido(self):
        for nombre, layout in self.layouts.items():
            for ventana in layout["ventanas"]:
                etiqueta = ventana.get("etiqueta", "?")
                for clave in ("x", "y", "ancho", "alto"):
                    valor = ventana.get(clave)
                    self.assertIsInstance(
                        valor, (int, float),
                        f"{nombre}/{etiqueta}: '{clave}' no es número ({valor!r})",
                    )
                    self.assertGreaterEqual(
                        valor, 0.0, f"{nombre}/{etiqueta}: '{clave}' negativo"
                    )
                    self.assertLessEqual(
                        valor, 100.0,
                        f"{nombre}/{etiqueta}: '{clave}' pasa del 100% ({valor})",
                    )


class TestIgnoradas(unittest.TestCase):
    def test_es_lista_con_proceso_y_etiqueta(self):
        ignoradas = cargar("ignoradas.json")
        self.assertIsInstance(ignoradas, list)
        for regla in ignoradas:
            self.assertTrue(regla.get("proceso"), f"sin proceso: {regla!r}")
            self.assertTrue(regla.get("etiqueta"), f"sin etiqueta: {regla!r}")


class TestServicios(unittest.TestCase):
    def setUp(self):
        self.servicios = cargar("servicios.json")

    def test_es_lista_no_vacia(self):
        self.assertIsInstance(self.servicios, list)
        self.assertTrue(self.servicios, "servicios.json vacío: el panel Servicios queda muerto")

    def test_cada_servicio_tiene_nombre_puerto_y_como_levantarlo(self):
        vistos = set()
        for servicio in self.servicios:
            nombre = servicio.get("nombre")
            self.assertTrue(nombre, f"servicio sin nombre: {servicio!r}")
            self.assertNotIn(nombre, vistos, f"nombre duplicado: {nombre}")
            vistos.add(nombre)

            puerto = servicio.get("puerto")
            self.assertIsInstance(puerto, int, f"{nombre}: puerto no es entero")
            self.assertGreaterEqual(puerto, 1)
            self.assertLessEqual(puerto, 65535)

            self.assertTrue(
                servicio.get("lanzar"), f"{nombre}: sin 'lanzar' no se puede relanzar"
            )


class TestPanel(unittest.TestCase):
    def test_preferencias_en_rango(self):
        panel = cargar("panel.json")
        self.assertIsInstance(panel, dict)
        self.assertIsInstance(panel.get("miniaturas"), bool, "miniaturas debe ser bool")
        self.assertIsInstance(panel.get("opacidad"), int, "opacidad debe ser entero")
        self.assertGreaterEqual(panel["opacidad"], 0, "opacidad: 0 es invisible, pero es válido")
        self.assertLessEqual(panel["opacidad"], 255, "opacidad es un byte Win32 (0-255)")
        if "barra_auto" in panel:
            self.assertIsInstance(panel["barra_auto"], (int, float))
            self.assertGreaterEqual(panel["barra_auto"], 0.0)
            self.assertLessEqual(panel["barra_auto"], 100.0)


if __name__ == "__main__":
    unittest.main()

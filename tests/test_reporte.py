"""Pruebas de generación y estructura del informe PDF."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from datastream.configuracion import Configuracion
from datastream.ia import generar_resumen_local
from datastream.reporte import _normalizar_unicode, generar_reporte_pdf
from datastream.simulador import SimuladorDatacenter


class PruebasReporte(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        config = Configuracion(duracion=10.0, semilla=31, tasa_llegada=5.0)
        simulador = SimuladorDatacenter(config)
        simulador.ejecutar()
        cls.instantanea = simulador.instantanea()
        cls.analisis = generar_resumen_local(cls.instantanea)

    def test_genera_pdf_valido(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "informe.pdf"
            salida = generar_reporte_pdf(self.instantanea, self.analisis, ruta)
            self.assertTrue(salida.exists())
            contenido = salida.read_bytes()
            self.assertTrue(contenido.startswith(b"%PDF-1.4"))
            self.assertTrue(contenido.rstrip().endswith(b"%%EOF"))
            paginas = len(re.findall(rb"/Type /Page\s", contenido))
            self.assertGreaterEqual(paginas, 5)
            self.assertGreater(len(contenido), 20_000)

    def test_agrega_extension_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "informe_sin_extension"
            salida = generar_reporte_pdf(self.instantanea, self.analisis, ruta)
            self.assertEqual(salida.suffix, ".pdf")
            self.assertTrue(salida.exists())

    def test_normaliza_simbolos_para_winansi(self) -> None:
        texto = _normalizar_unicode("Σ λ → ∞ °")
        self.assertEqual(texto, "Sum lambda -> inf °")

    def test_informe_incluye_metricas_clave(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "informe.pdf"
            generar_reporte_pdf(self.instantanea, self.analisis, ruta)
            contenido = ruta.read_bytes()
            for termino in (b"mochila", b"Lagrange", b"SimPy", b"Bellman-Ford"):
                self.assertIn(termino, contenido)


if __name__ == "__main__":
    unittest.main()

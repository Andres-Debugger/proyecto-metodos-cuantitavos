"""Pruebas de topología, congestión y rutas Bellman-Ford."""

from __future__ import annotations

import unittest

from datastream.red import crear_topologia_por_defecto


class PruebasRed(unittest.TestCase):
    def setUp(self) -> None:
        self.topologia = crear_topologia_por_defecto()

    def test_rutas_iniciales(self) -> None:
        esperadas = {
            "S1": ["Internet", "R1", "R2", "S1"],
            "S2": ["Internet", "R1", "R2", "S2"],
            "S3": ["Internet", "R1", "R3", "S3"],
            "S4": ["Internet", "R1", "R3", "S4"],
        }
        for destino, camino in esperadas.items():
            with self.subTest(destino=destino):
                resultado = self.topologia.ruta_mas_corta("Internet", destino)
                self.assertIsNotNone(resultado)
                assert resultado is not None
                self.assertEqual(resultado.camino, camino)
                self.assertTrue(resultado.valida)

    def test_latencia_del_camino(self) -> None:
        resultado = self.topologia.ruta_mas_corta("Internet", "S1")
        assert resultado is not None
        self.assertAlmostEqual(
            self.topologia.latencia_de_camino(resultado.camino),
            resultado.latencia,
            places=10,
        )

    def test_falla_de_router_obliga_a_ruta_alternativa(self) -> None:
        self.topologia.nodos["R2"].operativo = False
        resultado = self.topologia.ruta_mas_corta("Internet", "S1")
        self.assertIsNotNone(resultado)
        assert resultado is not None
        self.assertEqual(resultado.camino, ["Internet", "R1", "R3", "R4", "S1"])
        self.assertNotIn("R2", resultado.camino)

    def test_congestion_aumenta_latencia_y_cambia_ruta(self) -> None:
        enlace = self.topologia.enlace_entre("R1", "R2")
        assert enlace is not None
        base = enlace.latencia_efectiva
        enlace.utilizacion = 1.0
        self.assertGreater(enlace.latencia_efectiva, base)
        resultado = self.topologia.ruta_mas_corta("Internet", "S1")
        assert resultado is not None
        self.assertNotIn("R2", resultado.camino)

    def test_destino_fuera_de_servicio(self) -> None:
        self.topologia.nodos["S1"].operativo = False
        self.assertIsNone(self.topologia.ruta_mas_corta("Internet", "S1"))

    def test_nodo_origen_igual_destino(self) -> None:
        resultado = self.topologia.ruta_mas_corta("R1", "R1")
        assert resultado is not None
        self.assertEqual(resultado.camino, ["R1"])
        self.assertEqual(resultado.latencia, 0.0)

    def test_validaciones_de_construccion(self) -> None:
        with self.assertRaises(ValueError):
            self.topologia.agregar_enlace("R1", "R1", 1.0, 1.0)
        with self.assertRaises(ValueError):
            self.topologia.agregar_enlace("R1", "S1", -1.0, 1.0)
        with self.assertRaises(ValueError):
            self.topologia.agregar_enlace("R1", "S1", 1.0, 0.0)

    def test_instantanea_serializable(self) -> None:
        datos = self.topologia.instantanea()
        self.assertEqual(len(datos["nodos"]), 9)
        self.assertEqual(len(datos["enlaces"]), 14)
        self.assertIn("utilizacion", datos["enlaces"][0])


if __name__ == "__main__":
    unittest.main()

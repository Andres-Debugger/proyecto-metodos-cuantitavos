"""Pruebas del modelo cúbico, el gradiente y el controlador térmico."""

from __future__ import annotations

import unittest

from datastream.termodinamica import (
    ControladorRefrigeracion,
    ModeloRefrigeracion,
    ModeloTemperatura,
)


class PruebasTermodinamica(unittest.TestCase):
    def setUp(self) -> None:
        self.modelo = ModeloTemperatura()
        self.refrigeracion = ModeloRefrigeracion(modelo_temperatura=self.modelo)

    def test_puntos_criticos_del_polinomio(self) -> None:
        puntos = self.modelo.puntos_estacionarios()
        self.assertEqual(len(puntos), 2)
        maximo, minimo = puntos
        self.assertEqual(maximo.tipo, "máximo")
        self.assertAlmostEqual(maximo.carga, 0.6, places=6)
        self.assertAlmostEqual(maximo.temperatura, 60.64, places=2)
        self.assertEqual(minimo.tipo, "mínimo")
        self.assertAlmostEqual(minimo.carga, 1.1, places=6)

    def test_punto_de_inflexion(self) -> None:
        inflexion = self.modelo.puntos_inflexion()
        self.assertEqual(len(inflexion), 1)
        self.assertAlmostEqual(inflexion[0].carga, 0.85, places=6)
        self.assertAlmostEqual(inflexion[0].derivada_segunda, 0.0, places=8)

    def test_punto_de_emergencia(self) -> None:
        punto = self.modelo.punto_critico_emergencia()
        self.assertIsNotNone(punto)
        assert punto is not None
        self.assertAlmostEqual(punto.carga, 0.6, places=6)
        self.assertTrue(self.modelo.requiere_emergencia(0.6))
        self.assertFalse(self.modelo.requiere_emergencia(0.1))

    def test_dominio_de_carga(self) -> None:
        with self.assertRaises(ValueError):
            self.modelo.evaluar(-0.1)
        with self.assertRaises(ValueError):
            self.modelo.evaluar(1.5)
        with self.assertRaises(TypeError):
            self.modelo.evaluar("0.5")

    def test_gradiente_coincide_con_diferencia_finita(self) -> None:
        carga = 0.9
        fan = 0.55
        paso = 1e-6
        numerico = (
            self.refrigeracion.potencia(fan + paso, carga)
            - self.refrigeracion.potencia(fan - paso, carga)
        ) / (2.0 * paso)
        analitico = self.refrigeracion.gradiente(fan, carga)
        self.assertAlmostEqual(numerico, analitico, delta=1e-4)

    def test_minimizacion_reduce_potencia_y_respeta_limites(self) -> None:
        fan_inicial = 0.9
        carga = 0.75
        potencia_inicial = self.refrigeracion.potencia(fan_inicial, carga)
        fan, temperatura, potencia, historial = self.refrigeracion.minimizar(
            carga, ventilador_inicial=fan_inicial
        )
        self.assertGreaterEqual(fan, self.refrigeracion.fan_minimo)
        self.assertLessEqual(fan, self.refrigeracion.fan_maximo)
        self.assertLessEqual(potencia, potencia_inicial)
        self.assertGreater(temperatura, self.refrigeracion.temp_ambiente)
        self.assertGreaterEqual(len(historial), 1)

    def test_controlador_mantiene_estado_coherente(self) -> None:
        controlador = ControladorRefrigeracion("S1", self.refrigeracion)
        estado = controlador.actualizar(carga=0.5, tiempo=1.0)
        self.assertIn("ventilador", estado)
        self.assertGreaterEqual(estado["ventilador"], self.refrigeracion.fan_minimo)
        self.assertLessEqual(estado["ventilador"], self.refrigeracion.fan_maximo)
        self.assertGreaterEqual(estado["temperatura"], self.refrigeracion.temp_ambiente)
        self.assertEqual(len(controlador.historial), 1)


if __name__ == "__main__":
    unittest.main()

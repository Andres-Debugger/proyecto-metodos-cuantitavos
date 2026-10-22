"""Pruebas de la asignación de energía por multiplicadores de Lagrange."""

from __future__ import annotations

import math
import unittest

from datastream.lagrange import OptimizadorEnergia


class PruebasLagrange(unittest.TestCase):
    def setUp(self) -> None:
        self.optimizador = OptimizadorEnergia()

    def test_asignacion_analitica_y_restriccion(self) -> None:
        resultado = self.optimizador.resolver((1.0, 2.0, 3.0), 18.0)
        esperado = (18.0 / 14.0, 72.0 / 14.0, 162.0 / 14.0)
        for obtenido, referencia in zip(resultado.asignacion, esperado):
            self.assertAlmostEqual(obtenido, referencia, places=10)
        self.assertAlmostEqual(sum(resultado.asignacion), 18.0, places=10)

    def test_condiciones_kkt(self) -> None:
        resultado = self.optimizador.resolver((1.2, 1.0, 1.1), 36.0)
        for marginal in resultado.marginales:
            self.assertAlmostEqual(marginal, resultado.multiplicador_lagrange, places=10)

    def test_reparto_optimo_supera_al_igualitario(self) -> None:
        resultado = self.optimizador.resolver((1.25, 1.0, 1.1), 36.0)
        self.assertGreater(resultado.rendimiento_total, resultado.rendimiento_igualitario)
        self.assertGreater(resultado.ganancia_porcentual, 0.0)

    def test_pesos_dinamicos_son_positivos(self) -> None:
        pesos = self.optimizador.pesos_dinamicos((0.2, 0.6, 1.2), (1.2, 1.0, 1.1))
        self.assertEqual(len(pesos), 3)
        self.assertTrue(all(peso > 0 for peso in pesos))

    def test_rechaza_entradas_invalidas(self) -> None:
        with self.assertRaises(ValueError):
            self.optimizador.resolver((1.0, 2.0), 10.0)
        with self.assertRaises(ValueError):
            self.optimizador.resolver((1.0, 0.0, 1.0), 10.0)
        with self.assertRaises(ValueError):
            self.optimizador.resolver((1.0, 1.0, 1.0), -1.0)
        with self.assertRaises(TypeError):
            self.optimizador.resolver((1.0, 1.0, 1.0), "10")
        with self.assertRaises(ValueError):
            self.optimizador.pesos_dinamicos((1.0, 2.0), (1.0, 1.0, 1.0))

    def test_rendimiento_es_finito(self) -> None:
        resultado = self.optimizador.resolver((1.0, 1.0, 1.0), 30.0)
        self.assertTrue(math.isfinite(resultado.rendimiento_total))
        self.assertGreater(resultado.multiplicador_lagrange, 0.0)


if __name__ == "__main__":
    unittest.main()

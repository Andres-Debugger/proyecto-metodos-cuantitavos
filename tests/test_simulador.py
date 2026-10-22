"""Pruebas de integración del motor SimPy y de sus métricas."""

from __future__ import annotations

import json
import math
import unittest

from datastream.configuracion import Configuracion
from datastream.simulador import SimuladorDatacenter


class PruebasSimulador(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        config = Configuracion(duracion=10.0, semilla=15, tasa_llegada=6.0)
        cls.simulador = SimuladorDatacenter(config)
        cls.simulador.ejecutar()

    def test_la_simulacion_termina_y_genera_trafico(self) -> None:
        metricas = self.simulador.metricas()
        self.assertTrue(metricas["terminada"])
        self.assertAlmostEqual(metricas["tiempo_simulado"], 10.0, places=6)
        self.assertGreater(metricas["paquetes_generados"], 0)
        self.assertGreaterEqual(metricas["paquetes_procesados"], 0)

    def test_conteos_coherentes(self) -> None:
        metricas = self.simulador.metricas()
        generados = metricas["paquetes_generados"]
        self.assertLessEqual(metricas["paquetes_procesados"], generados)
        self.assertLessEqual(metricas["paquetes_descartados"], generados)
        self.assertLessEqual(metricas["paquetes_aceptados"], generados)
        self.assertGreaterEqual(metricas["tasa_aceptacion"], 0.0)
        self.assertLessEqual(metricas["tasa_aceptacion"], 1.0)

    def test_rutas_disponibles(self) -> None:
        self.assertEqual(len(self.simulador.rutas_actuales), 4)
        for info in self.simulador.rutas_actuales.values():
            self.assertIn("camino", info)
            self.assertIn("latencia", info)

    def test_teoria_de_colas(self) -> None:
        cola = self.simulador.teoria_colas()
        self.assertEqual(cola["c"], 4)
        self.assertGreater(cola["lambda_total"], 0.0)
        self.assertGreaterEqual(cola["rho"], 0.0)
        self.assertTrue(math.isfinite(cola["Wq"]))
        self.assertTrue(cola["estable"])

    def test_energia_respeta_presupuesto(self) -> None:
        energia = self.simulador.asignacion_energia
        self.assertAlmostEqual(sum(energia.asignacion), 36.0, places=8)
        self.assertGreater(energia.rendimiento_total, 0.0)

    def test_instantanea_es_json_serializable(self) -> None:
        instantanea = self.simulador.instantanea()
        texto = json.dumps(instantanea, allow_nan=False)
        self.assertIn("metricas", texto)
        self.assertIn("modelo_termico", texto)
        self.assertEqual(len(instantanea["servidores"]), 4)

    def test_reproducibilidad_con_la_misma_semilla(self) -> None:
        config = Configuracion(duracion=10.0, semilla=15, tasa_llegada=6.0)
        segundo = SimuladorDatacenter(config)
        segundo.ejecutar()
        primera = self.simulador.metricas()
        segunda = segundo.metricas()
        self.assertEqual(primera["paquetes_generados"], segunda["paquetes_generados"])
        self.assertEqual(primera["paquetes_procesados"], segunda["paquetes_procesados"])
        self.assertEqual(primera["paquetes_descartados"], segunda["paquetes_descartados"])
        self.assertAlmostEqual(
            primera["latencia_promedio"], segunda["latencia_promedio"], places=10
        )

    def test_avanzar_por_pasos_equivale_a_ejecutar(self) -> None:
        config = Configuracion(duracion=10.0, semilla=23, tasa_llegada=5.0)
        por_pasos = SimuladorDatacenter(config)
        while por_pasos.avanzar(0.5):
            pass
        completa = SimuladorDatacenter(config)
        completa.ejecutar()
        self.assertTrue(por_pasos.terminada)
        self.assertEqual(
            por_pasos.metricas()["paquetes_generados"],
            completa.metricas()["paquetes_generados"],
        )
        self.assertEqual(
            por_pasos.metricas()["paquetes_procesados"],
            completa.metricas()["paquetes_procesados"],
        )


if __name__ == "__main__":
    unittest.main()

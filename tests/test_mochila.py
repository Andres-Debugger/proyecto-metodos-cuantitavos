"""Pruebas del solver de mochila binaria bidimensional."""

from __future__ import annotations

import unittest

from datastream.mochila import MochilaBinaria2D, TareaMochila, valor_desde_prioridad


class PruebasMochila(unittest.TestCase):
    def setUp(self) -> None:
        self.solver = MochilaBinaria2D()

    def test_solucion_optima_con_dos_restricciones(self) -> None:
        tareas = [
            TareaMochila("A", cpu=2, ram=2, valor=3),
            TareaMochila("B", cpu=3, ram=3, valor=4),
            TareaMochila("C", cpu=4, ram=4, valor=5),
        ]
        resultado = self.solver.resolver(tareas, capacidad_cpu=5, capacidad_ram=5)
        self.assertEqual([t.id for t in resultado.seleccionados], ["A", "B"])
        self.assertEqual(resultado.valor_total, 7)
        self.assertEqual(resultado.cpu_usada, 5)
        self.assertEqual(resultado.ram_usada, 5)
        self.assertGreater(resultado.celdas_exploradas, 0)

    def test_prioriza_mayor_valor_si_cabe(self) -> None:
        tareas = [
            TareaMochila("grande", cpu=4, ram=4, valor=6),
            TareaMochila("compacta", cpu=2, ram=2, valor=5),
        ]
        resultado = self.solver.resolver(tareas, capacidad_cpu=4, capacidad_ram=4)
        self.assertEqual([t.id for t in resultado.seleccionados], ["grande"])

    def test_capacidad_insuficiente(self) -> None:
        tareas = [TareaMochila("A", cpu=3, ram=3, valor=3)]
        resultado = self.solver.resolver(tareas, capacidad_cpu=2, capacidad_ram=2)
        self.assertEqual(resultado.seleccionados, [])
        self.assertEqual(resultado.valor_total, 0)

    def test_instancia_vacia(self) -> None:
        resultado = self.solver.resolver([], capacidad_cpu=10, capacidad_ram=10)
        self.assertEqual(resultado.cantidad, 0)
        self.assertEqual(resultado.celdas_exploradas, 0)

    def test_rechaza_duplicados_y_dimensiones_invalidas(self) -> None:
        tareas = [
            TareaMochila("A", cpu=1, ram=1, valor=1),
            TareaMochila("A", cpu=1, ram=1, valor=1),
        ]
        with self.assertRaises(ValueError):
            self.solver.resolver(tareas, 10, 10)
        with self.assertRaises(ValueError):
            self.solver.resolver([TareaMochila("B", 0, 1, 1)], 10, 10)
        with self.assertRaises(ValueError):
            self.solver.resolver([], -1, 10)

    def test_valor_prioridad(self) -> None:
        self.assertGreater(
            valor_desde_prioridad(5, 10), valor_desde_prioridad(1, 10)
        )
        self.assertGreater(
            valor_desde_prioridad(3, 20), valor_desde_prioridad(3, 2)
        )
        with self.assertRaises(ValueError):
            valor_desde_prioridad(0)


if __name__ == "__main__":
    unittest.main()

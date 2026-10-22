"""Pruebas de validación, alias y persistencia de la configuración."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from datastream.configuracion import Configuracion, ErrorConfiguracion


class PruebasConfiguracion(unittest.TestCase):
    def test_valores_por_defecto(self) -> None:
        config = Configuracion()
        self.assertEqual(config.duracion, 120.0)
        self.assertEqual(config.tasa_llegada, 6.0)
        self.assertEqual(config.semilla, 42)
        self.assertEqual(len(config.pesos_cluster), 3)
        self.assertTrue(all(peso > 0 for peso in config.pesos_cluster))

    def test_alias_y_conversion_numerica(self) -> None:
        config = Configuracion.desde_diccionario(
            {
                "duracion_s": 60,
                "tasa_paquetes": 4,
                "buffer": 20,
                "presupuesto_electrico": 30,
                "pesos": [1, 2, 3],
            }
        )
        self.assertEqual(config.duracion, 60.0)
        self.assertEqual(config.tasa_llegada, 4.0)
        self.assertEqual(config.capacidad_buffer, 20)
        self.assertEqual(config.presupuesto_energia, 30.0)
        self.assertEqual(config.pesos_cluster, (1.0, 2.0, 3.0))

    def test_rechaza_valores_fuera_de_rango(self) -> None:
        casos = [
            {"duracion": 5},
            {"tasa_llegada": 0},
            {"capacidad_buffer": 3},
            {"presupuesto_energia": 0.5},
            {"tiempo_reparacion": 200},
            {"pesos_cluster": [1, 0, 1]},
        ]
        for datos in casos:
            with self.subTest(datos=datos), self.assertRaises(ErrorConfiguracion):
                Configuracion.desde_diccionario(datos)

    def test_rechaza_tipos_invalidos(self) -> None:
        for datos in (
            {"duracion": True},
            {"duracion": "120"},
            {"duracion": float("nan")},
            {"semilla": 1.5},
            {"pesos_cluster": [1, "dos", 3]},
        ):
            with self.subTest(datos=datos), self.assertRaises(ErrorConfiguracion):
                Configuracion.desde_diccionario(datos)

    def test_rechaza_claves_desconocidas(self) -> None:
        with self.assertRaisesRegex(ErrorConfiguracion, "desconocidas"):
            Configuracion.desde_diccionario({"duracion": 60, "inventada": 1})

    def test_archivo_json_valido_e_invalido(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "config.json"
            ruta.write_text(
                json.dumps({"duracion": 45, "semilla": 7, "tasa_llegada": 3.5}),
                encoding="utf-8",
            )
            config = Configuracion.cargar_json(ruta)
            self.assertEqual(config.duracion, 45.0)
            self.assertEqual(config.semilla, 7)
            self.assertEqual(config.tasa_llegada, 3.5)

            ruta.write_text("{no es json}", encoding="utf-8")
            with self.assertRaises(ErrorConfiguracion):
                Configuracion.cargar_json(ruta)

    def test_archivo_inexistente(self) -> None:
        with self.assertRaises(ErrorConfiguracion):
            Configuracion.cargar_json("/ruta/que/no/existe.json")

    def test_serializacion_ida_y_vuelta(self) -> None:
        original = Configuracion(duracion=80, semilla=99, pesos_cluster=(1, 2, 3))
        copia = Configuracion.desde_diccionario(original.a_diccionario())
        self.assertEqual(copia.a_diccionario(), original.a_diccionario())


if __name__ == "__main__":
    unittest.main()

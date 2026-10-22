"""Pruebas del cliente de IA y del análisis local de respaldo."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from datastream.ia import ClienteIA, ErrorIA, generar_resumen_local


class RespuestaFalsa:
    status = 200

    def __init__(self, cuerpo: dict) -> None:
        self.cuerpo = cuerpo

    def __enter__(self) -> "RespuestaFalsa":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self.cuerpo).encode("utf-8")


class PruebasIA(unittest.TestCase):
    def test_sin_clave_no_esta_disponible(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            cliente = ClienteIA(url="https://api.openai.com/v1/chat/completions")
            self.assertFalse(cliente.disponible)
            with self.assertRaises(ErrorIA):
                cliente.generar_analisis({"metricas": {}})

    def test_endpoint_local_no_exige_clave(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            cliente = ClienteIA(
                url="http://127.0.0.1:11434/v1/chat/completions",
                api_key="",
            )
            self.assertTrue(cliente.es_local)
            self.assertTrue(cliente.disponible)

    def test_url_invalida(self) -> None:
        with self.assertRaises(ErrorIA):
            ClienteIA(url="archivo/local", api_key="x")
        with self.assertRaises(ErrorIA):
            ClienteIA(modelo="   ", api_key="x")

    def test_extrae_formatos_de_respuesta(self) -> None:
        self.assertEqual(
            ClienteIA._extraer_texto(
                {"choices": [{"message": {"content": "  diagnóstico  "}}]}
            ),
            "diagnóstico",
        )
        self.assertEqual(
            ClienteIA._extraer_texto(
                {"choices": [{"message": {"content": [{"text": "parte"}]}}]}
            ),
            "parte",
        )
        self.assertEqual(ClienteIA._extraer_texto({"response": "texto"}), "texto")
        self.assertEqual(ClienteIA._extraer_texto({}), "")

    def test_peticion_http_exitosa(self) -> None:
        respuesta = RespuestaFalsa(
            {"choices": [{"message": {"content": "Análisis de prueba"}}]}
        )
        cliente = ClienteIA(
            url="https://api.openai.com/v1/chat/completions",
            api_key="clave-de-prueba",
            modelo="modelo-prueba",
        )
        with patch("urllib.request.urlopen", return_value=respuesta) as urlopen:
            resultado = cliente.generar_analisis(
                {"metricas": {"paquetes_generados": 10}, "energia": {}}
            )
        self.assertTrue(resultado.generado_por_api)
        self.assertEqual(resultado.texto, "Análisis de prueba")
        solicitud = urlopen.call_args.args[0]
        self.assertEqual(
            solicitud.get_header("Authorization"), "Bearer clave-de-prueba"
        )
        self.assertIn(b"modelo-prueba", solicitud.data)

    def test_resumen_local_siempre_disponible(self) -> None:
        instantanea = {
            "metricas": {
                "paquetes_generados": 20,
                "paquetes_procesados": 15,
                "paquetes_descartados": 5,
                "tasa_aceptacion": 0.75,
                "latencia_promedio": 1.2,
                "cambios_ruta_congestion": 2,
                "fallas_totales": 1,
                "emergencias": 0,
            },
            "energia": {
                "nombres_cluster": ["Cómputo", "Almacenamiento", "Red"],
                "asignacion": [10.0, 8.0, 18.0],
                "presupuesto": 36.0,
                "multiplicador_lagrange": 0.2,
                "ganancia_porcentual": 4.0,
            },
            "modelo_termico": {
                "punto_emergencia": {"carga": 0.6, "temperatura": 60.64}
            },
            "servidores": [
                {"temperatura": 30.0, "ventilador": 0.5},
                {"temperatura": 32.0, "ventilador": 0.6},
            ],
            "rutas": {
                "S1": {"disponible": True, "camino": ["Internet", "R1", "S1"]}
            },
        }
        resultado = generar_resumen_local(instantanea)
        self.assertIn("Diagnóstico de tráfico", resultado.texto)
        self.assertIn("15", resultado.texto)
        self.assertIn("Lagrange", resultado.texto)
        self.assertIsNone(resultado.error)


if __name__ == "__main__":
    unittest.main()

"""Cliente de API de IA compatible con OpenAI y resumen analítico local.

La clave nunca se escribe en el proyecto ni en los reportes.  Se lee desde
variables de entorno:

    DATASTREAM_IA_API_KEY  (o OPENAI_API_KEY)
    DATASTREAM_IA_URL      (por defecto https://api.openai.com/v1/chat/completions)
    DATASTREAM_IA_MODELO   (por defecto gpt-4o-mini)

También funciona con un servidor local compatible (por ejemplo Ollama en
``http://127.0.0.1:11434/v1/chat/completions``) sin exigir clave.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class ErrorIA(RuntimeError):
    """Error controlado al consultar el proveedor de IA."""


@dataclass(frozen=True)
class ResultadoIA:
    texto: str
    fuente: str
    error: str | None = None

    @property
    def generado_por_api(self) -> bool:
        return self.error is None and self.fuente.startswith("API")


class ClienteIA:
    """Realiza una petición de chat/completions sin dependencias externas."""

    def __init__(
        self,
        url: str | None = None,
        modelo: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.url = (
            url
            or os.getenv("DATASTREAM_IA_URL")
            or "https://api.openai.com/v1/chat/completions"
        ).strip()
        self.modelo = (
            modelo
            or os.getenv("DATASTREAM_IA_MODELO")
            or "gpt-4o-mini"
        ).strip()
        self.api_key = (
            api_key
            or os.getenv("DATASTREAM_IA_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        ).strip()
        self.timeout = timeout
        self._validar_configuracion()

    def _validar_configuracion(self) -> None:
        partes = urllib.parse.urlparse(self.url)
        if partes.scheme not in {"http", "https"} or not partes.netloc:
            raise ErrorIA("La URL de IA debe ser http(s) y contener un host válido.")
        if not self.modelo:
            raise ErrorIA("El modelo de IA no puede estar vacío.")
        if self.timeout <= 0:
            raise ErrorIA("El tiempo de espera de la IA debe ser positivo.")

    @property
    def es_local(self) -> bool:
        host = urllib.parse.urlparse(self.url).hostname or ""
        return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local")

    @property
    def disponible(self) -> bool:
        return bool(self.api_key) or self.es_local

    def generar_analisis(self, instantanea: dict[str, Any]) -> ResultadoIA:
        """Solicita un análisis ejecutivo y devuelve el texto recibido."""
        if not self.disponible:
            raise ErrorIA(
                "API de IA no configurada. Defina DATASTREAM_IA_API_KEY o use un "
                "endpoint local; se generará el análisis determinista de respaldo."
            )
        prompt = self._construir_prompt(instantanea)
        cuerpo = {
            "model": self.modelo,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Eres un ingeniero senior de confiabilidad y eficiencia "
                        "energética de centros de datos. Analizas resultados de una "
                        "simulación de eventos discretos. Responde en español, con "
                        "rigor técnico, sin inventar cifras que no aparezcan en los "
                        "datos y sin usar tablas Markdown."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 950,
        }
        datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
        encabezados = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "DataStream-Simulator/1.0",
        }
        if self.api_key:
            encabezados["Authorization"] = f"Bearer {self.api_key}"
        solicitud = urllib.request.Request(
            self.url, data=datos, headers=encabezados, method="POST"
        )

        try:
            with urllib.request.urlopen(solicitud, timeout=self.timeout) as respuesta:
                estado = getattr(respuesta, "status", 200)
                crudo = respuesta.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detalle = ""
            try:
                detalle = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            raise ErrorIA(
                f"El proveedor de IA respondió HTTP {exc.code}."
                + (f" Detalle: {detalle}" if detalle else "")
            ) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise ErrorIA(f"No se pudo conectar con la API de IA: {exc}") from exc
        except OSError as exc:
            raise ErrorIA(f"Error de red al consultar la IA: {exc}") from exc

        if estado < 200 or estado >= 300:
            raise ErrorIA(f"El proveedor de IA respondió HTTP {estado}.")
        try:
            respuesta_json = json.loads(crudo)
        except json.JSONDecodeError as exc:
            raise ErrorIA("La API de IA devolvió una respuesta que no es JSON.") from exc

        texto = self._extraer_texto(respuesta_json)
        if not texto:
            raise ErrorIA("La API de IA respondió sin contenido de texto.")
        return ResultadoIA(texto=texto, fuente=f"API {self.modelo}", error=None)

    @staticmethod
    def _extraer_texto(respuesta: dict[str, Any]) -> str:
        # Formato OpenAI chat/completions.
        try:
            contenido = respuesta["choices"][0]["message"]["content"]
            if isinstance(contenido, list):
                return "\n".join(
                    parte.get("text", "")
                    for parte in contenido
                    if isinstance(parte, dict)
                ).strip()
            if isinstance(contenido, str):
                return contenido.strip()
        except (KeyError, IndexError, TypeError):
            pass
        # Formatos alternativos de APIs compatibles.
        for clave in ("output_text", "response", "text"):
            valor = respuesta.get(clave)
            if isinstance(valor, str) and valor.strip():
                return valor.strip()
        return ""

    def _construir_prompt(self, instantanea: dict[str, Any]) -> str:
        metricas = instantanea.get("metricas", {})
        energia = instantanea.get("energia", {})
        termico = instantanea.get("modelo_termico", {})
        servidores = instantanea.get("servidores", [])
        rutas = instantanea.get("rutas", {})
        historial = instantanea.get("historial", [])[-6:]
        resumen = {
            "configuracion": instantanea.get("configuracion", {}),
            "metricas": {
                clave: valor
                for clave, valor in metricas.items()
                if clave not in {"teoria_colas"}
            },
            "teoria_colas": metricas.get("teoria_colas", {}),
            "energia_lagrange": {
                "nombres_cluster": energia.get("nombres_cluster"),
                "asignacion": energia.get("asignacion"),
                "presupuesto": energia.get("presupuesto"),
                "multiplicador": energia.get("multiplicador_lagrange"),
                "rendimiento_total": energia.get("rendimiento_total"),
                "ganancia_vs_reparto_igual": energia.get("ganancia_porcentual"),
                "demanda": energia.get("demanda"),
            },
            "modelo_termico": termico,
            "servidores": [
                {
                    clave: servidor.get(clave)
                    for clave in (
                        "id",
                        "cluster",
                        "operativo",
                        "carga",
                        "temperatura",
                        "ventilador",
                        "potencia_refrigeracion",
                        "candidatos",
                        "procesando",
                        "aceptados",
                        "procesados",
                        "perdidos",
                        "fallas",
                    )
                }
                for servidor in servidores
            ],
            "rutas_actuales": rutas,
            "historial_reciente": historial,
        }
        return (
            "Analiza la siguiente salida del simulador DataStream Networks. "
            "Incluye: (1) diagnóstico de latencia y congestión, (2) riesgo térmico "
            "y desempeño del gradiente, (3) evaluación de la asignación de energía "
            "por Lagrange y del multiplicador, (4) tres o cuatro recomendaciones "
            "accionables y (5) conclusión de una línea. Sé específico con las cifras. "
            "No repitas el JSON.\n\n"
            + json.dumps(resumen, ensure_ascii=False, indent=2, default=str)
        )


def generar_resumen_local(instantanea: dict[str, Any]) -> ResultadoIA:
    """Análisis determinista de respaldo cuando no hay API de IA disponible.

    No reemplaza a un modelo generativo, pero garantiza que el PDF siempre
    contenga un diagnóstico técnico coherente y reproducible.
    """
    metricas = instantanea.get("metricas", {})
    energia = instantanea.get("energia", {})
    termico = instantanea.get("modelo_termico", {})
    servidores = instantanea.get("servidores", [])
    rutas = instantanea.get("rutas", {})
    generados = metricas.get("paquetes_generados", 0)
    procesados = metricas.get("paquetes_procesados", 0)
    descartados = metricas.get("paquetes_descartados", 0)
    aceptacion = 100.0 * metricas.get("tasa_aceptacion", 0.0)
    latencia = metricas.get("latencia_promedio", 0.0)
    cambios = metricas.get("cambios_ruta_congestion", 0)
    fallas = metricas.get("fallas_totales", 0)
    emergencias = metricas.get("emergencias", 0)
    asignacion = energia.get("asignacion", [0, 0, 0])
    nombres = energia.get("nombres_cluster", ["Cómputo", "Almacenamiento", "Red"])
    presupuesto = energia.get("presupuesto", 0.0)
    lam = energia.get("multiplicador_lagrange", 0.0)
    ganancia = energia.get("ganancia_porcentual", 0.0)
    temps = [float(s.get("temperatura", 0.0)) for s in servidores]
    fans = [float(s.get("ventilador", 0.0)) for s in servidores]
    temp_media = sum(temps) / len(temps) if temps else 0.0
    fan_medio = sum(fans) / len(fans) if fans else 0.0
    punto = termico.get("punto_emergencia") or {}
    rutas_texto = "; ".join(
        f"{destino}: {' → '.join(info.get('camino', []))}"
        for destino, info in sorted(rutas.items())
        if info.get("disponible")
    )
    asignacion_texto = ", ".join(
        f"{nombre} {valor:.1f} kW"
        for nombre, valor in zip(nombres, asignacion)
    )

    if generados:
        if aceptacion >= 85:
            juicio = "La política de admisión fue efectiva y preservó la mayor parte del tráfico."
        elif aceptacion >= 60:
            juicio = "La red operó con pérdidas moderadas; conviene ampliar capacidad o ajustar umbrales."
        else:
            juicio = "La pérdida de paquetes es alta y exige revisar capacidad, fallas y tiempos de espera."
    else:
        juicio = "No se generaron paquetes en la ventana simulada."

    return ResultadoIA(
        texto=(
            "Diagnóstico de tráfico. Se generaron "
            f"{generados} paquetes, se procesaron {procesados} y se descartaron "
            f"{descartados}. La tasa de aceptación fue {aceptacion:.1f} % y la "
            f"latencia media de extremo a extremo {latencia:.2f} s. {juicio} "
            f"La red registró {cambios} cambios de ruta por congestión y {fallas} "
            "eventos de falla/recuperación de nodos, por lo que la topología "
            "redundante y el recálculo dinámico resultan determinantes.\n\n"
            "Riesgo térmico y gradiente. La temperatura media de los servidores fue "
            f"{temp_media:.1f} °C con una velocidad media de ventilador de "
            f"{fan_medio:.2f}. El punto crítico del modelo cúbico se ubica en carga "
            f"{float(punto.get('carga', 0.0)):.2f} con "
            f"{float(punto.get('temperatura', 0.0)):.1f} °C. Se registraron "
            f"{emergencias} activaciones de enfriamiento de emergencia. El descenso "
            "de gradiente mantuvo el ventilador dentro de sus límites operativos.\n\n"
            "Energía y Lagrange. Con un presupuesto de "
            f"{presupuesto:.1f} kW, la asignación óptima fue {asignacion_texto}. "
            f"El multiplicador λ = {lam:.4f} representa el valor marginal del "
            f"presupuesto, y la ganancia frente al reparto igualitario fue "
            f"{ganancia:.2f} %. Las rutas vigentes son: {rutas_texto}.\n\n"
            "Recomendaciones. (1) Priorizar la ampliación del enlace y servidor con "
            "mayor ocupación sostenida. (2) Mantener el recálculo de rutas con "
            "histéresis para evitar cambios innecesarios. (3) Ajustar el presupuesto "
            "eléctrico si λ crece por encima del costo marginal de capacidad. "
            "(4) Programar mantenimiento preventivo en nodos con mayor tasa de falla.\n\n"
            "Conclusión. El lazo tráfico-colas-energía responde de forma estable; "
            "las decisiones de ruta, admisión y refrigeración deben evaluarse "
            "conjuntamente y no como optimizaciones aisladas."
        ),
        fuente=f"Análisis determinista local ({datetime.now():%Y-%m-%d %H:%M})",
        error=None,
    )

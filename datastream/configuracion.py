"""Configuración y validación central del simulador DataStream Networks.

Todos los parámetros que pueden modificar el comportamiento de la simulación
se concentran en este módulo.  La validación se hace antes de crear objetos
SimPy, de modo que un dato incorrecto nunca deja una simulación a medio
construir.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, ClassVar


class ErrorConfiguracion(ValueError):
    """Error legible producido por un parámetro de configuración inválido."""


def _numero(valor: Any, nombre: str) -> float:
    """Convierte un número verificando que no sea booleano, NaN o infinito."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ErrorConfiguracion(f"'{nombre}' debe ser numérico (recibido: {valor!r}).")
    numero = float(valor)
    if not math.isfinite(numero):
        raise ErrorConfiguracion(f"'{nombre}' debe ser un número finito.")
    return numero


def _entero(valor: Any, nombre: str) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise ErrorConfiguracion(f"'{nombre}' debe ser un entero (recibido: {valor!r}).")
    return valor


@dataclass
class Configuracion:
    """Parámetros validados de una ejecución del centro de datos.

    Los valores por defecto forman un escenario de demostración estable y
    suficientemente activo para observar congestión, cambios de ruta, fallas,
    control térmico y reasignación de energía en pocos minutos.
    """

    nombre_equipo: str = "Equipo DataStream"
    duracion: float = 120.0
    semilla: int = 42
    tasa_llegada: float = 6.0
    capacidad_buffer: int = 36
    intervalo_planificacion: float = 0.65
    intervalo_rutas: float = 1.25
    factor_congestion: float = 2.8
    mtbf_router: float = 170.0
    mtbf_servidor: float = 135.0
    tiempo_reparacion: float = 12.0
    presupuesto_energia: float = 36.0
    pesos_cluster: tuple[float, float, float] = field(
        default_factory=lambda: (1.25, 1.00, 1.10)
    )

    # Alias reconocidos al importar JSON (facilita configuraciones externas).
    ALIAS: ClassVar[dict[str, str]] = {
        "duracion_s": "duracion",
        "tasa_paquetes": "tasa_llegada",
        "buffer": "capacidad_buffer",
        "presupuesto_electrico": "presupuesto_energia",
        "pesos": "pesos_cluster",
    }

    def __post_init__(self) -> None:
        self.validar()

    # ------------------------------------------------------------------
    # Validación
    # ------------------------------------------------------------------
    def validar(self) -> None:
        """Comprueba rangos y tipos; lanza ``ErrorConfiguracion`` si falla."""
        errores: list[str] = []

        if not isinstance(self.nombre_equipo, str) or not self.nombre_equipo.strip():
            errores.append("'nombre_equipo' no puede estar vacío.")
        elif len(self.nombre_equipo) > 80:
            errores.append("'nombre_equipo' no puede superar 80 caracteres.")

        reglas: dict[str, tuple[float, float]] = {
            "duracion": (10.0, 7200.0),
            "tasa_llegada": (0.1, 100.0),
            "intervalo_planificacion": (0.1, 10.0),
            "intervalo_rutas": (0.1, 10.0),
            "factor_congestion": (0.0, 20.0),
            "mtbf_router": (10.0, 10000.0),
            "mtbf_servidor": (10.0, 10000.0),
            "tiempo_reparacion": (0.5, 1000.0),
            "presupuesto_energia": (1.0, 10000.0),
        }
        for nombre, (minimo, maximo) in reglas.items():
            try:
                valor = _numero(getattr(self, nombre), nombre)
            except ErrorConfiguracion as exc:
                errores.append(str(exc))
                continue
            if not minimo <= valor <= maximo:
                errores.append(
                    f"'{nombre}' debe estar entre {minimo:g} y {maximo:g}; "
                    f"recibido {valor:g}."
                )
            setattr(self, nombre, valor)

        try:
            self.semilla = _entero(self.semilla, "semilla")
            if not 0 <= self.semilla <= 2**32 - 1:
                errores.append("'semilla' debe estar entre 0 y 4294967295.")
        except ErrorConfiguracion as exc:
            errores.append(str(exc))

        try:
            self.capacidad_buffer = _entero(self.capacidad_buffer, "capacidad_buffer")
            if not 4 <= self.capacidad_buffer <= 500:
                errores.append("'capacidad_buffer' debe estar entre 4 y 500.")
        except ErrorConfiguracion as exc:
            errores.append(str(exc))

        pesos = self.pesos_cluster
        if not isinstance(pesos, (list, tuple)):
            errores.append("'pesos_cluster' debe ser una lista de tres números.")
        else:
            pesos_convertidos: list[float] = []
            for indice, peso in enumerate(pesos):
                try:
                    valor = _numero(peso, f"pesos_cluster[{indice}]")
                except ErrorConfiguracion as exc:
                    errores.append(str(exc))
                    continue
                if valor <= 0:
                    errores.append("Cada peso de clúster debe ser mayor que cero.")
                pesos_convertidos.append(valor)
            if len(pesos_convertidos) != 3:
                errores.append("'pesos_cluster' debe contener exactamente tres pesos.")
            self.pesos_cluster = tuple(pesos_convertidos)  # type: ignore[assignment]

        try:
            if self.intervalo_rutas > self.duracion:
                errores.append("'intervalo_rutas' no puede superar la duración total.")
            if self.tiempo_reparacion >= max(self.mtbf_router, self.mtbf_servidor):
                errores.append(
                    "'tiempo_reparacion' debe ser menor que los MTBF de los nodos."
                )
        except TypeError:
            # Los errores de tipo ya se registraron arriba.
            pass

        if errores:
            raise ErrorConfiguracion("\n".join(f"  - {error}" for error in errores))

    # ------------------------------------------------------------------
    # Carga desde JSON
    # ------------------------------------------------------------------
    @classmethod
    def desde_diccionario(cls, datos: dict[str, Any]) -> "Configuracion":
        """Crea una configuración desde un diccionario, validando claves."""
        if not isinstance(datos, dict):
            raise ErrorConfiguracion("La configuración raíz debe ser un objeto JSON.")

        permitidos = {campo.name for campo in fields(cls)}
        normalizados: dict[str, Any] = {}
        desconocidos: list[str] = []
        for clave, valor in datos.items():
            clave_normalizada = cls.ALIAS.get(clave, clave)
            if clave_normalizada not in permitidos:
                desconocidos.append(clave)
            else:
                normalizados[clave_normalizada] = valor
        if desconocidos:
            raise ErrorConfiguracion(
                "Claves desconocidas en la configuración: " + ", ".join(sorted(desconocidos))
            )
        try:
            return cls(**normalizados)
        except TypeError as exc:
            raise ErrorConfiguracion(f"Configuración inválida: {exc}") from exc

    @classmethod
    def cargar_json(cls, ruta: str | Path) -> "Configuracion":
        """Lee y valida un archivo JSON de configuración."""
        camino = Path(ruta)
        if not camino.exists():
            raise ErrorConfiguracion(f"No existe el archivo de configuración: {camino}")
        if not camino.is_file():
            raise ErrorConfiguracion(f"La ruta no es un archivo: {camino}")
        try:
            with camino.open("r", encoding="utf-8") as archivo:
                datos = json.load(archivo)
        except json.JSONDecodeError as exc:
            raise ErrorConfiguracion(
                f"JSON inválido en {camino.name}, línea {exc.lineno}: {exc.msg}"
            ) from exc
        except OSError as exc:
            raise ErrorConfiguracion(f"No se pudo leer {camino}: {exc}") from exc
        return cls.desde_diccionario(datos)

    def a_diccionario(self) -> dict[str, Any]:
        """Devuelve una copia serializable de la configuración."""
        datos = asdict(self)
        datos["pesos_cluster"] = list(self.pesos_cluster)
        return datos

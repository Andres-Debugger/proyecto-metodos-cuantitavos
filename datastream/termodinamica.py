"""Modelos térmicos y control de refrigeración por descenso de gradiente.

El módulo contiene tres piezas independientes y comprobables:

1. ``ModeloTemperatura``: polinomio cúbico que relaciona carga con temperatura.
   Sus derivadas permiten clasificar máximos, mínimos y puntos de inflexión.
2. ``ModeloRefrigeracion``: consumo no lineal de ventiladores y penalización
   térmica; el gradiente se calcula de forma analítica.
3. ``ControladorRefrigeracion``: estado dinámico de un ventilador que aplica
   gradiente proyectado en cada paso de la simulación.

El modelo cúbico es una aproximación académica.  Después del máximo térmico
se representa el efecto de *throttling* de la CPU, por eso la temperatura
disminuye antes de volver a subir en la zona de sobrecarga.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class PuntoCritico:
    carga: float
    temperatura: float
    tipo: Literal["máximo", "mínimo", "inflexión"]
    derivada_segunda: float


class ModeloTemperatura:
    """T(c) = a*c^3 + b*c^2 + d*c + e sobre el rango de operación [0, 1.4]."""

    def __init__(
        self,
        a: float = 220.0 / 3.0,
        b: float = -187.0,
        d: float = 145.2,
        e: float = 25.0,
        carga_minima: float = 0.0,
        carga_maxima: float = 1.4,
        temperatura_emergencia: float = 60.0,
    ) -> None:
        if carga_maxima <= carga_minima:
            raise ValueError("El rango de carga es inválido.")
        if temperatura_emergencia <= e:
            raise ValueError("La temperatura de emergencia debe superar la ambiental.")
        self.a = a
        self.b = b
        self.d = d
        self.e = e
        self.carga_minima = carga_minima
        self.carga_maxima = carga_maxima
        self.temperatura_emergencia = temperatura_emergencia

    # ------------------------------------------------------------------
    # Función y derivadas
    # ------------------------------------------------------------------
    def evaluar(self, carga: float) -> float:
        carga = self._validar_carga(carga)
        return self.a * carga**3 + self.b * carga**2 + self.d * carga + self.e

    def derivada(self, carga: float) -> float:
        carga = self._validar_carga(carga)
        return 3.0 * self.a * carga**2 + 2.0 * self.b * carga + self.d

    def derivada_segunda(self, carga: float) -> float:
        carga = self._validar_carga(carga)
        return 6.0 * self.a * carga + 2.0 * self.b

    def _validar_carga(self, carga: float) -> float:
        if isinstance(carga, bool) or not isinstance(carga, (int, float)):
            raise TypeError("La carga debe ser numérica.")
        carga = float(carga)
        if not math.isfinite(carga) or carga < self.carga_minima or carga > self.carga_maxima:
            raise ValueError(
                f"La carga debe estar en [{self.carga_minima:g}, {self.carga_maxima:g}]."
            )
        return carga

    # ------------------------------------------------------------------
    # Puntos críticos
    # ------------------------------------------------------------------
    def puntos_estacionarios(self) -> list[PuntoCritico]:
        """Resuelve T'(c)=0 y clasifica con el criterio de la 2.ª derivada."""
        # T'(c) = A*c^2 + B*c + C
        A = 3.0 * self.a
        B = 2.0 * self.b
        C = self.d
        if abs(A) < 1e-12:
            if abs(B) < 1e-12:
                return []
            raices = [-C / B]
        else:
            discriminante = B * B - 4.0 * A * C
            if discriminante < -1e-12:
                return []
            raiz = math.sqrt(max(0.0, discriminante))
            raices = [(-B - raiz) / (2.0 * A), (-B + raiz) / (2.0 * A)]

        puntos: list[PuntoCritico] = []
        for carga in sorted(set(round(x, 12) for x in raices)):
            if not self.carga_minima <= carga <= self.carga_maxima:
                continue
            segunda = self.derivada_segunda(carga)
            if segunda < -1e-9:
                tipo: Literal["máximo", "mínimo", "inflexión"] = "máximo"
            elif segunda > 1e-9:
                tipo = "mínimo"
            else:
                tipo = "inflexión"
            puntos.append(PuntoCritico(carga, self.evaluar(carga), tipo, segunda))
        return puntos

    def puntos_inflexion(self) -> list[PuntoCritico]:
        """Resuelve T''(c)=0 dentro del dominio de operación."""
        if abs(6.0 * self.a) < 1e-12:
            return []
        carga = -2.0 * self.b / (6.0 * self.a)
        if not self.carga_minima <= carga <= self.carga_maxima:
            return []
        return [
            PuntoCritico(
                carga=carga,
                temperatura=self.evaluar(carga),
                tipo="inflexión",
                derivada_segunda=self.derivada_segunda(carga),
            )
        ]

    def analisis_completo(self) -> list[PuntoCritico]:
        puntos = self.puntos_estacionarios() + self.puntos_inflexion()
        # Elimina duplicados si una segunda derivada es prácticamente cero.
        unicos: list[PuntoCritico] = []
        for punto in sorted(puntos, key=lambda p: p.carga):
            if any(abs(punto.carga - previo.carga) < 1e-9 for previo in unicos):
                continue
            unicos.append(punto)
        return unicos

    def punto_critico_emergencia(self) -> PuntoCritico | None:
        """Devuelve el máximo local que dispara refrigeración de emergencia.

        Si el polinomio no tuviera un máximo dentro del rango, se devuelve el
        punto de mayor temperatura de la frontera (caso de respaldo).
        """
        maximos = [p for p in self.puntos_estacionarios() if p.tipo == "máximo"]
        if maximos:
            return max(maximos, key=lambda punto: punto.temperatura)

        frontera = [
            PuntoCritico(self.carga_minima, self.evaluar(self.carga_minima), "máximo", 0.0),
            PuntoCritico(self.carga_maxima, self.evaluar(self.carga_maxima), "máximo", 0.0),
        ]
        return max(frontera, key=lambda punto: punto.temperatura)

    def requiere_emergencia(self, carga: float, temperatura: float | None = None) -> bool:
        temperatura = self.evaluar(carga) if temperatura is None else temperatura
        return temperatura >= self.temperatura_emergencia

    def instantanea(self) -> dict:
        punto = self.punto_critico_emergencia()
        return {
            "coeficientes": {"a": self.a, "b": self.b, "c": self.d, "d": self.e},
            "puntos": [
                {
                    "carga": punto_critico.carga,
                    "temperatura": punto_critico.temperatura,
                    "tipo": punto_critico.tipo,
                    "derivada_segunda": punto_critico.derivada_segunda,
                }
                for punto_critico in self.analisis_completo()
            ],
            "punto_emergencia": (
                {
                    "carga": punto.carga,
                    "temperatura": punto.temperatura,
                    "tipo": punto.tipo,
                }
                if punto
                else None
            ),
            "temperatura_emergencia": self.temperatura_emergencia,
        }


class ModeloRefrigeracion:
    """Consumo de ventilador + penalización térmica en función de la velocidad.

    ``f`` es la velocidad normalizada del ventilador en [0.1, 1.0].
    ``carga`` es la carga normalizada del servidor en [0, 1.4].

    T(f, L) = T_amb + (T_base(L) - T_amb) / (1 + k*f)
    P(f, L) = P0 + cv*f^3 + c1*max(0, T-Tobj)^2
              + c2*max(0, T-Temerg)^2
    """

    def __init__(
        self,
        modelo_temperatura: ModeloTemperatura | None = None,
        temp_ambiente: float = 22.0,
        temp_objetivo: float = 27.0,
        temp_emergencia: float = 48.0,
        factor_enfriamiento: float = 1.8,
        potencia_base: float = 0.18,
        coef_ventilador: float = 1.3,
        coef_termico: float = 0.045,
        coef_emergencia: float = 2.0,
        fan_minimo: float = 0.1,
        fan_maximo: float = 1.0,
    ) -> None:
        if not 0 <= fan_minimo < fan_maximo <= 1.0:
            raise ValueError("Los límites del ventilador deben cumplir 0 <= min < max <= 1.")
        self.modelo_temperatura = modelo_temperatura or ModeloTemperatura()
        self.temp_ambiente = temp_ambiente
        self.temp_objetivo = temp_objetivo
        self.temp_emergencia = temp_emergencia
        self.factor_enfriamiento = factor_enfriamiento
        self.potencia_base = potencia_base
        self.coef_ventilador = coef_ventilador
        self.coef_termico = coef_termico
        self.coef_emergencia = coef_emergencia
        self.fan_minimo = fan_minimo
        self.fan_maximo = fan_maximo

    def temperatura(self, ventilador: float, carga: float) -> float:
        if not self.fan_minimo <= ventilador <= self.fan_maximo:
            raise ValueError("La velocidad del ventilador está fuera de rango.")
        base = self.modelo_temperatura.evaluar(carga)
        delta = max(0.0, base - self.temp_ambiente)
        return self.temp_ambiente + delta / (1.0 + self.factor_enfriamiento * ventilador)

    def potencia(self, ventilador: float, carga: float) -> float:
        temp = self.temperatura(ventilador, carga)
        termico = self.coef_termico * max(0.0, temp - self.temp_objetivo) ** 2
        emergencia = self.coef_emergencia * max(0.0, temp - self.temp_emergencia) ** 2
        return (
            self.potencia_base
            + self.coef_ventilador * ventilador**3
            + termico
            + emergencia
        )

    def gradiente(self, ventilador: float, carga: float) -> float:
        """Derivada analítica dP/df, incluyendo las funciones max(0, .)."""
        base = self.modelo_temperatura.evaluar(carga)
        delta = max(0.0, base - self.temp_ambiente)
        denominador = 1.0 + self.factor_enfriamiento * ventilador
        temp = self.temp_ambiente + delta / denominador
        d_temp = -delta * self.factor_enfriamiento / (denominador**2)

        gradiente = 3.0 * self.coef_ventilador * ventilador**2
        if temp > self.temp_objetivo:
            gradiente += 2.0 * self.coef_termico * (temp - self.temp_objetivo) * d_temp
        if temp > self.temp_emergencia:
            gradiente += 2.0 * self.coef_emergencia * (temp - self.temp_emergencia) * d_temp
        return gradiente

    def minimizar(
        self,
        carga: float,
        ventilador_inicial: float = 0.5,
        tasa_aprendizaje: float = 0.18,
        iteraciones: int = 8,
        tolerancia: float = 1e-4,
    ) -> tuple[float, float, float, list[float]]:
        """Gradiente proyectado con retroceso simple de paso.

        Se prueban pasos cada vez más pequeños cuando el gradiente no reduce
        la función objetivo.  Esto evita divergencias y mantiene ``f`` dentro
        de [fan_minimo, fan_maximo].
        """
        if not self.modelo_temperatura.carga_minima <= carga <= self.modelo_temperatura.carga_maxima:
            raise ValueError("La carga está fuera del dominio del modelo térmico.")
        f = min(self.fan_maximo, max(self.fan_minimo, float(ventilador_inicial)))
        historial: list[float] = [f]

        for _ in range(iteraciones):
            gradiente = self.gradiente(f, carga)
            if abs(gradiente) < tolerancia:
                break
            actual = self.potencia(f, carga)
            paso = tasa_aprendizaje
            nuevo = f
            for _intento in range(8):
                candidato = min(self.fan_maximo, max(self.fan_minimo, f - paso * gradiente))
                if self.potencia(candidato, carga) <= actual + 1e-12:
                    nuevo = candidato
                    break
                paso *= 0.5
            if abs(nuevo - f) < tolerancia:
                f = nuevo
                historial.append(f)
                break
            f = nuevo
            historial.append(f)

        return f, self.temperatura(f, carga), self.potencia(f, carga), historial


@dataclass
class ControladorRefrigeracion:
    """Estado de un ventilador controlado en cada intervalo de simulación."""

    id_servidor: str
    modelo: ModeloRefrigeracion
    ventilador: float = 0.45
    temperatura: float = 25.0
    potencia: float = 0.0
    gradiente: float = 0.0
    emergencia: bool = False
    iteraciones: int = 0
    historial: list[dict] = field(default_factory=list)

    def actualizar(self, carga: float, tiempo: float) -> dict:
        f, temp, potencia, rastro = self.modelo.minimizar(
            carga=min(self.modelo.modelo_temperatura.carga_maxima, max(0.0, carga)),
            ventilador_inicial=self.ventilador,
            tasa_aprendizaje=0.20,
            iteraciones=5,
        )
        self.gradiente = self.modelo.gradiente(self.ventilador, carga)
        self.ventilador = f
        # Suavizado físico de primer orden: el ventilador y la temperatura no
        # cambian instantáneamente.
        self.temperatura += 0.45 * (temp - self.temperatura)
        self.potencia = potencia
        self.emergencia = self.temperatura >= self.modelo.temp_emergencia
        self.iteraciones = len(rastro)
        estado = {
            "tiempo": tiempo,
            "servidor": self.id_servidor,
            "ventilador": self.ventilador,
            "temperatura": self.temperatura,
            "potencia": self.potencia,
            "gradiente": self.gradiente,
            "emergencia": self.emergencia,
            "iteraciones": self.iteraciones,
        }
        self.historial.append(estado)
        if len(self.historial) > 600:
            self.historial = self.historial[-600:]
        return estado

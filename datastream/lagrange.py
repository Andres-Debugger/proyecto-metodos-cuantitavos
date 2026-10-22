"""Asignación óptima del presupuesto eléctrico con multiplicadores de Lagrange.

Modelo separables por clúster:

    R_i(e_i) = w_i * sqrt(e_i)          (rendimiento marginal decreciente)
    max  R(e) = Σ R_i(e_i)
    s.a. Σ e_i = E,  e_i > 0

Lagrangiano:
    L = Σ w_i sqrt(e_i) + λ (E - Σ e_i)

Condiciones de primer orden:
    ∂L/∂e_i = w_i / (2 sqrt(e_i)) - λ = 0
    => e_i = w_i² / (4 λ²)

Al imponer Σ e_i = E se obtiene:
    λ = 0.5 * sqrt(Σ w_i² / E)
    e_i = E * w_i² / Σ w_j²
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ResultadoEnergia:
    asignacion: tuple[float, ...]
    rendimiento_total: float
    multiplicador_lagrange: float
    asignacion_igualitaria: tuple[float, ...]
    rendimiento_igualitario: float
    ganancia_porcentual: float
    marginales: tuple[float, ...]
    pesos: tuple[float, ...]
    presupuesto: float

    def como_diccionario(self) -> dict:
        return {
            "asignacion": list(self.asignacion),
            "rendimiento_total": self.rendimiento_total,
            "multiplicador_lagrange": self.multiplicador_lagrange,
            "asignacion_igualitaria": list(self.asignacion_igualitaria),
            "rendimiento_igualitario": self.rendimiento_igualitario,
            "ganancia_porcentual": self.ganancia_porcentual,
            "marginales": list(self.marginales),
            "pesos": list(self.pesos),
            "presupuesto": self.presupuesto,
        }


class OptimizadorEnergia:
    """Resuelve la asignación de energía de forma analítica (KKT)."""

    NOMBRES_CLUSTER: tuple[str, str, str] = ("Cómputo", "Almacenamiento", "Red")

    def __init__(self, nombres_cluster: tuple[str, ...] = NOMBRES_CLUSTER) -> None:
        if len(nombres_cluster) < 2:
            raise ValueError("Se requieren al menos dos clústeres.")
        if len(set(nombres_cluster)) != len(nombres_cluster):
            raise ValueError("Los nombres de clúster deben ser únicos.")
        self.nombres_cluster = nombres_cluster

    @staticmethod
    def rendimiento(pesos: tuple[float, ...], energia: tuple[float, ...]) -> float:
        if len(pesos) != len(energia):
            raise ValueError("Pesos y energía deben tener la misma dimensión.")
        return sum(peso * math.sqrt(max(0.0, e)) for peso, e in zip(pesos, energia))

    def resolver(
        self, pesos: tuple[float, ...] | list[float], presupuesto: float
    ) -> ResultadoEnergia:
        """Calcula la distribución óptima y comprueba las condiciones KKT."""
        if not isinstance(pesos, (tuple, list)) or len(pesos) != len(self.nombres_cluster):
            raise ValueError(
                f"Se esperaban {len(self.nombres_cluster)} pesos de clúster."
            )
        pesos_convertidos: list[float] = []
        for peso in pesos:
            if isinstance(peso, bool) or not isinstance(peso, (int, float)):
                raise TypeError("Los pesos deben ser numéricos.")
            peso = float(peso)
            if not math.isfinite(peso) or peso <= 0:
                raise ValueError("Todos los pesos deben ser positivos y finitos.")
            pesos_convertidos.append(peso)

        if isinstance(presupuesto, bool) or not isinstance(presupuesto, (int, float)):
            raise TypeError("El presupuesto debe ser numérico.")
        presupuesto = float(presupuesto)
        if not math.isfinite(presupuesto) or presupuesto <= 0:
            raise ValueError("El presupuesto de energía debe ser positivo y finito.")

        suma_cuadrados = sum(peso**2 for peso in pesos_convertidos)
        asignacion = tuple(
            presupuesto * peso**2 / suma_cuadrados for peso in pesos_convertidos
        )
        multiplicador = 0.5 * math.sqrt(suma_cuadrados / presupuesto)
        rendimiento = self.rendimiento(tuple(pesos_convertidos), asignacion)
        marginales = tuple(
            peso / (2.0 * math.sqrt(energia))
            for peso, energia in zip(pesos_convertidos, asignacion)
        )

        # Referencia justa: repartir el mismo presupuesto en partes iguales.
        igualitaria = tuple(presupuesto / len(pesos_convertidos) for _ in pesos_convertidos)
        rendimiento_igual = self.rendimiento(tuple(pesos_convertidos), igualitaria)
        ganancia = (
            100.0 * (rendimiento - rendimiento_igual) / rendimiento_igual
            if rendimiento_igual > 0
            else 0.0
        )

        # Verificación numérica de la restricción y de las estacionariedad.
        if not math.isclose(sum(asignacion), presupuesto, rel_tol=1e-9, abs_tol=1e-9):
            raise ArithmeticError("La asignación no respeta el presupuesto.")
        if not all(
            math.isclose(marginal, multiplicador, rel_tol=1e-9, abs_tol=1e-9)
            for marginal in marginales
        ):
            raise ArithmeticError("Las condiciones de Lagrange no se satisfacen.")

        return ResultadoEnergia(
            asignacion=asignacion,
            rendimiento_total=rendimiento,
            multiplicador_lagrange=multiplicador,
            asignacion_igualitaria=igualitaria,
            rendimiento_igualitario=rendimiento_igual,
            ganancia_porcentual=ganancia,
            marginales=marginales,
            pesos=tuple(pesos_convertidos),
            presupuesto=presupuesto,
        )

    def pesos_dinamicos(
        self,
        demanda_relativa: tuple[float, ...] | list[float],
        pesos_base: tuple[float, ...] | list[float],
    ) -> tuple[float, ...]:
        """Ajusta los pesos según la demanda observada de cada clúster.

        La demanda relativa se normaliza con su media.  El resultado queda
        acotado para evitar que un pico aislado absorba todo el presupuesto.
        """
        if len(demanda_relativa) != len(self.nombres_cluster):
            raise ValueError("La demanda debe tener una entrada por clúster.")
        if len(pesos_base) != len(self.nombres_cluster):
            raise ValueError("Los pesos base deben tener una entrada por clúster.")
        demandas = [max(0.05, float(valor)) for valor in demanda_relativa]
        media = sum(demandas) / len(demandas)
        normalizadas = [min(2.0, max(0.35, valor / media)) for valor in demandas]
        return tuple(
            max(0.05, float(base)) * (0.55 + 0.45 * normalizada)
            for base, normalizada in zip(pesos_base, normalizadas)
        )

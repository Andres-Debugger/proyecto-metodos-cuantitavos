"""Problema de la mochila binaria bidimensional (CPU y RAM).

Cada paquete es un elemento indivisible con demanda de CPU, demanda de RAM y
un valor derivado de su prioridad.  El planificador de cada servidor resuelve
la mochila para escoger el subconjunto de tareas con mayor valor total que
quepa en la capacidad libre del servidor.

Programación dinámica:
    V(i, c, r) = máximo valor usando los primeros i elementos con capacidad
                 de CPU c y capacidad de RAM r.
    V(i, c, r) = max(V(i-1, c, r), V(i-1, c-cpu_i, r-ram_i) + valor_i)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


class ElementoCarga(Protocol):
    """Interfaz mínima que debe cumplir un paquete para entrar a la mochila."""

    id: str
    cpu: int
    ram: int
    valor: float


@dataclass(frozen=True)
class ResultadoMochila:
    seleccionados: list[ElementoCarga]
    valor_total: float
    cpu_usada: int
    ram_usada: int
    celdas_exploradas: int

    @property
    def cantidad(self) -> int:
        return len(self.seleccionados)


@dataclass(frozen=True)
class TareaMochila:
    """Elemento sencillo para pruebas y ejemplos académicos."""

    id: str
    cpu: int
    ram: int
    valor: float


class MochilaBinaria2D:
    """Solver 0/1 con dos restricciones de capacidad.

    La reconstrucción de la solución se hace recorriendo la tabla hacia atrás:
    si el valor óptimo con ``i`` elementos difiere del valor con ``i-1``
    elementos para la misma capacidad, el elemento ``i`` fue seleccionado.
    """

    def __init__(self) -> None:
        self.ultimo_numero_elementos = 0
        self.ultima_capacidad_cpu = 0
        self.ultima_capacidad_ram = 0

    @staticmethod
    def _validar(elementos: Sequence[ElementoCarga], cap_cpu: int, cap_ram: int) -> None:
        if isinstance(cap_cpu, bool) or not isinstance(cap_cpu, int) or cap_cpu < 0:
            raise ValueError("La capacidad de CPU debe ser un entero no negativo.")
        if isinstance(cap_ram, bool) or not isinstance(cap_ram, int) or cap_ram < 0:
            raise ValueError("La capacidad de RAM debe ser un entero no negativo.")
        ids: set[str] = set()
        for elemento in elementos:
            if elemento.id in ids:
                raise ValueError(f"Elemento duplicado en la mochila: {elemento.id}")
            ids.add(elemento.id)
            if (
                isinstance(elemento.cpu, bool)
                or not isinstance(elemento.cpu, int)
                or elemento.cpu <= 0
            ):
                raise ValueError(f"CPU inválida en {elemento.id}: {elemento.cpu!r}")
            if (
                isinstance(elemento.ram, bool)
                or not isinstance(elemento.ram, int)
                or elemento.ram <= 0
            ):
                raise ValueError(f"RAM inválida en {elemento.id}: {elemento.ram!r}")
            if elemento.valor < 0:
                raise ValueError(f"Valor negativo en {elemento.id}")

    def resolver(
        self,
        elementos: Sequence[ElementoCarga],
        capacidad_cpu: int,
        capacidad_ram: int,
    ) -> ResultadoMochila:
        """Resuelve la instancia y devuelve el subconjunto óptimo."""
        self._validar(elementos, capacidad_cpu, capacidad_ram)
        n = len(elementos)
        self.ultimo_numero_elementos = n
        self.ultima_capacidad_cpu = capacidad_cpu
        self.ultima_capacidad_ram = capacidad_ram
        if n == 0 or capacidad_cpu == 0 or capacidad_ram == 0:
            return ResultadoMochila([], 0.0, 0, 0, 0)

        # Tabla tridimensional indexada por elemento, CPU y RAM.  Se usa una
        # tabla completa para poder reconstruir la decisión sin ambigüedad.
        dp: list[list[list[float]]] = [
            [[0.0] * (capacidad_ram + 1) for _ in range(capacidad_cpu + 1)]
            for _ in range(n + 1)
        ]
        celdas = 0

        for i, elemento in enumerate(elementos, start=1):
            anterior = dp[i - 1]
            actual = dp[i]
            for c in range(capacidad_cpu + 1):
                for r in range(capacidad_ram + 1):
                    # Alternativa 1: no tomar el elemento i.
                    mejor = anterior[c][r]
                    # Alternativa 2: tomarlo si cabe.
                    if c >= elemento.cpu and r >= elemento.ram:
                        candidato = (
                            anterior[c - elemento.cpu][r - elemento.ram]
                            + elemento.valor
                        )
                        if candidato > mejor:
                            mejor = candidato
                    actual[c][r] = mejor
                    celdas += 1

        valor_optimo = dp[n][capacidad_cpu][capacidad_ram]

        # Reconstrucción hacia atrás de las decisiones.
        seleccionados: list[ElementoCarga] = []
        c, r = capacidad_cpu, capacidad_ram
        for i in range(n, 0, -1):
            if dp[i][c][r] != dp[i - 1][c][r]:
                elemento = elementos[i - 1]
                seleccionados.append(elemento)
                c -= elemento.cpu
                r -= elemento.ram
        seleccionados.reverse()

        cpu_usada = sum(elemento.cpu for elemento in seleccionados)
        ram_usada = sum(elemento.ram for elemento in seleccionados)
        return ResultadoMochila(
            seleccionados=seleccionados,
            valor_total=valor_optimo,
            cpu_usada=cpu_usada,
            ram_usada=ram_usada,
            celdas_exploradas=celdas,
        )


def valor_desde_prioridad(prioridad: int, tamano_mb: float = 0.0) -> float:
    """Convierte prioridad (1-5) y tamaño en un valor de optimización.

    La prioridad domina el valor, mientras que el tamaño aporta un desempate
    pequeño.  Así el solver prefiere tareas urgentes sin ignorar por completo
    las tareas grandes cuando la prioridad es parecida.
    """
    if not 1 <= prioridad <= 5:
        raise ValueError("La prioridad debe estar entre 1 y 5.")
    if tamano_mb < 0:
        raise ValueError("El tamaño del paquete no puede ser negativo.")
    return float(prioridad * 100 + min(49, int(tamano_mb // 2)))

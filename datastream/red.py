"""Topología de red y enrutamiento mediante programación dinámica.

El algoritmo de ruta más corta se implementa con relajación de aristas
(Bellman-Ford).  Es un algoritmo de programación dinámica: la solución con
hasta ``k`` aristas se obtiene a partir de la solución con ``k-1`` aristas.

La latencia de cada enlace no es constante: crece según su tasa de tráfico
observada (congestión).  Por eso la ruta óptima puede cambiar en tiempo real.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class NodoRed:
    """Nodo de la topología: origen, router o servidor."""

    id: str
    nombre: str
    tipo: str
    x: float
    y: float
    cluster: str | None = None
    operativo: bool = True


@dataclass
class Enlace:
    """Enlace bidireccional con latencia dependiente de la congestión."""

    origen: str
    destino: str
    latencia_base: float
    capacidad_pps: float
    factor_congestion: float = 2.8
    paquetes: int = 0
    _eventos_intervalo: int = 0
    _ultimo_muestreo: float = 0.0
    tasa_pps: float = 0.0
    utilizacion: float = 0.0

    @property
    def clave(self) -> tuple[str, str]:
        return tuple(sorted((self.origen, self.destino)))

    @property
    def latencia_efectiva(self) -> float:
        """Latencia base penalizada de forma no lineal por la congestión."""
        saturacion = min(1.75, max(0.0, self.utilizacion))
        return self.latencia_base * (1.0 + self.factor_congestion * saturacion**1.35)

    def registrar_paquete(self) -> None:
        self.paquetes += 1
        self._eventos_intervalo += 1

    def actualizar_congestion(self, tiempo: float) -> None:
        """Calcula una tasa de tráfico suavizada (EWMA) para evitar parpadeos."""
        intervalo = tiempo - self._ultimo_muestreo
        if intervalo < 0.35:
            return
        tasa_instantanea = self._eventos_intervalo / intervalo
        self.tasa_pps = 0.62 * self.tasa_pps + 0.38 * tasa_instantanea
        self.utilizacion = min(2.0, self.tasa_pps / self.capacidad_pps)
        self._eventos_intervalo = 0
        self._ultimo_muestreo = tiempo

    def estado(self) -> str:
        if self.utilizacion >= 0.85:
            return "saturado"
        if self.utilizacion >= 0.50:
            return "congestionado"
        return "libre"


@dataclass
class ResultadoRuta:
    """Salida del algoritmo de programación dinámica."""

    camino: list[str]
    latencia: float
    iteraciones: int
    relajaciones: int

    @property
    def valida(self) -> bool:
        return bool(self.camino)


@dataclass
class Topologia:
    """Grafo no dirigido de la red con estado dinámico de sus nodos."""

    nodos: dict[str, NodoRed] = field(default_factory=dict)
    enlaces: list[Enlace] = field(default_factory=list)
    factor_congestion: float = 2.8
    _adyacencia: dict[str, list[Enlace]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._reconstruir_adyacencia()

    # ------------------------------------------------------------------
    # Construcción y consultas básicas
    # ------------------------------------------------------------------
    def agregar_nodo(self, nodo: NodoRed) -> None:
        if nodo.id in self.nodos:
            raise ValueError(f"Nodo duplicado: {nodo.id}")
        if not 0 <= nodo.x <= 1000 or not 0 <= nodo.y <= 700:
            raise ValueError(f"Coordenadas fuera de rango para {nodo.id}")
        self.nodos[nodo.id] = nodo
        self._adyacencia.setdefault(nodo.id, [])

    def agregar_enlace(
        self, origen: str, destino: str, latencia: float, capacidad: float
    ) -> Enlace:
        if origen == destino:
            raise ValueError("Un enlace no puede unir un nodo consigo mismo.")
        if origen not in self.nodos or destino not in self.nodos:
            raise ValueError(f"Enlace con nodo inexistente: {origen}-{destino}")
        if latencia <= 0 or capacidad <= 0:
            raise ValueError("Latencia y capacidad de enlace deben ser positivas.")
        for enlace in self.enlaces:
            if enlace.clave == tuple(sorted((origen, destino))):
                raise ValueError(f"Enlace duplicado: {origen}-{destino}")
        enlace = Enlace(
            origen=origen,
            destino=destino,
            latencia_base=float(latencia),
            capacidad_pps=float(capacidad),
            factor_congestion=self.factor_congestion,
        )
        self.enlaces.append(enlace)
        self._adyacencia.setdefault(origen, []).append(enlace)
        self._adyacencia.setdefault(destino, []).append(enlace)
        return enlace

    def _reconstruir_adyacencia(self) -> None:
        self._adyacencia = {nodo_id: [] for nodo_id in self.nodos}
        for enlace in self.enlaces:
            self._adyacencia[enlace.origen].append(enlace)
            self._adyacencia[enlace.destino].append(enlace)

    def otro_extremo(self, enlace: Enlace, nodo_id: str) -> str:
        return enlace.destino if enlace.origen == nodo_id else enlace.origen

    def nodo_operativo(self, nodo_id: str) -> bool:
        nodo = self.nodos.get(nodo_id)
        return bool(nodo and nodo.operativo)

    def enlace_disponible(self, enlace: Enlace) -> bool:
        return self.nodo_operativo(enlace.origen) and self.nodo_operativo(enlace.destino)

    def actualizar_congestion(self, tiempo: float) -> None:
        for enlace in self.enlaces:
            enlace.actualizar_congestion(tiempo)

    def reiniciar_contadores(self) -> None:
        for enlace in self.enlaces:
            enlace._eventos_intervalo = 0
            enlace._ultimo_muestreo = 0.0
            enlace.tasa_pps = 0.0
            enlace.utilizacion = 0.0

    # ------------------------------------------------------------------
    # Programación dinámica: Bellman-Ford
    # ------------------------------------------------------------------
    def ruta_mas_corta(self, origen: str, destino: str) -> ResultadoRuta | None:
        """Calcula la ruta de mínima latencia usando programación dinámica.

        ``dist[v]`` representa la mejor latencia conocida hacia ``v``.  En cada
        ronda se relajan todas las aristas, incorporando un salto adicional a
        las soluciones de la ronda anterior.  Tras ``n-1`` rondas la solución
        es óptima para un grafo sin ciclos negativos (aquí todas las latencias
        son positivas).
        """
        if origen not in self.nodos or destino not in self.nodos:
            return None
        if not self.nodo_operativo(origen) or not self.nodo_operativo(destino):
            return None
        if origen == destino:
            return ResultadoRuta([origen], 0.0, 0, 0)

        dist = {nodo_id: math.inf for nodo_id in self.nodos}
        predecesor: dict[str, str | None] = {nodo_id: None for nodo_id in self.nodos}
        dist[origen] = 0.0
        iteraciones = 0
        relajaciones = 0

        for ronda in range(max(1, len(self.nodos) - 1)):
            cambios = False
            iteraciones = ronda + 1
            for enlace in self.enlaces:
                if not self.enlace_disponible(enlace):
                    continue
                for actual, vecino in (
                    (enlace.origen, enlace.destino),
                    (enlace.destino, enlace.origen),
                ):
                    candidato = dist[actual] + enlace.latencia_efectiva
                    if candidato + 1e-12 < dist[vecino]:
                        dist[vecino] = candidato
                        predecesor[vecino] = actual
                        cambios = True
                        relajaciones += 1
            if not cambios:
                break

        if math.isinf(dist[destino]):
            return None

        camino = [destino]
        actual = destino
        while actual != origen:
            anterior = predecesor[actual]
            if anterior is None or len(camino) > len(self.nodos):
                return None
            camino.append(anterior)
            actual = anterior
        camino.reverse()
        return ResultadoRuta(camino, float(dist[destino]), iteraciones, relajaciones)

    def rutas_a_servidores(
        self, origen: str, servidores: Iterable[str]
    ) -> dict[str, ResultadoRuta]:
        """Calcula y devuelve la mejor ruta hacia cada servidor activo."""
        rutas: dict[str, ResultadoRuta] = {}
        for servidor in servidores:
            resultado = self.ruta_mas_corta(origen, servidor)
            if resultado is not None:
                rutas[servidor] = resultado
        return rutas

    def latencia_de_camino(self, camino: list[str]) -> float:
        """Suma la latencia efectiva de un camino ya calculado."""
        if len(camino) < 2:
            return 0.0
        mapa = {enlace.clave: enlace for enlace in self.enlaces}
        total = 0.0
        for origen, destino in zip(camino, camino[1:]):
            enlace = mapa.get(tuple(sorted((origen, destino))))
            if enlace is None or not self.enlace_disponible(enlace):
                return math.inf
            total += enlace.latencia_efectiva
        return total

    def enlace_entre(self, origen: str, destino: str) -> Enlace | None:
        clave = tuple(sorted((origen, destino)))
        for enlace in self.enlaces:
            if enlace.clave == clave:
                return enlace
        return None

    def instantanea(self) -> dict:
        """Copia serializable del estado de la red para UI e informes."""
        return {
            "nodos": [
                {
                    "id": nodo.id,
                    "nombre": nodo.nombre,
                    "tipo": nodo.tipo,
                    "x": nodo.x,
                    "y": nodo.y,
                    "cluster": nodo.cluster,
                    "operativo": nodo.operativo,
                }
                for nodo in self.nodos.values()
            ],
            "enlaces": [
                {
                    "origen": enlace.origen,
                    "destino": enlace.destino,
                    "latencia_base": enlace.latencia_base,
                    "latencia_efectiva": enlace.latencia_efectiva,
                    "capacidad_pps": enlace.capacidad_pps,
                    "tasa_pps": enlace.tasa_pps,
                    "utilizacion": enlace.utilizacion,
                    "estado": enlace.estado(),
                    "paquetes": enlace.paquetes,
                }
                for enlace in self.enlaces
            ],
        }


def crear_topologia_por_defecto(factor_congestion: float = 2.8) -> Topologia:
    """Construye la red de DataStream con caminos alternativos redundantes.

    El nodo ``Internet`` es la fuente de tráfico.  Los routers R2, R3 y R4
    ofrecen rutas alternativas hacia los cuatro servidores, condición necesaria
    para que la ruta óptima pueda cambiar cuando un enlace se congestiona.
    """
    topologia = Topologia(factor_congestion=factor_congestion)

    nodos = [
        NodoRed("Internet", "Internet", "origen", 70, 300),
        NodoRed("R1", "Núcleo R1", "router", 255, 300),
        NodoRed("R2", "Router R2", "router", 440, 150),
        NodoRed("R3", "Router R3", "router", 440, 450),
        NodoRed("R4", "Router R4", "router", 635, 300),
        NodoRed("S1", "Servidor S1", "servidor", 845, 85, "Cómputo"),
        NodoRed("S2", "Servidor S2", "servidor", 845, 225, "Cómputo"),
        NodoRed("S3", "Servidor S3", "servidor", 845, 405, "Almacenamiento"),
        NodoRed("S4", "Servidor S4", "servidor", 845, 545, "Red"),
    ]
    for nodo in nodos:
        topologia.agregar_nodo(nodo)

    # (origen, destino, latencia base en segundos simulados, capacidad pps)
    conexiones = [
        ("Internet", "R1", 0.45, 24.0),
        ("R1", "R2", 0.80, 9.0),
        ("R1", "R3", 0.95, 9.0),
        ("R2", "R3", 1.45, 5.5),
        ("R2", "R4", 0.55, 7.0),
        ("R3", "R4", 0.65, 7.0),
        ("R2", "S1", 0.95, 5.0),
        ("R4", "S1", 1.20, 4.0),
        ("R2", "S2", 1.00, 5.0),
        ("R4", "S2", 0.85, 5.0),
        ("R3", "S3", 0.90, 5.0),
        ("R4", "S3", 1.15, 4.0),
        ("R3", "S4", 1.05, 5.0),
        ("R4", "S4", 0.80, 5.0),
    ]
    for datos in conexiones:
        topologia.agregar_enlace(*datos)
    return topologia

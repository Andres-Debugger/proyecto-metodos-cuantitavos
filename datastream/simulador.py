"""Motor de simulación de eventos discretos para DataStream Networks.

Se integran en un mismo reloj SimPy:

* llegadas Poisson de paquetes (teoría de colas),
* recorrido del paquete por la ruta calculada con programación dinámica,
* admisión por mochila bidimensional en cada servidor,
* tiempos de servicio exponenciales,
* fallas y reparaciones estocásticas de routers y servidores,
* control de ventiladores por gradiente y asignación de energía por Lagrange.

La interfaz gráfica consume el estado público del simulador; el generador de
informes consume ``instantanea()`` para trabajar con una copia consistente.
"""

from __future__ import annotations

import math
import random
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Deque

import simpy

from .configuracion import Configuracion
from .lagrange import OptimizadorEnergia, ResultadoEnergia
from .mochila import MochilaBinaria2D, ResultadoMochila, valor_desde_prioridad
from .red import ResultadoRuta, Topologia, crear_topologia_por_defecto
from .termodinamica import (
    ControladorRefrigeracion,
    ModeloRefrigeracion,
    ModeloTemperatura,
)


@dataclass
class Paquete:
    """Paquete/tarea que atraviesa la red y compite por capacidad de servidor."""

    id: str
    creado: float
    prioridad: int
    cpu: int
    ram: int
    tamano_mb: float
    valor: float
    destino: str = ""
    camino: list[str] = field(default_factory=list)
    estado: str = "creado"
    enlace_indice: int = -1
    inicio_tramo: float = 0.0
    fin_tramo: float = 0.0
    servidor: str | None = None
    llegada_servidor: float | None = None
    inicio_servicio: float | None = None
    completado: float | None = None
    motivo: str = ""
    tiempo_servicio: float = 1.0

    @property
    def latencia(self) -> float | None:
        if self.completado is None:
            return None
        return max(0.0, self.completado - self.creado)


@dataclass
class EspecificacionServidor:
    id: str
    nombre: str
    cluster: str
    capacidad_cpu: int
    capacidad_ram: int
    factor_velocidad: float
    slots: int


ESPECIFICACIONES_SERVIDOR = (
    EspecificacionServidor("S1", "Servidor S1", "Cómputo", 20, 56, 1.00, 4),
    EspecificacionServidor("S2", "Servidor S2", "Cómputo", 16, 48, 0.92, 4),
    EspecificacionServidor("S3", "Servidor S3", "Almacenamiento", 14, 64, 0.88, 4),
    EspecificacionServidor("S4", "Servidor S4", "Red", 12, 40, 1.10, 3),
)


class Servidor:
    """Servidor con admisión por mochila, servicio concurrente y fallas."""

    def __init__(
        self,
        especificacion: EspecificacionServidor,
        simulador: "SimuladorDatacenter",
        env: simpy.Environment,
        rng: random.Random,
        topologia: Topologia,
    ) -> None:
        self.id = especificacion.id
        self.nombre = especificacion.nombre
        self.cluster = especificacion.cluster
        self.capacidad_cpu = especificacion.capacidad_cpu
        self.capacidad_ram = especificacion.capacidad_ram
        self.factor_velocidad = especificacion.factor_velocidad
        self.env = env
        self.rng = rng
        self.topologia = topologia
        self.simulador = simulador
        self.recurso_slots = simpy.PriorityResource(env, capacity=especificacion.slots)

        self.operativo = True
        self.evento_falla = env.event()
        self.fallas = 0
        self.reparado_en = 0.0

        self.candidatos: list[Paquete] = []
        self.procesando: dict[str, Paquete] = {}
        self.cpu_comprometida = 0
        self.ram_comprometida = 0

        self.aceptados = 0
        self.procesados = 0
        self.perdidos = 0
        self.expirados = 0
        self.valor_aceptado = 0.0
        self.valor_procesado = 0.0
        self.paquetes_recibidos = 0
        self.tiempo_servicio_acumulado = 0.0
        self.decisiones_mochila = 0
        self.celdas_dp = 0
        self.factor_energia = 1.0
        self.carga = 0.0
        self.temperatura = 24.0
        self.ventilador = 0.45
        self.potencia_refrigeracion = 0.0

        self.controlador = ControladorRefrigeracion(
            id_servidor=self.id,
            modelo=ModeloRefrigeracion(modelo_temperatura=simulador.modelo_temperatura),
            temperatura=self.temperatura,
            ventilador=self.ventilador,
        )
        self._emergencia_previa = False

        env.process(self._bucle_planificacion())

    # ------------------------------------------------------------------
    # Admisión y servicio
    # ------------------------------------------------------------------
    @property
    def capacidad_libre_cpu(self) -> int:
        return max(0, self.capacidad_cpu - self.cpu_comprometida)

    @property
    def capacidad_libre_ram(self) -> int:
        return max(0, self.capacidad_ram - self.ram_comprometida)

    def estimacion_espera(self) -> float:
        pendientes = len(self.candidatos) + len(self.procesando)
        espera_base = pendientes * 1.25 / max(1, self.recurso_slots.capacity)
        penalizacion_carga = 1.5 * self.carga
        return espera_base + penalizacion_carga

    def recibir(self, paquete: Paquete) -> None:
        """Coloca un paquete en la ventana de admisión del servidor."""
        if not self.operativo:
            self.simulador.descartar(paquete, "servidor_fuera_servicio")
            return
        self.paquetes_recibidos += 1
        paquete.servidor = self.id
        paquete.llegada_servidor = self.env.now
        paquete.estado = "en_cola"

        if len(self.candidatos) >= self.simulador.config.capacidad_buffer:
            # Si el buffer está lleno se conserva la tarea de mayor prioridad.
            debil = min(self.candidatos, key=lambda p: (p.prioridad, -p.creado))
            if paquete.prioridad > debil.prioridad:
                self.candidatos.remove(debil)
                self.simulador.descartar(debil, "buffer_lleno")
                self.candidatos.append(paquete)
            else:
                self.simulador.descartar(paquete, "buffer_lleno")
        else:
            self.candidatos.append(paquete)

    def _bucle_planificacion(self):
        intervalo = self.simulador.config.intervalo_planificacion
        while True:
            yield self.env.timeout(intervalo)
            self.planificar()

    def planificar(self) -> ResultadoMochila | None:
        """Ejecuta la mochila sobre los candidatos y lanza las tareas elegidas."""
        ahora = self.env.now
        # Tareas que esperaron demasiado se descartan para evitar bloqueos.
        vigentes: list[Paquete] = []
        for paquete in self.candidatos:
            if paquete.estado != "en_cola":
                continue
            if ahora - (paquete.llegada_servidor or ahora) > 6.0:
                self.expirados += 1
                self.simulador.descartar(paquete, "expirado_en_admision")
            else:
                vigentes.append(paquete)
        self.candidatos = vigentes

        if not self.operativo or not self.candidatos:
            return None
        if self.capacidad_libre_cpu <= 0 or self.capacidad_libre_ram <= 0:
            return None

        solver = MochilaBinaria2D()
        resultado = solver.resolver(
            self.candidatos,
            self.capacidad_libre_cpu,
            self.capacidad_libre_ram,
        )
        self.decisiones_mochila += 1
        self.celdas_dp += resultado.celdas_exploradas
        if not resultado.seleccionados:
            return resultado

        for paquete in resultado.seleccionados:
            if paquete not in self.candidatos:
                continue
            self.candidatos.remove(paquete)
            self.cpu_comprometida += paquete.cpu
            self.ram_comprometida += paquete.ram
            self.aceptados += 1
            self.valor_aceptado += paquete.valor
            self.simulador.paquetes_aceptados += 1
            self.procesando[paquete.id] = paquete
            self.env.process(self._procesar(paquete, self.evento_falla))
        return resultado

    def _procesar(self, paquete: Paquete, evento_falla: simpy.Event):
        """Procesa una tarea y libera sus recursos al terminar o fallar."""
        with self.recurso_slots.request(priority=6 - paquete.prioridad) as solicitud:
            resultado_espera = yield solicitud | evento_falla
            if evento_falla in resultado_espera or not self.operativo:
                self._perder(paquete, "servidor_fuera_servicio")
                return

            paquete.estado = "procesando"
            paquete.inicio_servicio = self.env.now
            duracion = max(
                0.25,
                paquete.tiempo_servicio
                / max(0.5, self.factor_energia * self.factor_velocidad),
            )
            resultado = yield self.env.timeout(duracion) | evento_falla
            if evento_falla in resultado or not self.operativo:
                self._perder(paquete, "falla_durante_procesamiento")
                return

            self.simulador.finalizar_paquete(paquete)
            self.procesados += 1
            self.valor_procesado += paquete.valor
            self.tiempo_servicio_acumulado += duracion
            self._liberar_compromiso(paquete)

    def _perder(self, paquete: Paquete, motivo: str) -> None:
        self.perdidos += 1
        self.simulador.descartar(paquete, motivo)
        self._liberar_compromiso(paquete)

    def _liberar_compromiso(self, paquete: Paquete) -> None:
        self.procesando.pop(paquete.id, None)
        self.cpu_comprometida = max(0, self.cpu_comprometida - paquete.cpu)
        self.ram_comprometida = max(0, self.ram_comprometida - paquete.ram)

    def fallar(self) -> None:
        if not self.operativo:
            return
        self.operativo = False
        self.fallas += 1
        self.topologia.nodos[self.id].operativo = False
        # Las tareas aún no admitidas se pierden; las activas observan el evento.
        for paquete in list(self.candidatos):
            self.simulador.descartar(paquete, "nodo_fuera_servicio")
            self.perdidos += 1
        self.candidatos.clear()
        if not self.evento_falla.triggered:
            self.evento_falla.succeed()
        self.simulador.registrar_alerta(
            "falla", f"{self.nombre} fuera de servicio", "alta", self.env.now
        )

    def reparar(self) -> None:
        if self.operativo:
            return
        self.operativo = True
        self.topologia.nodos[self.id].operativo = True
        self.evento_falla = self.env.event()
        self.reparado_en = self.env.now
        self.simulador.registrar_alerta(
            "recuperacion", f"{self.nombre} reparado y en línea", "media", self.env.now
        )

    def actualizar_control(self, tiempo: float) -> None:
        carga = min(
            self.simulador.modelo_temperatura.carga_maxima,
            self.cpu_comprometida / max(1, self.capacidad_cpu)
            + 0.15 * len(self.candidatos) / max(1, self.simulador.config.capacidad_buffer),
        )
        self.carga = carga
        estado = self.controlador.actualizar(carga, tiempo)
        self.ventilador = estado["ventilador"]
        self.temperatura = estado["temperatura"]
        self.potencia_refrigeracion = estado["potencia"]
        if estado["emergencia"] and not self._emergencia_previa:
            self.simulador.registrar_alerta(
                "emergencia",
                f"{self.nombre}: enfriamiento de emergencia ({self.temperatura:.1f} °C)",
                "alta",
                tiempo,
            )
            self.simulador.emergencias += 1
        self._emergencia_previa = bool(estado["emergencia"])

    def instantanea(self) -> dict:
        return {
            "id": self.id,
            "nombre": self.nombre,
            "cluster": self.cluster,
            "operativo": self.operativo,
            "capacidad_cpu": self.capacidad_cpu,
            "capacidad_ram": self.capacidad_ram,
            "cpu_comprometida": self.cpu_comprometida,
            "ram_comprometida": self.ram_comprometida,
            "carga": self.carga,
            "temperatura": self.temperatura,
            "ventilador": self.ventilador,
            "potencia_refrigeracion": self.potencia_refrigeracion,
            "emergencia": self.controlador.emergencia,
            "candidatos": len(self.candidatos),
            "procesando": len(self.procesando),
            "aceptados": self.aceptados,
            "procesados": self.procesados,
            "perdidos": self.perdidos,
            "expirados": self.expirados,
            "decisiones_mochila": self.decisiones_mochila,
            "celdas_dp": self.celdas_dp,
            "valor_aceptado": self.valor_aceptado,
            "valor_procesado": self.valor_procesado,
            "fallas": self.fallas,
            "factor_energia": self.factor_energia,
            "slots_ocupados": len(self.procesando),
            "slots_totales": self.recurso_slots.capacity,
        }


class SimuladorDatacenter:
    """Orquesta red, tráfico, servidores, energía y métricas."""

    def __init__(self, config: Configuracion) -> None:
        self.config = config
        self.env = simpy.Environment()
        self.rng_trafico = random.Random(config.semilla)
        self.rng_servicio = random.Random(config.semilla + 101)
        self.rng_fallas = random.Random(config.semilla + 202)

        self.modelo_temperatura = ModeloTemperatura()
        self.optimizador_energia = OptimizadorEnergia()
        self.topologia = crear_topologia_por_defecto(config.factor_congestion)

        self.servidores: dict[str, Servidor] = {}
        for especificacion in ESPECIFICACIONES_SERVIDOR:
            self.servidores[especificacion.id] = Servidor(
                especificacion,
                self,
                self.env,
                self.rng_servicio,
                self.topologia,
            )

        self.rutas_actuales: dict[str, dict] = {}
        self.paquetes_activos: dict[str, Paquete] = {}
        self.alertas: Deque[dict] = deque(maxlen=40)
        self.historial: list[dict] = []
        self.ultimo_cambio_ruta: dict | None = None
        self.asignacion_energia: ResultadoEnergia
        self.demanda_clusters: tuple[float, ...] = (0.25, 0.25, 0.25)
        self.ultima_actualizacion_energia = 0.0

        self.inicio = 0.0
        self.terminada = False
        self.motivo_fin = ""

        self.paquetes_generados = 0
        self.paquetes_procesados = 0
        self.paquetes_descartados = 0
        self.paquetes_aceptados = 0
        self.valor_generado = 0.0
        self.valor_procesado = 0.0
        self.suma_latencia = 0.0
        self.latencias: list[float] = []
        self.motivos_descarte: Counter[str] = Counter()
        self.cambios_ruta = 0
        self.cambios_ruta_congestion = 0
        self._ultimo_cambio_por_servidor: dict[str, float] = {}
        self.fallas_totales = 0
        self.emergencias = 0
        self.energia_ventiladores_kwh = 0.0
        self.distribucion_destino: Counter[str] = Counter()
        self._ultimos_contadores = {
            "generados": 0,
            "procesados": 0,
            "descartados": 0,
        }
        self._ultimo_tiempo_historial = 0.0
        self._proximo_control_energia = 0.0
        self._paquete_secuencia = 0
        self._events_configured = False

        # Primera solución de Lagrange con los pesos base, visible desde t=0.
        self._optimizar_energia(forzar=True)
        self._actualizar_rutas(causa="inicial", contar_cambio=False)
        self._registrar_historial()
        self._iniciar_procesos()

    # ------------------------------------------------------------------
    # Arranque de procesos SimPy
    # ------------------------------------------------------------------
    def _iniciar_procesos(self) -> None:
        if self._events_configured:
            return
        self._events_configured = True
        self.env.process(self._generar_trafico())
        self.env.process(self._vigilar_rutas())
        self.env.process(self._bucle_control())
        for nodo_id in self.topologia.nodos:
            if nodo_id == "Internet":
                continue
            self.env.process(self._proceso_fallas(nodo_id))

    def _proceso_fallas(self, nodo_id: str):
        es_servidor = nodo_id in self.servidores
        mtbf = self.config.mtbf_servidor if es_servidor else self.config.mtbf_router
        while True:
            yield self.env.timeout(self.rng_fallas.expovariate(1.0 / mtbf))
            if es_servidor:
                self.servidores[nodo_id].fallar()
            else:
                self._fallar_router(nodo_id)
            self.fallas_totales += 1
            self._actualizar_rutas(causa="falla", contar_cambio=False)

            reparacion = max(
                1.0, self.rng_fallas.expovariate(1.0 / self.config.tiempo_reparacion)
            )
            yield self.env.timeout(reparacion)
            if es_servidor:
                self.servidores[nodo_id].reparar()
            else:
                self._reparar_router(nodo_id)
            self._actualizar_rutas(causa="recuperación", contar_cambio=False)

    def _fallar_router(self, nodo_id: str) -> None:
        nodo = self.topologia.nodos[nodo_id]
        if not nodo.operativo:
            return
        nodo.operativo = False
        self.registrar_alerta(
            "falla", f"{nodo.nombre} fuera de servicio", "alta", self.env.now
        )

    def _reparar_router(self, nodo_id: str) -> None:
        nodo = self.topologia.nodos[nodo_id]
        if nodo.operativo:
            return
        nodo.operativo = True
        self.registrar_alerta(
            "recuperacion", f"{nodo.nombre} reparado y en línea", "media", self.env.now
        )

    # ------------------------------------------------------------------
    # Generación y recorrido de paquetes
    # ------------------------------------------------------------------
    def _generar_trafico(self):
        while True:
            espera = self.rng_trafico.expovariate(max(0.1, self.config.tasa_llegada))
            yield self.env.timeout(max(0.001, espera))
            self._crear_paquete()

    def _crear_paquete(self) -> None:
        self._paquete_secuencia += 1
        prioridad = self.rng_trafico.choices(
            [1, 2, 3, 4, 5], weights=[12, 23, 30, 22, 13], k=1
        )[0]
        cpu = min(8, max(1, self.rng_trafico.randint(1, 3) + prioridad // 2))
        ram = min(14, max(2, cpu * 2 + self.rng_trafico.randint(0, 4)))
        tamano = round(self.rng_trafico.uniform(2.0, 30.0), 1)
        valor = valor_desde_prioridad(prioridad, tamano)
        paquete = Paquete(
            id=f"P{self._paquete_secuencia:05d}",
            creado=self.env.now,
            prioridad=prioridad,
            cpu=cpu,
            ram=ram,
            tamano_mb=tamano,
            valor=valor,
            tiempo_servicio=0.45 + tamano / 30.0 + cpu * 0.04,
        )
        self.paquetes_generados += 1
        self.valor_generado += valor

        candidatos: list[tuple[float, str, ResultadoRuta]] = []
        for servidor_id, servidor in self.servidores.items():
            if not servidor.operativo:
                continue
            ruta_info = self.rutas_actuales.get(servidor_id)
            if not ruta_info or not ruta_info.get("disponible"):
                ruta = self.topologia.ruta_mas_corta("Internet", servidor_id)
                if ruta is None:
                    continue
            else:
                ruta = ResultadoRuta(
                    camino=list(ruta_info["camino"]),
                    latencia=float(ruta_info["latencia"]),
                    iteraciones=int(ruta_info.get("iteraciones", 0)),
                    relajaciones=int(ruta_info.get("relajaciones", 0)),
                )
            # Puntaje de asignación: latencia + espera + carga + pequeño azar.
            puntaje = (
                ruta.latencia
                + servidor.estimacion_espera()
                + servidor.carga * 1.2
                + (1.0 - servidor.factor_energia) * 0.8
                + self.rng_trafico.random() * 0.12
            )
            candidatos.append((puntaje, servidor_id, ruta))

        if not candidatos:
            self.paquetes_descartados += 1
            self.motivos_descarte["sin_ruta_disponible"] += 1
            self.registrar_alerta(
                "descarte", f"{paquete.id} descartado: red sin ruta", "media", self.env.now
            )
            return

        _, destino, ruta = min(candidatos, key=lambda item: item[0])
        paquete.destino = destino
        paquete.camino = list(ruta.camino)
        paquete.estado = "en_viaje"
        self.paquetes_activos[paquete.id] = paquete
        self.distribucion_destino[destino] += 1
        self.env.process(self._viajar(paquete))

    def _viajar(self, paquete: Paquete):
        """Recorre el camino óptimo enlace por enlace."""
        for indice, (origen, destino) in enumerate(zip(paquete.camino, paquete.camino[1:])):
            enlace = self.topologia.enlace_entre(origen, destino)
            if (
                enlace is None
                or not self.topologia.enlace_disponible(enlace)
                or paquete.estado in {"descartado", "completado"}
            ):
                self.descartar(paquete, "ruta_interrumpida")
                return

            enlace.registrar_paquete()
            paquete.estado = "en_viaje"
            paquete.enlace_indice = indice
            paquete.inicio_tramo = self.env.now
            travesia = max(
                0.08,
                enlace.latencia_efectiva * self.rng_trafico.uniform(0.93, 1.08),
            )
            paquete.fin_tramo = self.env.now + travesia
            yield self.env.timeout(travesia)

        if paquete.estado in {"descartado", "completado"}:
            return
        servidor = self.servidores.get(paquete.destino)
        if servidor is None or not servidor.operativo:
            self.descartar(paquete, "servidor_fuera_servicio")
            return
        paquete.enlace_indice = -1
        servidor.recibir(paquete)

    # ------------------------------------------------------------------
    # Rutas dinámicas y control energético
    # ------------------------------------------------------------------
    def _vigilar_rutas(self):
        while True:
            yield self.env.timeout(self.config.intervalo_rutas)
            self.topologia.actualizar_congestion(self.env.now)
            self._actualizar_rutas(causa="congestión", contar_cambio=True)

    def _actualizar_rutas(self, causa: str, contar_cambio: bool = True) -> None:
        for servidor_id, servidor in self.servidores.items():
            disponible = servidor.operativo
            ruta = (
                self.topologia.ruta_mas_corta("Internet", servidor_id)
                if disponible
                else None
            )
            anterior = self.rutas_actuales.get(servidor_id)
            if ruta is None:
                self.rutas_actuales[servidor_id] = {
                    "camino": list(anterior["camino"]) if anterior else [],
                    "latencia": math.inf,
                    "disponible": False,
                    "actualizada_en": self.env.now,
                    "iteraciones": 0,
                    "relajaciones": 0,
                }
                continue

            camino_final = list(ruta.camino)
            latencia_final = ruta.latencia
            cambio_efectivo = False
            if (
                contar_cambio
                and anterior
                and anterior.get("disponible")
                and anterior.get("camino")
                and anterior.get("camino") != ruta.camino
            ):
                latencia_anterior = self.topologia.latencia_de_camino(anterior["camino"])
                cooldown = self.env.now - self._ultimo_cambio_por_servidor.get(
                    servidor_id, -1e9
                ) < 6.0
                # Histéresis: solo se cambia si la nueva ruta mejora al menos 15 %
                # y no está activo el tiempo de espera. Evita parpadeos por ruido.
                mejora_suficiente = (
                    math.isfinite(latencia_anterior)
                    and ruta.latencia < latencia_anterior * 0.85
                )
                if mejora_suficiente and not cooldown:
                    cambio_efectivo = True
                elif math.isfinite(latencia_anterior):
                    camino_final = list(anterior["camino"])
                    latencia_final = latencia_anterior
            if cambio_efectivo:
                self.cambios_ruta += 1
                self._ultimo_cambio_por_servidor[servidor_id] = self.env.now
                if causa == "congestión":
                    self.cambios_ruta_congestion += 1
                    self.ultimo_cambio_ruta = {
                        "tiempo": self.env.now,
                        "destino": servidor_id,
                        "anterior": list(anterior["camino"]),
                        "nueva": list(ruta.camino),
                        "latencia": ruta.latencia,
                    }
                    self.registrar_alerta(
                        "ruta",
                        f"Ruta a {servidor_id} actualizada por congestión: "
                        f"{' → '.join(ruta.camino)}",
                        "info",
                        self.env.now,
                    )
            self.rutas_actuales[servidor_id] = {
                "camino": camino_final,
                "latencia": latencia_final,
                "disponible": True,
                "actualizada_en": self.env.now,
                "iteraciones": ruta.iteraciones,
                "relajaciones": ruta.relajaciones,
            }

    def _bucle_control(self):
        ultimo = self.env.now
        while True:
            yield self.env.timeout(1.0)
            ahora = self.env.now
            dt = max(1e-9, ahora - ultimo)
            ultimo = ahora
            self.topologia.actualizar_congestion(ahora)
            for servidor in self.servidores.values():
                servidor.actualizar_control(ahora)
                if servidor.operativo:
                    self.energia_ventiladores_kwh += (
                        servidor.potencia_refrigeracion * dt / 3600.0
                    )
            if ahora >= self._proximo_control_energia:
                self._optimizar_energia()
                self._proximo_control_energia = ahora + 2.0
            self._registrar_historial()

    def _optimizar_energia(self, forzar: bool = False) -> None:
        carga_computo = sum(
            self.servidores[sid].cpu_comprometida / self.servidores[sid].capacidad_cpu
            for sid in ("S1", "S2")
        ) / 2.0
        carga_almacenamiento = (
            self.servidores["S3"].cpu_comprometida / self.servidores["S3"].capacidad_cpu
        )
        carga_red = (
            self.servidores["S4"].cpu_comprometida / self.servidores["S4"].capacidad_cpu
        )
        utilizacion_enlaces = [
            enlace.utilizacion for enlace in self.topologia.enlaces
        ]
        demanda = (
            min(1.4, 0.12 + carga_computo),
            min(1.4, 0.12 + carga_almacenamiento),
            min(1.4, 0.12 + carga_red + 0.15 * sum(utilizacion_enlaces) / len(utilizacion_enlaces)),
        )
        self.demanda_clusters = demanda
        pesos = self.optimizador_energia.pesos_dinamicos(
            demanda, self.config.pesos_cluster
        )
        self.asignacion_energia = self.optimizador_energia.resolver(
            pesos, self.config.presupuesto_energia
        )
        self.ultima_actualizacion_energia = self.env.now

        # La energía del clúster modula la velocidad de sus servidores.
        referencia = self.config.presupuesto_energia / 3.0
        for servidor in self.servidores.values():
            indice = self.optimizador_energia.nombres_cluster.index(servidor.cluster)
            factor = self.asignacion_energia.asignacion[indice] / referencia
            servidor.factor_energia = min(1.20, max(0.80, factor))

    def _registrar_historial(self) -> None:
        ahora = self.env.now
        dt = max(1e-9, ahora - self._ultimo_tiempo_historial)
        self._ultimo_tiempo_historial = ahora
        generados = self.paquetes_generados - self._ultimos_contadores["generados"]
        procesados = self.paquetes_procesados - self._ultimos_contadores["procesados"]
        descartados = self.paquetes_descartados - self._ultimos_contadores["descartados"]
        self._ultimos_contadores = {
            "generados": self.paquetes_generados,
            "procesados": self.paquetes_procesados,
            "descartados": self.paquetes_descartados,
        }

        activos = [
            servidor for servidor in self.servidores.values() if servidor.operativo
        ]
        promedio_temp = (
            sum(servidor.temperatura for servidor in activos) / len(activos)
            if activos
            else 0.0
        )
        promedio_fan = (
            sum(servidor.ventilador for servidor in activos) / len(activos)
            if activos
            else 0.0
        )
        potencia = sum(servidor.potencia_refrigeracion for servidor in activos)
        carga_media = sum(servidor.carga for servidor in self.servidores.values()) / 4.0
        emergencias = sum(
            1 for servidor in self.servidores.values() if servidor.controlador.emergencia
        )
        latencia_promedio = (
            self.suma_latencia / self.paquetes_procesados
            if self.paquetes_procesados
            else 0.0
        )
        registro = {
            "tiempo": ahora,
            "temperatura_media": promedio_temp,
            "temperatura_maxima": max(
                (servidor.temperatura for servidor in self.servidores.values()),
                default=0.0,
            ),
            "ventilador_medio": promedio_fan,
            "potencia_refrigeracion": potencia,
            "carga_media": carga_media,
            "emergencias": emergencias,
            "llegadas_por_s": generados / dt,
            "procesados_por_s": procesados / dt,
            "descartados_por_s": descartados / dt,
            "activos": len(self.paquetes_activos),
            "procesados_acumulados": self.paquetes_procesados,
            "latencia_promedio": latencia_promedio,
            "cambios_ruta": self.cambios_ruta,
            "asignacion_energia": list(self.asignacion_energia.asignacion),
            "lagrange": self.asignacion_energia.multiplicador_lagrange,
            "rendimiento_energia": self.asignacion_energia.rendimiento_total,
            "demanda_clusters": list(self.demanda_clusters),
        }
        self.historial.append(registro)
        if len(self.historial) > 1200:
            self.historial = self.historial[-1200:]

    # ------------------------------------------------------------------
    # Métricas y cierre
    # ------------------------------------------------------------------
    def descartar(self, paquete: Paquete, motivo: str) -> None:
        """Marca un paquete como perdido una sola vez y actualiza contadores."""
        if paquete.estado in {"descartado", "completado"}:
            return
        paquete.estado = "descartado"
        paquete.motivo = motivo
        paquete.completado = self.env.now
        self.paquetes_descartados += 1
        self.motivos_descarte[motivo] += 1
        self.paquetes_activos.pop(paquete.id, None)

    def finalizar_paquete(self, paquete: Paquete) -> None:
        if paquete.estado in {"descartado", "completado"}:
            return
        paquete.estado = "completado"
        paquete.completado = self.env.now
        latencia = paquete.latencia or 0.0
        self.paquetes_procesados += 1
        self.suma_latencia += latencia
        self.latencias.append(latencia)
        self.valor_procesado += paquete.valor
        self.paquetes_activos.pop(paquete.id, None)

    def registrar_alerta(self, tipo: str, mensaje: str, severidad: str, tiempo: float) -> None:
        self.alertas.append(
            {
                "tiempo": tiempo,
                "tipo": tipo,
                "mensaje": mensaje,
                "severidad": severidad,
            }
        )

    def avanzar(self, dt: float) -> bool:
        """Avanza el reloj simulado ``dt`` segundos sin bloquear la UI."""
        if self.terminada:
            return False
        if isinstance(dt, bool) or not isinstance(dt, (int, float)) or not math.isfinite(dt):
            raise ValueError("El incremento de tiempo debe ser numérico y finito.")
        if dt <= 0:
            return False
        objetivo = min(self.config.duracion, self.env.now + float(dt))
        if objetivo <= self.env.now:
            self._finalizar_simulacion()
            return False
        try:
            self.env.run(until=objetivo)
        except simpy.core.EmptySchedule:
            self._finalizar_simulacion()
            return False
        if self.env.now >= self.config.duracion - 1e-9:
            self._finalizar_simulacion()
            return False
        return True

    def ejecutar(self) -> None:
        """Ejecuta la simulación completa (modo consola/informe)."""
        if self.terminada:
            return
        try:
            self.env.run(until=self.config.duracion)
        except simpy.core.EmptySchedule:
            pass
        self._finalizar_simulacion()

    def _finalizar_simulacion(self) -> None:
        if self.terminada:
            return
        self.terminada = True
        self.motivo_fin = "Duración configurada alcanzada"
        self.registrar_alerta(
            "sistema", "Simulación finalizada", "info", self.env.now
        )

    def teoria_colas(self) -> dict:
        """Estimación M/M/c por servidor a partir de las tasas observadas."""
        duracion = max(1e-9, self.env.now)
        tasa_llegadas = self.paquetes_generados / duracion
        service_times = [
            servidor.tiempo_servicio_acumulado
            for servidor in self.servidores.values()
            if servidor.tiempo_servicio_acumulado > 0
        ]
        servicios = sum(servidor.procesados for servidor in self.servidores.values())
        if service_times and servicios:
            servicio_promedio = sum(service_times) / servicios
            mu = 1.0 / max(1e-9, servicio_promedio)
            lambda_por_servidor = tasa_llegadas / 4.0
            # Cada servidor atiende hasta ``slots`` tareas concurrentes: M/M/c.
            c = 4
            carga_ofrecida = lambda_por_servidor / mu
            rho = carga_ofrecida / c
            if rho < 1:
                suma = sum(carga_ofrecida**k / math.factorial(k) for k in range(c))
                cola_termino = carga_ofrecida**c / (
                    math.factorial(c) * (1.0 - rho)
                )
                p0 = 1.0 / (suma + cola_termino)
                lq = p0 * carga_ofrecida**c * rho / (
                    math.factorial(c) * (1.0 - rho) ** 2
                )
                wq = lq / max(1e-9, lambda_por_servidor)
                w = wq + 1.0 / mu
                l = lq + carga_ofrecida
            else:
                wq = w = lq = l = math.inf
            return {
                "lambda_total": tasa_llegadas,
                "lambda_por_servidor": lambda_por_servidor,
                "mu": mu,
                "c": c,
                "rho": rho,
                "Lq": lq,
                "L": l,
                "Wq": wq,
                "W": w,
                "estable": rho < 1,
                "servicio_promedio": servicio_promedio,
            }
        return {
            "lambda_total": tasa_llegadas,
            "lambda_por_servidor": tasa_llegadas / 4.0,
            "mu": 0.0,
            "c": 4,
            "rho": 0.0,
            "Lq": 0.0,
            "L": 0.0,
            "Wq": 0.0,
            "W": 0.0,
            "estable": True,
            "servicio_promedio": 0.0,
        }

    def metricas(self) -> dict:
        duracion = max(1e-9, self.env.now)
        latencia_promedio = (
            self.suma_latencia / self.paquetes_procesados
            if self.paquetes_procesados
            else 0.0
        )
        return {
            "tiempo_simulado": self.env.now,
            "duracion_configurada": self.config.duracion,
            "terminada": self.terminada,
            "paquetes_generados": self.paquetes_generados,
            "paquetes_procesados": self.paquetes_procesados,
            "paquetes_descartados": self.paquetes_descartados,
            "paquetes_activos": len(self.paquetes_activos),
            "paquetes_aceptados": self.paquetes_aceptados,
            "throughput": self.paquetes_procesados / duracion,
            "tasa_llegada_observada": self.paquetes_generados / duracion,
            "latencia_promedio": latencia_promedio,
            "latencia_p95": self._percentil(self.latencias, 0.95),
            "valor_generado": self.valor_generado,
            "valor_procesado": self.valor_procesado,
            "tasa_aceptacion": (
                self.paquetes_aceptados / self.paquetes_generados
                if self.paquetes_generados
                else 0.0
            ),
            "tasa_descarte": (
                self.paquetes_descartados / self.paquetes_generados
                if self.paquetes_generados
                else 0.0
            ),
            "motivos_descarte": dict(self.motivos_descarte),
            "cambios_ruta": self.cambios_ruta,
            "cambios_ruta_congestion": self.cambios_ruta_congestion,
            "fallas_totales": self.fallas_totales,
            "emergencias": self.emergencias,
            "energia_ventiladores_kwh": self.energia_ventiladores_kwh,
            "distribucion_destino": dict(self.distribucion_destino),
            "teoria_colas": self.teoria_colas(),
            "motivo_fin": self.motivo_fin,
        }

    @staticmethod
    def _percentil(valores: list[float], q: float) -> float:
        if not valores:
            return 0.0
        ordenados = sorted(valores)
        indice = min(len(ordenados) - 1, max(0, int(math.ceil(q * len(ordenados))) - 1))
        return ordenados[indice]

    def instantanea(self) -> dict:
        """Copia serializable y consistente del estado actual."""
        return {
            "configuracion": self.config.a_diccionario(),
            "metricas": self.metricas(),
            "topologia": self.topologia.instantanea(),
            "rutas": {
                destino: {
                    "camino": list(info["camino"]),
                    "latencia": (
                        info["latencia"] if math.isfinite(info["latencia"]) else None
                    ),
                    "disponible": info["disponible"],
                    "actualizada_en": info["actualizada_en"],
                    "iteraciones": info["iteraciones"],
                    "relajaciones": info["relajaciones"],
                }
                for destino, info in self.rutas_actuales.items()
            },
            "servidores": [
                servidor.instantanea() for servidor in self.servidores.values()
            ],
            "energia": {
                **self.asignacion_energia.como_diccionario(),
                "nombres_cluster": list(self.optimizador_energia.nombres_cluster),
                "demanda": list(self.demanda_clusters),
                "actualizada_en": self.ultima_actualizacion_energia,
            },
            "modelo_termico": self.modelo_temperatura.instantanea(),
            "historial": list(self.historial[-240:]),
            "alertas": list(self.alertas),
            "ultimo_cambio_ruta": self.ultimo_cambio_ruta,
        }

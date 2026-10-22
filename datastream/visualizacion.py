"""Dashboard Pygame del simulador DataStream Networks.

La interfaz consume el estado del motor SimPy sin ejecutar eventos propios:
el bucle principal avanza el reloj simulado y luego dibuja la topología, los
paquetes, las métricas y los gráficos de gradiente/Lagrange.

Controles:
    ESPACIO / P : pausa
    1, 2, 3, 4  : velocidad 0.5x, 1x, 2x, 4x
    F5 / R      : generar informe PDF / reiniciar simulación
    F6          : activar o desactivar el análisis por IA
    ESC / Q     : salir
    Clic        : enfocar la ruta de un servidor
"""

from __future__ import annotations

import math
import os
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

try:
    import pygame
except ImportError as exc:  # pragma: no cover - depende del entorno del usuario
    raise ImportError(
        "Pygame no está instalado. Ejecute: pip install -r requirements.txt"
    ) from exc

from .configuracion import Configuracion
from .ia import ClienteIA, ErrorIA, ResultadoIA, generar_resumen_local
from .reporte import generar_reporte_pdf
from .simulador import Paquete, SimuladorDatacenter


class ErrorVisualizacion(RuntimeError):
    """Error al iniciar o mantener el dashboard."""


ANCHO = 1440
ALTO = 900
FPS = 60

COLOR_FONDO = (8, 16, 29)
COLOR_PANEL = (15, 27, 44)
COLOR_PANEL_ALT = (19, 34, 54)
COLOR_BORDE = (35, 56, 80)
COLOR_TEXTO = (226, 237, 247)
COLOR_TEXTO_SUAVE = (139, 164, 191)
COLOR_CIAN = (0, 211, 248)
COLOR_VERDE = (48, 218, 151)
COLOR_AMBAR = (255, 185, 63)
COLOR_ROJO = (255, 82, 104)
COLOR_MORADO = (169, 129, 255)
COLOR_AZUL = (83, 148, 255)

COLORES_CLUSTER = {
    "Cómputo": COLOR_CIAN,
    "Almacenamiento": COLOR_VERDE,
    "Red": COLOR_AMBAR,
}
COLORES_PRIORIDAD = {
    1: (104, 213, 255),
    2: (82, 229, 169),
    3: (255, 213, 91),
    4: (255, 146, 76),
    5: (255, 90, 112),
}
FUENTES_DISPONIBLES = "dejavusans,liberationsans,freesans,arial"


class Dashboard:
    """Ventana principal y presentación de métricas en tiempo real."""

    def __init__(
        self,
        simulador: SimuladorDatacenter,
        config: Configuracion,
        cliente_ia: ClienteIA | None = None,
        usar_ia: bool = False,
        directorio_reportes: str | Path = "reportes",
    ) -> None:
        self.simulador = simulador
        self.config = config
        self.cliente_ia = cliente_ia
        self.usar_ia = bool(usar_ia and cliente_ia is not None)
        self.directorio_reportes = Path(directorio_reportes)
        self.directorio_reportes.mkdir(parents=True, exist_ok=True)

        self.pausado = False
        self.velocidades = (0.5, 1.0, 2.0, 4.0)
        self.indice_velocidad = 1
        self.foco_servidor = "S1"
        self.ultimo_mensaje = ""
        self.ultimo_mensaje_en = 0.0
        self._reporte_cola: queue.Queue[tuple[str, str]] = queue.Queue()
        self._generando_reporte = False
        self._rects_servidores: dict[str, pygame.Rect] = {}
        self._rects_botones: dict[str, pygame.Rect] = {}
        self._fuentes: dict[tuple[int, bool], pygame.font.Font] = {}
        self._glow_cache: dict[tuple[int, int, int], pygame.Surface] = {}
        self._reloj = pygame.time.Clock()
        self._iniciar_pygame()
        self._actualizar_rects_servidores()

    # ------------------------------------------------------------------
    # Ciclo principal
    # ------------------------------------------------------------------
    def _iniciar_pygame(self) -> None:
        try:
            pygame.init()
            pygame.font.init()
            self.pantalla = pygame.display.set_mode((ANCHO, ALTO))
            pygame.display.set_caption(
                "DataStream Networks · Simulador de optimización de centros de datos"
            )
        except pygame.error as exc:
            raise ErrorVisualizacion(
                "No se pudo abrir la ventana Pygame. En servidores sin pantalla "
                "use el modo consola: python main.py --sin-ui"
            ) from exc

    def ejecutar(self) -> None:
        """Bucle interactivo hasta que el usuario cierra la ventana."""
        en_ejecucion = True
        while en_ejecucion:
            dt_real = min(0.06, self._reloj.tick(FPS) / 1000.0)
            for evento in pygame.event.get():
                if evento.type == pygame.QUIT:
                    en_ejecucion = False
                elif evento.type == pygame.KEYDOWN:
                    en_ejecucion = self._procesar_tecla(evento.key)
                elif evento.type == pygame.MOUSEBUTTONDOWN and evento.button == 1:
                    self._procesar_clic(evento.pos)
            self._actualizar(dt_real)
            self.dibujar()
            pygame.display.flip()
        pygame.quit()

    def _procesar_tecla(self, tecla: int) -> bool:
        if tecla in (pygame.K_ESCAPE, pygame.K_q):
            return False
        if tecla in (pygame.K_SPACE, pygame.K_p):
            self.pausado = not self.pausado
            self._avisar("Simulación en pausa" if self.pausado else "Simulación reanudada")
        elif tecla in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4):
            self.indice_velocidad = tecla - pygame.K_1
            self._avisar(f"Velocidad: {self.velocidades[self.indice_velocidad]:g}x")
        elif tecla == pygame.K_F5:
            self._solicitar_reporte()
        elif tecla == pygame.K_F6:
            self.usar_ia = not self.usar_ia
            if self.usar_ia and (
                self.cliente_ia is None or not self.cliente_ia.disponible
            ):
                self._avisar("IA sin clave/endpoint: el PDF usará análisis local")
            else:
                self._avisar("Análisis IA activado" if self.usar_ia else "Análisis IA desactivado")
        elif tecla == pygame.K_r:
            self.reiniciar()
        elif tecla == pygame.K_TAB:
            self._ciclar_foco()
        return True

    def _procesar_clic(self, posicion: tuple[int, int]) -> None:
        for servidor_id, rect in self._rects_servidores.items():
            if rect.collidepoint(posicion):
                self.foco_servidor = servidor_id
                return
        for accion, rect in self._rects_botones.items():
            if rect.collidepoint(posicion):
                if accion == "pausa":
                    self.pausado = not self.pausado
                elif accion == "reporte":
                    self._solicitar_reporte()
                elif accion == "reiniciar":
                    self.reiniciar()
                elif accion == "ia":
                    self.usar_ia = not self.usar_ia
                return

    def _actualizar(self, dt_real: float) -> None:
        if not self.pausado and not self.simulador.terminada:
            velocidad = self.velocidades[self.indice_velocidad]
            self.simulador.avanzar(dt_real * velocidad)
        self._revisar_reporte()
        if self.simulador.terminada and self.ultimo_mensaje == "":
            self._avisar("Simulación finalizada. F5 genera el informe PDF.")

    def _avisar(self, mensaje: str, duracion: float = 5.0) -> None:
        self.ultimo_mensaje = mensaje
        self.ultimo_mensaje_en = pygame.time.get_ticks() / 1000.0
        # La duración se consulta al dibujar para evitar perder el mensaje.
        self._duracion_mensaje = duracion

    def _mensaje_visible(self) -> bool:
        if not self.ultimo_mensaje:
            return False
        transcurrido = pygame.time.get_ticks() / 1000.0 - self.ultimo_mensaje_en
        return transcurrido < getattr(self, "_duracion_mensaje", 5.0)

    def reiniciar(self) -> None:
        if self._generando_reporte:
            self._avisar("Espere a que termine el informe actual")
            return
        self.simulador = SimuladorDatacenter(self.config)
        self.pausado = False
        self.foco_servidor = "S1"
        self._avisar("Simulación reiniciada con la misma semilla")
        self._actualizar_rects_servidores()

    # ------------------------------------------------------------------
    # Informe PDF asíncrono
    # ------------------------------------------------------------------
    def _solicitar_reporte(self) -> None:
        if self._generando_reporte:
            self._avisar("Ya se está generando un informe")
            return
        self._generando_reporte = True
        self._avisar("Generando informe PDF...")
        instantanea = self.simulador.instantanea()
        usar_ia = self.usar_ia and self.cliente_ia is not None
        cliente = self.cliente_ia
        ruta = self.directorio_reportes / (
            "reporte_datastream_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".pdf"
        )

        def trabajo() -> None:
            try:
                if usar_ia and cliente is not None:
                    try:
                        analisis = cliente.generar_analisis(instantanea)
                    except ErrorIA as exc:
                        analisis = ResultadoIA(
                            texto=generar_resumen_local(instantanea).texto,
                            fuente="Análisis determinista local",
                            error=str(exc),
                        )
                else:
                    analisis = generar_resumen_local(instantanea)
                ruta_final = generar_reporte_pdf(instantanea, analisis, ruta)
                self._reporte_cola.put(("ok", str(ruta_final)))
            except Exception as exc:  # el hilo no debe tumbar la UI
                self._reporte_cola.put(("error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=trabajo, daemon=True, name="reporte-pdf").start()

    def _revisar_reporte(self) -> None:
        try:
            estado, detalle = self._reporte_cola.get_nowait()
        except queue.Empty:
            return
        self._generando_reporte = False
        if estado == "ok":
            self._avisar(f"Informe generado: {detalle}", 9.0)
        else:
            self._avisar(f"Error al generar PDF: {detalle}", 9.0)

    # ------------------------------------------------------------------
    # Utilidades gráficas
    # ------------------------------------------------------------------
    def _fuente(self, tamano: int, negrita: bool = False) -> pygame.font.Font:
        clave = (tamano, negrita)
        if clave not in self._fuentes:
            self._fuentes[clave] = pygame.font.SysFont(
                FUENTES_DISPONIBLES, tamano, bold=negrita
            )
        return self._fuentes[clave]

    def _texto(
        self,
        superficie: pygame.Surface,
        texto: str,
        x: float,
        y: float,
        tamano: int = 12,
        color: tuple[int, int, int] = COLOR_TEXTO,
        negrita: bool = False,
        centrado: bool = False,
        derecha: bool = False,
    ) -> None:
        imagen = self._fuente(tamano, negrita).render(texto, True, color)
        rect = imagen.get_rect()
        if centrado:
            rect.midtop = (int(x), int(y))
        elif derecha:
            rect.topright = (int(x), int(y))
        else:
            rect.topleft = (int(x), int(y))
        superficie.blit(imagen, rect)

    def _panel(
        self,
        superficie: pygame.Surface,
        rect: pygame.Rect,
        color: tuple[int, int, int] = COLOR_PANEL,
        borde: tuple[int, int, int] = COLOR_BORDE,
        radio: int = 10,
    ) -> None:
        pygame.draw.rect(superficie, color, rect, border_radius=radio)
        pygame.draw.rect(superficie, borde, rect, width=1, border_radius=radio)

    def _barra(
        self,
        superficie: pygame.Surface,
        x: int,
        y: int,
        ancho: int,
        alto: int,
        fraccion: float,
        color: tuple[int, int, int],
        fondo: tuple[int, int, int] = (28, 44, 64),
    ) -> None:
        fraccion = max(0.0, min(1.0, fraccion))
        pygame.draw.rect(superficie, fondo, (x, y, ancho, alto), border_radius=alto // 2)
        if fraccion > 0:
            pygame.draw.rect(
                superficie,
                color,
                (x, y, max(2, int(ancho * fraccion)), alto),
                border_radius=alto // 2,
            )

    def _glow(self, radio: int, color: tuple[int, int, int]) -> pygame.Surface:
        clave = (radio, *color)
        if clave not in self._glow_cache:
            superficie = pygame.Surface((radio * 2, radio * 2), pygame.SRCALPHA)
            for escala in range(radio, 0, -1):
                alfa = int(110 * (1.0 - escala / radio) ** 1.4)
                pygame.draw.circle(
                    superficie,
                    (*color, alfa),
                    (radio, radio),
                    escala,
                )
            self._glow_cache[clave] = superficie
        return self._glow_cache[clave]

    # ------------------------------------------------------------------
    # Dibujo general
    # ------------------------------------------------------------------
    def dibujar(self) -> None:
        self.pantalla.fill(COLOR_FONDO)
        self._dibujar_fondo()
        self._dibujar_cabecera()
        rect_topo = pygame.Rect(18, 84, 906, 544)
        rect_control = pygame.Rect(938, 84, 484, 544)
        self._dibujar_topologia(rect_topo)
        self._dibujar_control(rect_control)
        self._dibujar_graficos()
        self._dibujar_pie()
        if self._generando_reporte:
            self._dibujar_velo("Generando informe PDF...")
        elif self.pausado:
            self._dibujar_velo("PAUSA")
        if self._mensaje_visible():
            self._dibujar_toast(self.ultimo_mensaje)

    def _dibujar_fondo(self) -> None:
        # Rejilla tenue para dar profundidad sin distraer de los datos.
        for x in range(0, ANCHO, 40):
            pygame.draw.line(self.pantalla, (11, 22, 38), (x, 0), (x, ALTO))
        for y in range(0, ALTO, 40):
            pygame.draw.line(self.pantalla, (11, 22, 38), (0, y), (ANCHO, y))
        pygame.draw.circle(self.pantalla, (14, 31, 50), (ANCHO - 110, 42), 220)

    def _dibujar_cabecera(self) -> None:
        pygame.draw.rect(self.pantalla, (10, 21, 36), (0, 0, ANCHO, 68))
        pygame.draw.line(self.pantalla, COLOR_BORDE, (0, 68), (ANCHO, 68), 1)
        pygame.draw.circle(self.pantalla, COLOR_CIAN, (34, 34), 17, width=2)
        pygame.draw.circle(self.pantalla, (0, 211, 248, 40), (34, 34), 11)
        self._texto(self.pantalla, "DS", 24, 22, 13, COLOR_CIAN, True)
        self._texto(self.pantalla, "DATASTREAM NETWORKS", 61, 12, 17, COLOR_TEXTO, True)
        self._texto(
            self.pantalla,
            "Simulador de optimización · SimPy + Pygame",
            62,
            36,
            10,
            COLOR_TEXTO_SUAVE,
        )

        # Estado del reloj y velocidad.
        ahora = self.simulador.env.now
        duracion = self.config.duracion
        if self.simulador.terminada:
            estado = "FINALIZADA"
            color_estado = COLOR_AMBAR
        elif self.pausado:
            estado = "PAUSA"
            color_estado = COLOR_AMBAR
        else:
            estado = "EN EJECUCIÓN"
            color_estado = COLOR_VERDE
        pygame.draw.circle(self.pantalla, color_estado, (1090, 24), 5)
        self._texto(self.pantalla, estado, 1101, 16, 10, color_estado, True)
        self._texto(
            self.pantalla,
            f"T+ {int(ahora // 60):02d}:{ahora % 60:04.1f} / "
            f"{int(duracion // 60):02d}:{duracion % 60:04.1f}",
            1090,
            38,
            10,
            COLOR_TEXTO_SUAVE,
        )
        self._texto(
            self.pantalla,
            f"{self.velocidades[self.indice_velocidad]:g}x",
            1398,
            18,
            16,
            COLOR_CIAN,
            True,
            derecha=True,
        )
        self._texto(self.pantalla, "velocidad", 1398, 41, 9, COLOR_TEXTO_SUAVE, derecha=True)
        # Botones mínimos de cabecera.
        self._rects_botones["pausa"] = pygame.Rect(850, 18, 72, 30)
        self._rects_botones["reporte"] = pygame.Rect(930, 18, 66, 30)
        self._rects_botones["ia"] = pygame.Rect(1004, 18, 54, 30)
        self._boton("pausa", "Pausa" if not self.pausado else "Seguir")
        self._boton("reporte", "PDF")
        self._boton("ia", "IA" if self.usar_ia else "IA off", activo=self.usar_ia)

    def _boton(self, accion: str, etiqueta: str, activo: bool = False) -> None:
        rect = self._rects_botones.get(accion)
        if not rect:
            return
        color = COLOR_CIAN if activo else COLOR_PANEL_ALT
        borde = COLOR_CIAN if activo else COLOR_BORDE
        pygame.draw.rect(self.pantalla, color, rect, border_radius=6)
        pygame.draw.rect(self.pantalla, borde, rect, width=1, border_radius=6)
        self._texto(
            self.pantalla,
            etiqueta,
            rect.centerx,
            rect.y + 7,
            9,
            COLOR_FONDO if activo else COLOR_TEXTO,
            True,
            centrado=True,
        )

    # ------------------------------------------------------------------
    # Topología
    # ------------------------------------------------------------------
    def _rect_mapa(self, rect: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(rect.x + 38, rect.y + 58, rect.width - 62, rect.height - 98)

    def _posicion_nodo(self, nodo: dict[str, Any], rect: pygame.Rect) -> tuple[int, int]:
        area = self._rect_mapa(rect)
        return (
            int(area.x + nodo["x"] / 1000.0 * area.width),
            int(area.y + nodo["y"] / 700.0 * area.height),
        )

    def _actualizar_rects_servidores(self) -> None:
        rect = pygame.Rect(18, 84, 906, 544)
        self._rects_servidores = {}
        for nodo in self.simulador.topologia.nodos.values():
            if nodo.tipo == "servidor":
                x, y = self._posicion_nodo(
                    {"x": nodo.x, "y": nodo.y}, rect
                )
                self._rects_servidores[nodo.id] = pygame.Rect(x - 22, y - 22, 44, 44)

    def _foco_disponible(self) -> str:
        if self.foco_servidor in self.simulador.rutas_actuales:
            return self.foco_servidor
        disponibles = [
            sid
            for sid, info in self.simulador.rutas_actuales.items()
            if info.get("disponible")
        ]
        if disponibles:
            self.foco_servidor = disponibles[0]
        return self.foco_servidor

    def _dibujar_topologia(self, rect: pygame.Rect) -> None:
        self._panel(self.pantalla, rect)
        self._texto(
            self.pantalla,
            "TOPOLOGÍA Y TRÁFICO EN TIEMPO REAL",
            rect.x + 16,
            rect.y + 14,
            12,
            COLOR_TEXTO,
            True,
        )
        self._texto(
            self.pantalla,
            "Clic en un servidor para enfocar su ruta óptima",
            rect.x + 16,
            rect.y + 32,
            9,
            COLOR_TEXTO_SUAVE,
        )
        area = self._rect_mapa(rect)
        for x in range(area.x, area.right, 32):
            for y in range(area.y, area.bottom, 32):
                pygame.draw.circle(self.pantalla, (23, 38, 56), (x, y), 1)

        nodos = {nodo["id"]: nodo for nodo in self.simulador.topologia.instantanea()["nodos"]}
        posiciones = {
            nodo_id: self._posicion_nodo(nodo, rect) for nodo_id, nodo in nodos.items()
        }
        self._rects_servidores = {}
        for nodo_id, nodo in nodos.items():
            if nodo["tipo"] == "servidor":
                x, y = posiciones[nodo_id]
                self._rects_servidores[nodo_id] = pygame.Rect(x - 24, y - 24, 48, 48)

        foco = self._foco_disponible()
        ruta_foco = self.simulador.rutas_actuales.get(foco, {}).get("camino", [])
        # Enlaces base. El ancho y color representan utilización.
        for enlace in self.simulador.topologia.enlaces:
            p1, p2 = posiciones.get(enlace.origen), posiciones.get(enlace.destino)
            if not p1 or not p2:
                continue
            disponible = self.simulador.topologia.enlace_disponible(enlace)
            util = enlace.utilizacion
            if not disponible:
                pygame.draw.line(self.pantalla, (55, 70, 88), p1, p2, 2)
            else:
                if util < 0.5:
                    color = COLOR_VERDE
                elif util < 0.85:
                    color = COLOR_AMBAR
                else:
                    color = COLOR_ROJO
                grosor = 2 + min(3, int(util * 2.5))
                pygame.draw.line(self.pantalla, color, p1, p2, grosor)
            clave = (enlace.origen, enlace.destino)
            if self._enlace_en_camino(clave, ruta_foco):
                pygame.draw.line(self.pantalla, COLOR_CIAN, p1, p2, 5)
                pygame.draw.line(self.pantalla, (210, 250, 255), p1, p2, 1)

        # Ruta enfocada: pulsos móviles que refuerzan el concepto de tráfico.
        if len(ruta_foco) >= 2:
            self._dibujar_pulsos_ruta(ruta_foco, posiciones)
        # Paquetes reales en tránsito.
        self._dibujar_paquetes(rect, posiciones)

        # Nodos.
        for nodo_id, nodo in nodos.items():
            x, y = posiciones[nodo_id]
            if nodo["tipo"] == "servidor":
                self._dibujar_servidor(nodo_id, nodo, x, y, rect)
            elif nodo["tipo"] == "router":
                self._dibujar_router(nodo_id, nodo, x, y)
            else:
                self._dibujar_origen(nodo, x, y)

        # Leyenda y etiqueta de ruta.
        self._dibujar_leyenda(rect)
        self._dibujar_etiqueta_ruta(rect, foco)

        # Aviso de cambio de ruta por congestión.
        ultimo = self.simulador.ultimo_cambio_ruta
        if ultimo:
            edad = self.simulador.env.now - float(ultimo.get("tiempo", 0))
            if 0 <= edad <= 3.2:
                self._dibujar_aviso_ruta(rect, ultimo, edad)

    @staticmethod
    def _enlace_en_camino(
        clave: tuple[str, str], camino: list[str]
    ) -> bool:
        if len(camino) < 2:
            return False
        for origen, destino in zip(camino, camino[1:]):
            if tuple(sorted((origen, destino))) == tuple(sorted(clave)):
                return True
        return False

    def _dibujar_pulsos_ruta(
        self, camino: list[str], posiciones: dict[str, tuple[int, int]]
    ) -> None:
        if len(camino) < 2:
            return
        puntos = [posiciones[nodo] for nodo in camino if nodo in posiciones]
        segmentos = list(zip(puntos, puntos[1:]))
        longitudes = [math.dist(a, b) for a, b in segmentos]
        total = sum(longitudes)
        if total <= 0:
            return
        fase = (self.simulador.env.now * 0.28) % 1.0
        for pulso in range(3):
            fraccion = (fase + pulso / 3.0) % 1.0
            objetivo = fraccion * total
            acumulado = 0.0
            for (a, b), longitud in zip(segmentos, longitudes):
                if acumulado + longitud >= objetivo and longitud > 0:
                    t = (objetivo - acumulado) / longitud
                    x = a[0] + (b[0] - a[0]) * t
                    y = a[1] + (b[1] - a[1]) * t
                    glow = self._glow(12, COLOR_CIAN)
                    self.pantalla.blit(glow, (int(x) - 12, int(y) - 12))
                    pygame.draw.circle(self.pantalla, (230, 252, 255), (int(x), int(y)), 3)
                    break
                acumulado += longitud

    def _dibujar_paquetes(
        self, rect: pygame.Rect, posiciones: dict[str, tuple[int, int]]
    ) -> None:
        ahora = self.simulador.env.now
        visibles = 0
        for paquete in self.simulador.paquetes_activos.values():
            if paquete.estado != "en_viaje" or paquete.enlace_indice < 0:
                continue
            if paquete.enlace_indice >= len(paquete.camino) - 1:
                continue
            origen = posiciones.get(paquete.camino[paquete.enlace_indice])
            destino = posiciones.get(paquete.camino[paquete.enlace_indice + 1])
            if not origen or not destino:
                continue
            duracion = max(1e-6, paquete.fin_tramo - paquete.inicio_tramo)
            progreso = (ahora - paquete.inicio_tramo) / duracion
            progreso = max(0.0, min(1.0, progreso))
            x = origen[0] + (destino[0] - origen[0]) * progreso
            y = origen[1] + (destino[1] - origen[1]) * progreso
            color = COLORES_PRIORIDAD.get(paquete.prioridad, COLOR_CIAN)
            glow = self._glow(9, color)
            self.pantalla.blit(glow, (int(x) - 9, int(y) - 9))
            pygame.draw.circle(self.pantalla, color, (int(x), int(y)), 4)
            pygame.draw.circle(self.pantalla, (240, 252, 255), (int(x), int(y)), 1)
            visibles += 1
            if visibles >= 120:
                break

    def _dibujar_servidor(
        self, servidor_id: str, nodo: dict[str, Any], x: int, y: int, rect: pygame.Rect
    ) -> None:
        servidor = self.simulador.servidores[servidor_id]
        color = COLORES_CLUSTER.get(servidor.cluster, COLOR_CIAN)
        activo = servidor.operativo
        if activo:
            glow = self._glow(30, color)
            self.pantalla.blit(glow, (x - 30, y - 30))
        pygame.draw.circle(
            self.pantalla,
            (18, 34, 52) if activo else (33, 35, 40),
            (x, y),
            25,
        )
        pygame.draw.circle(
            self.pantalla,
            color if activo else COLOR_ROJO,
            (x, y),
            25,
            width=2,
        )
        pygame.draw.arc(
            self.pantalla,
            color if activo else COLOR_ROJO,
            pygame.Rect(x - 20, y - 20, 40, 40),
            -math.pi / 2,
            -math.pi / 2 + 2 * math.pi * min(1.0, servidor.carga),
            3,
        )
        self._texto(self.pantalla, servidor_id, x, y - 9, 11, COLOR_TEXTO, True, centrado=True)
        self._texto(
            self.pantalla,
            f"{servidor.temperatura:.0f}°C",
            x,
            y + 4,
            8,
            COLOR_TEXTO_SUAVE,
            centrado=True,
        )
        self._texto(
            self.pantalla,
            servidor.cluster[:10],
            x,
            y + 29,
            8,
            color,
            True,
            centrado=True,
        )
        if self.foco_servidor == servidor_id:
            pygame.draw.circle(self.pantalla, COLOR_CIAN, (x, y), 31, width=1)
        if not activo:
            self._texto(self.pantalla, "OFFLINE", x, y + 42, 7, COLOR_ROJO, True, centrado=True)

    def _dibujar_router(self, nodo_id: str, nodo: dict[str, Any], x: int, y: int) -> None:
        activo = self.simulador.topologia.nodo_operativo(nodo_id)
        color = COLOR_AZUL if activo else COLOR_ROJO
        radio = 17
        puntos = [
            (x, y - radio),
            (x + radio * 0.86, y - radio * 0.5),
            (x + radio * 0.86, y + radio * 0.5),
            (x, y + radio),
            (x - radio * 0.86, y + radio * 0.5),
            (x - radio * 0.86, y - radio * 0.5),
        ]
        pygame.draw.polygon(self.pantalla, (20, 38, 63), puntos)
        pygame.draw.polygon(self.pantalla, color, puntos, width=2)
        self._texto(self.pantalla, nodo_id, x, y - 8, 10, COLOR_TEXTO, True, centrado=True)
        self._texto(self.pantalla, "router", x, y + 18, 7, COLOR_TEXTO_SUAVE, centrado=True)

    def _dibujar_origen(self, nodo: dict[str, Any], x: int, y: int) -> None:
        pygame.draw.circle(self.pantalla, (20, 48, 63), (x, y), 27)
        pygame.draw.circle(self.pantalla, COLOR_VERDE, (x, y), 27, width=2)
        self._texto(self.pantalla, "IN", x, y - 8, 11, COLOR_VERDE, True, centrado=True)
        self._texto(self.pantalla, "Internet", x, y + 31, 8, COLOR_TEXTO_SUAVE, centrado=True)

    def _dibujar_leyenda(self, rect: pygame.Rect) -> None:
        base_y = rect.bottom - 25
        elementos = [
            ("libre", COLOR_VERDE),
            ("congestión", COLOR_AMBAR),
            ("saturado", COLOR_ROJO),
            ("ruta óptima", COLOR_CIAN),
        ]
        x = rect.x + 18
        for etiqueta, color in elementos:
            pygame.draw.circle(self.pantalla, color, (x, base_y + 4), 3)
            self._texto(self.pantalla, etiqueta, x + 8, base_y - 3, 8, COLOR_TEXTO_SUAVE)
            x += 94

    def _dibujar_etiqueta_ruta(self, rect: pygame.Rect, foco: str) -> None:
        info = self.simulador.rutas_actuales.get(foco, {})
        camino = info.get("camino", [])
        latencia = info.get("latencia")
        if info.get("disponible") and camino:
            texto = " → ".join(camino)
            detalle = f"{latencia:.2f} s" if latencia is not None else ""
        else:
            texto = "Sin ruta disponible"
            detalle = ""
        etiqueta = f"RUTA ÓPTIMA A {foco}: {texto}   {detalle}"
        imagen = self._fuente(9, True).render(etiqueta, True, COLOR_CIAN)
        caja = pygame.Rect(rect.right - imagen.get_width() - 34, rect.y + 12, imagen.get_width() + 20, 24)
        pygame.draw.rect(self.pantalla, (12, 38, 55), caja, border_radius=6)
        pygame.draw.rect(self.pantalla, (20, 82, 105), caja, width=1, border_radius=6)
        self._texto(
            self.pantalla,
            etiqueta,
            caja.x + 10,
            caja.y + 6,
            9,
            COLOR_CIAN,
            True,
        )

    def _dibujar_aviso_ruta(
        self, rect: pygame.Rect, ultimo: dict[str, Any], edad: float
    ) -> None:
        # El aviso parpadea suavemente al aparecer y se desvanece a los 3 s.
        pulso = 0.62 + 0.38 * abs(math.sin(edad * 5.5))
        alpha = int(255 * pulso * max(0.0, 1.0 - max(0.0, edad - 2.2) / 1.0))
        superficie = pygame.Surface((rect.width - 32, 38), pygame.SRCALPHA)
        pygame.draw.rect(superficie, (70, 45, 10, max(60, alpha // 2)), superficie.get_rect(), border_radius=8)
        pygame.draw.rect(
            superficie,
            (*COLOR_AMBAR, max(100, alpha)),
            superficie.get_rect(),
            width=1,
            border_radius=8,
        )
        self.pantalla.blit(superficie, (rect.x + 16, rect.bottom - 76))
        ruta_nueva = " → ".join(ultimo.get("nueva", []))
        self._texto(
            self.pantalla,
            f"RUTA ACTUALIZADA POR CONGESTIÓN · {ultimo.get('destino')}: {ruta_nueva}",
            rect.x + 28,
            rect.bottom - 66,
            9,
            COLOR_AMBAR,
            True,
        )

    # ------------------------------------------------------------------
    # Panel derecho
    # ------------------------------------------------------------------
    def _dibujar_control(self, rect: pygame.Rect) -> None:
        self._panel(self.pantalla, rect)
        self._texto(
            self.pantalla,
            "CENTRO DE CONTROL",
            rect.x + 16,
            rect.y + 14,
            12,
            COLOR_TEXTO,
            True,
        )
        self._texto(
            self.pantalla,
            "Métricas en vivo e indicadores del simulador",
            rect.x + 16,
            rect.y + 32,
            9,
            COLOR_TEXTO_SUAVE,
        )
        sim = self.simulador
        generados = max(1, sim.paquetes_generados)
        aceptacion = sim.paquetes_aceptados / generados
        latencia = sim.suma_latencia / max(1, sim.paquetes_procesados)
        throughput = sim.paquetes_procesados / max(1e-9, sim.env.now)
        tarjetas = [
            ("PROCESADOS", f"{sim.paquetes_procesados}", f"{throughput:.2f} paq/s", COLOR_CIAN),
            ("ACEPTACIÓN", f"{aceptacion * 100:.0f} %", f"{sim.paquetes_aceptados} aceptados", COLOR_VERDE),
            ("LATENCIA MEDIA", f"{latencia:.1f} s", "extremo a extremo", COLOR_AMBAR),
            ("DESCARTADOS", f"{sim.paquetes_descartados}", "política de admisión", COLOR_ROJO),
        ]
        ancho = (rect.width - 44) // 2
        for indice, (titulo, valor, detalle, color) in enumerate(tarjetas):
            columna = indice % 2
            fila = indice // 2
            x = rect.x + 16 + columna * (ancho + 8)
            y = rect.y + 60 + fila * 62
            caja = pygame.Rect(x, y, ancho, 54)
            self._panel(self.pantalla, caja, COLOR_PANEL_ALT, (30, 53, 75), 7)
            pygame.draw.rect(self.pantalla, color, (x, y + 9, 2, 36), border_radius=1)
            self._texto(self.pantalla, titulo, x + 9, y + 8, 7, COLOR_TEXTO_SUAVE, True)
            self._texto(self.pantalla, valor, x + 9, y + 19, 16, color, True)
            self._texto(self.pantalla, detalle, x + 9, y + 39, 7, COLOR_TEXTO_SUAVE)

        # Servidores con carga, temperatura y ventilador.
        y = rect.y + 194
        self._texto(self.pantalla, "SERVIDORES", rect.x + 16, y, 9, COLOR_TEXTO_SUAVE, True)
        self._texto(
            self.pantalla,
            f"{sim.fallas_totales} fallas · {sim.emergencias} emergencias",
            rect.right - 16,
            y,
            8,
            COLOR_TEXTO_SUAVE,
            derecha=True,
        )
        for servidor in sim.servidores.values():
            y += 27
            color = COLORES_CLUSTER.get(servidor.cluster, COLOR_CIAN)
            pygame.draw.circle(
                self.pantalla,
                color if servidor.operativo else COLOR_ROJO,
                (rect.x + 22, y + 8),
                3,
            )
            self._texto(
                self.pantalla,
                servidor.id,
                rect.x + 32,
                y,
                9,
                COLOR_TEXTO,
                True,
            )
            self._texto(
                self.pantalla,
                f"{servidor.temperatura:.1f} °C",
                rect.x + 64,
                y,
                8,
                COLOR_TEXTO_SUAVE,
            )
            self._texto(
                self.pantalla,
                f"fan {servidor.ventilador * 100:.0f}%",
                rect.x + 144,
                y,
                8,
                COLOR_CIAN if servidor.ventilador > 0.65 else COLOR_TEXTO_SUAVE,
            )
            self._barra(
                self.pantalla,
                rect.x + 222,
                y + 2,
                rect.width - 260,
                7,
                servidor.carga,
                color if servidor.operativo else COLOR_ROJO,
            )
            self._texto(
                self.pantalla,
                f"{servidor.carga * 100:.0f}%",
                rect.right - 24,
                y,
                8,
                COLOR_TEXTO_SUAVE,
                derecha=True,
            )

        # Energía y alertas.
        y = rect.y + 332
        self._texto(self.pantalla, "LAGRANGE", rect.x + 16, y, 9, COLOR_TEXTO_SUAVE, True)
        self._texto(
            self.pantalla,
            f"λ = {sim.asignacion_energia.multiplicador_lagrange:.4f}",
            rect.x + 16,
            y + 17,
            11,
            COLOR_MORADO,
            True,
        )
        self._texto(
            self.pantalla,
            f"Rendimiento {sim.asignacion_energia.rendimiento_total:.1f}",
            rect.x + 155,
            y + 18,
            8,
            COLOR_TEXTO_SUAVE,
        )
        self._texto(
            self.pantalla,
            f"Presupuesto {self.config.presupuesto_energia:.0f} kW",
            rect.right - 16,
            y + 18,
            8,
            COLOR_TEXTO_SUAVE,
            derecha=True,
        )

        y = rect.y + 382
        self._texto(self.pantalla, "EVENTOS RECIENTES", rect.x + 16, y, 9, COLOR_TEXTO_SUAVE, True)
        alertas = list(sim.alertas)[-4:][::-1]
        if not alertas:
            self._texto(self.pantalla, "Sin eventos todavía", rect.x + 16, y + 20, 9, COLOR_TEXTO_SUAVE)
        for indice, alerta in enumerate(alertas):
            fila_y = y + 21 + indice * 29
            color = {
                "alta": COLOR_ROJO,
                "media": COLOR_AMBAR,
                "info": COLOR_CIAN,
            }.get(alerta.get("severidad", "info"), COLOR_CIAN)
            pygame.draw.circle(self.pantalla, color, (rect.x + 21, fila_y + 7), 3)
            self._texto(
                self.pantalla,
                f"t={float(alerta.get('tiempo', 0)):05.1f}",
                rect.x + 32,
                fila_y,
                7,
                COLOR_TEXTO_SUAVE,
            )
            mensaje = str(alerta.get("mensaje", ""))
            if len(mensaje) > 54:
                mensaje = mensaje[:51] + "..."
            self._texto(self.pantalla, mensaje, rect.x + 82, fila_y, 8, COLOR_TEXTO)
        # Indicador de modo de reporte.
        self._texto(
            self.pantalla,
            f"Informe: {'IA + PDF' if self.usar_ia else 'PDF local'}",
            rect.x + 16,
            rect.bottom - 25,
            8,
            COLOR_TEXTO_SUAVE,
        )
        self._texto(
            self.pantalla,
            "F5 generar · F6 IA",
            rect.right - 16,
            rect.bottom - 25,
            8,
            COLOR_TEXTO_SUAVE,
            derecha=True,
        )

    # ------------------------------------------------------------------
    # Gráficos inferiores
    # ------------------------------------------------------------------
    def _datos_historial(self, limite: int = 100) -> list[dict]:
        historial = self.simulador.historial[-limite:]
        if len(historial) > 1:
            return historial
        return historial

    def _dibujar_graficos(self) -> None:
        y = 644
        alto = 218
        ancho = 456
        self._grafico_gradiente(pygame.Rect(18, y, ancho, alto))
        self._grafico_lagrange(pygame.Rect(486, y, ancho, alto))
        self._grafico_trafico(pygame.Rect(954, y, 468, alto))

    def _marco_grafico(self, rect: pygame.Rect, titulo: str, detalle: str = "") -> pygame.Rect:
        self._panel(self.pantalla, rect)
        self._texto(self.pantalla, titulo, rect.x + 12, rect.y + 10, 9, COLOR_TEXTO, True)
        if detalle:
            self._texto(
                self.pantalla,
                detalle,
                rect.right - 12,
                rect.y + 11,
                8,
                COLOR_TEXTO_SUAVE,
                derecha=True,
            )
        return pygame.Rect(rect.x + 36, rect.y + 36, rect.width - 50, rect.height - 64)

    def _grafico_gradiente(self, rect: pygame.Rect) -> None:
        historial = self._datos_historial(90)
        area = self._marco_grafico(
            rect,
            "GRADIENTE · TEMPERATURA Y VENTILADOR",
            "minimización de energía",
        )
        temps = [float(fila.get("temperatura_media", 0)) for fila in historial]
        fans = [float(fila.get("ventilador_medio", 0)) * 100 for fila in historial]
        tiempos = [float(fila.get("tiempo", 0)) for fila in historial]
        if len(temps) < 2:
            self._texto(self.pantalla, "Recopilando datos...", area.x, area.y + 30, 9, COLOR_TEXTO_SUAVE)
            return
        # Rejilla.
        for i in range(4):
            yy = area.y + area.height * i / 3
            pygame.draw.line(self.pantalla, (30, 48, 66), (area.x, yy), (area.right, yy), 1)
            self._texto(
                self.pantalla,
                f"{max(20, 65 - 45 * i / 3):.0f}",
                rect.x + 9,
                yy - 5,
                7,
                COLOR_TEXTO_SUAVE,
            )
        puntos_temp = self._puntos_serie(temps, tiempos, area, 20.0, 65.0)
        puntos_fan = self._puntos_serie(fans, tiempos, area, 0.0, 100.0)
        if len(puntos_temp) > 1:
            pygame.draw.lines(self.pantalla, COLOR_AMBAR, False, puntos_temp, 2)
        if len(puntos_fan) > 1:
            pygame.draw.lines(self.pantalla, COLOR_CIAN, False, puntos_fan, 2)
        # Leyenda con valores actuales.
        pygame.draw.circle(self.pantalla, COLOR_AMBAR, (rect.x + 12, rect.bottom - 17), 3)
        self._texto(
            self.pantalla,
            f"Temp {temps[-1]:.1f} °C",
            rect.x + 19,
            rect.bottom - 23,
            8,
            COLOR_AMBAR,
        )
        pygame.draw.circle(self.pantalla, COLOR_CIAN, (rect.x + 120, rect.bottom - 17), 3)
        self._texto(
            self.pantalla,
            f"Fan {fans[-1] / 100:.2f}",
            rect.x + 127,
            rect.bottom - 23,
            8,
            COLOR_CIAN,
        )
        self._texto(
            self.pantalla,
            f"t={tiempos[0]:.0f}→{tiempos[-1]:.0f}s",
            rect.right - 12,
            rect.bottom - 23,
            7,
            COLOR_TEXTO_SUAVE,
            derecha=True,
        )

    def _grafico_lagrange(self, rect: pygame.Rect) -> None:
        historial = self._datos_historial(2)
        area = self._marco_grafico(
            rect,
            "LAGRANGE · DISTRIBUCIÓN DE ENERGÍA",
            f"λ = {self.simulador.asignacion_energia.multiplicador_lagrange:.4f}",
        )
        nombres = list(self.simulador.optimizador_energia.nombres_cluster)
        valores = list(self.simulador.asignacion_energia.asignacion)
        if not valores:
            return
        maximo = max(valores) * 1.25 or 1.0
        base_y = area.bottom - 16
        ancho_barra = min(58, int(area.width / (len(valores) * 1.85)))
        for indice, (nombre, valor) in enumerate(zip(nombres, valores)):
            color = list(COLORES_CLUSTER.values())[indice % 3]
            x = area.x + int(area.width * (indice + 0.5) / len(valores)) - ancho_barra // 2
            alto_barra = max(2, int((area.height - 35) * valor / maximo))
            pygame.draw.rect(
                self.pantalla,
                (26, 44, 64),
                (x - 2, base_y - area.height + 35, ancho_barra + 4, area.height - 35),
                border_radius=4,
            )
            pygame.draw.rect(
                self.pantalla,
                color,
                (x, base_y - alto_barra, ancho_barra, alto_barra),
                border_radius=4,
            )
            self._texto(
                self.pantalla,
                f"{valor:.1f}",
                x + ancho_barra // 2,
                base_y - alto_barra - 14,
                8,
                color,
                True,
                centrado=True,
            )
            self._texto(
                self.pantalla,
                nombre[:12],
                x + ancho_barra // 2,
                base_y + 4,
                7,
                COLOR_TEXTO_SUAVE,
                centrado=True,
            )
        # Aporte relativo de la optimización.
        if historial:
            fila = historial[-1]
            demanda = fila.get("demanda_clusters", [0, 0, 0])
            self._texto(
                self.pantalla,
                f"Demanda C/A/R: {demanda[0]:.2f} / {demanda[1]:.2f} / {demanda[2]:.2f}",
                rect.x + 12,
                rect.bottom - 20,
                7,
                COLOR_TEXTO_SUAVE,
            )

    def _grafico_trafico(self, rect: pygame.Rect) -> None:
        historial = self._datos_historial(90)
        area = self._marco_grafico(
            rect,
            "TRÁFICO Y COLAS",
            "llegadas vs. procesados",
        )
        if len(historial) < 2:
            self._texto(self.pantalla, "Recopilando datos...", area.x, area.y + 30, 9, COLOR_TEXTO_SUAVE)
            return
        llegadas = [float(fila.get("llegadas_por_s", 0)) for fila in historial]
        procesados = [float(fila.get("procesados_por_s", 0)) for fila in historial]
        maximos = max(1.0, max(llegadas + procesados) * 1.18)
        for i in range(4):
            yy = area.y + area.height * i / 3
            pygame.draw.line(self.pantalla, (30, 48, 66), (area.x, yy), (area.right, yy), 1)
            self._texto(
                self.pantalla,
                f"{maximos * (1 - i / 3):.1f}",
                rect.x + 8,
                yy - 5,
                7,
                COLOR_TEXTO_SUAVE,
            )
        puntos_l = self._puntos_serie(llegadas, [f["tiempo"] for f in historial], area, 0.0, maximos)
        puntos_p = self._puntos_serie(procesados, [f["tiempo"] for f in historial], area, 0.0, maximos)
        if len(puntos_l) > 1:
            pygame.draw.lines(self.pantalla, COLOR_AMBAR, False, puntos_l, 2)
        if len(puntos_p) > 1:
            pygame.draw.lines(self.pantalla, COLOR_VERDE, False, puntos_p, 2)
        pygame.draw.circle(self.pantalla, COLOR_AMBAR, (rect.x + 12, rect.bottom - 17), 3)
        self._texto(self.pantalla, f"Llegadas {llegadas[-1]:.1f}/s", rect.x + 19, rect.bottom - 23, 8, COLOR_AMBAR)
        pygame.draw.circle(self.pantalla, COLOR_VERDE, (rect.x + 130, rect.bottom - 17), 3)
        self._texto(self.pantalla, f"Procesados {procesados[-1]:.1f}/s", rect.x + 137, rect.bottom - 23, 8, COLOR_VERDE)
        self._texto(
            self.pantalla,
            f"activos {len(self.simulador.paquetes_activos)}",
            rect.right - 12,
            rect.bottom - 23,
            8,
            COLOR_TEXTO_SUAVE,
            derecha=True,
        )

    @staticmethod
    def _puntos_serie(
        valores: list[float],
        tiempos: list[float],
        area: pygame.Rect,
        minimo: float,
        maximo: float,
    ) -> list[tuple[int, int]]:
        if not valores:
            return []
        rango = max(1e-9, maximo - minimo)
        x0, x1 = tiempos[0], tiempos[-1]
        ancho_t = max(1e-9, x1 - x0)
        return [
            (
                int(area.x + (tiempo - x0) / ancho_t * area.width),
                int(area.bottom - max(0.0, min(1.0, (valor - minimo) / rango)) * area.height),
            )
            for tiempo, valor in zip(tiempos, valores)
        ]

    # ------------------------------------------------------------------
    # Pie, avisos y superposiciones
    # ------------------------------------------------------------------
    def _dibujar_pie(self) -> None:
        y = 874
        controles = (
            "[ESPACIO] Pausa   [1-4] Velocidad   [F5] Informe PDF   "
            "[F6] IA   [R] Reiniciar   [TAB] Enfocar   [ESC] Salir"
        )
        self._texto(self.pantalla, controles, 20, y, 9, COLOR_TEXTO_SUAVE)
        reportes = str(self.directorio_reportes.resolve())
        self._texto(
            self.pantalla,
            f"Reportes: {reportes}",
            ANCHO - 20,
            y,
            8,
            COLOR_TEXTO_SUAVE,
            derecha=True,
        )

    def _dibujar_velo(self, texto: str) -> None:
        velo = pygame.Surface((ANCHO, ALTO), pygame.SRCALPHA)
        velo.fill((4, 10, 18, 120))
        self.pantalla.blit(velo, (0, 0))
        imagen = self._fuente(30, True).render(texto, True, COLOR_CIAN)
        caja = pygame.Rect(
            (ANCHO - imagen.get_width()) // 2 - 34,
            (ALTO - imagen.get_height()) // 2 - 22,
            imagen.get_width() + 68,
            imagen.get_height() + 44,
        )
        pygame.draw.rect(self.pantalla, (12, 26, 42), caja, border_radius=12)
        pygame.draw.rect(self.pantalla, COLOR_CIAN, caja, width=1, border_radius=12)
        self.pantalla.blit(imagen, imagen.get_rect(center=caja.center))

    def _dibujar_toast(self, mensaje: str) -> None:
        fuente = self._fuente(10, True)
        imagen = fuente.render(mensaje, True, COLOR_TEXTO)
        ancho = min(1100, imagen.get_width() + 42)
        caja = pygame.Rect((ANCHO - ancho) // 2, 76, ancho, 32)
        pygame.draw.rect(self.pantalla, (12, 30, 48), caja, border_radius=8)
        pygame.draw.rect(self.pantalla, COLOR_CIAN, caja, width=1, border_radius=8)
        self.pantalla.blit(imagen, imagen.get_rect(center=caja.center))

    def _ciclar_foco(self) -> None:
        ids = [servidor_id for servidor_id in sorted(self.simulador.servidores)]
        if not ids:
            return
        try:
            indice = ids.index(self.foco_servidor)
        except ValueError:
            indice = -1
        self.foco_servidor = ids[(indice + 1) % len(ids)]


def ejecutar_dashboard(
    simulador: SimuladorDatacenter,
    config: Configuracion,
    cliente_ia: ClienteIA | None = None,
    usar_ia: bool = False,
    directorio_reportes: str | Path = "reportes",
) -> None:
    """Punto de entrada reutilizable para la interfaz gráfica."""
    dashboard = Dashboard(
        simulador=simulador,
        config=config,
        cliente_ia=cliente_ia,
        usar_ia=usar_ia,
        directorio_reportes=directorio_reportes,
    )
    dashboard.ejecutar()

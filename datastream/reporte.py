"""Generación de informes PDF autocontenidos (sin dependencias externas).

El documento se construye dibujando texto, tablas y gráficos vectoriales
directamente en el flujo PDF.  Esto garantiza que el reporte se pueda generar
en el mismo entorno en el que corre SimPy/Pygame, sin depender de LaTeX,
ReportLab o FPDF.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from .ia import ResultadoIA


class ErrorReporte(RuntimeError):
    """Error controlado durante la escritura del PDF."""


ANCHO_PAGINA = 595.28
ALTO_PAGINA = 841.89
MARGEN = 42.0
ANCHO_UTIL = ANCHO_PAGINA - 2 * MARGEN

AZUL = (0.055, 0.105, 0.180)
AZUL_MEDIO = (0.070, 0.255, 0.400)
AZUL_CLARO = (0.900, 0.955, 0.985)
CIAN = (0.000, 0.655, 0.780)
VERDE = (0.110, 0.650, 0.430)
AMBAR = (0.930, 0.560, 0.100)
ROJO = (0.800, 0.190, 0.230)
GRIS = (0.380, 0.430, 0.490)
GRIS_CLARO = (0.930, 0.945, 0.955)
BLANCO = (1.0, 1.0, 1.0)
NEGRO = (0.110, 0.140, 0.190)

# Anchos Helvetica en milésimas de em. Se usan para partir líneas y tablas.
_ANCHOS = {
    " ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667,
    "'": 191, "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333,
    ".": 278, "/": 278, "0": 556, "1": 556, "2": 556, "3": 556, "4": 556,
    "5": 556, "6": 556, "7": 556, "8": 556, "9": 556, ":": 278, ";": 278,
    "<": 584, "=": 584, ">": 584, "?": 556, "@": 1015, "A": 667, "B": 667,
    "C": 722, "D": 722, "E": 667, "F": 611, "G": 778, "H": 722, "I": 278,
    "J": 500, "K": 667, "L": 556, "M": 833, "N": 722, "O": 778, "P": 667,
    "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722, "V": 667, "W": 944,
    "X": 667, "Y": 667, "Z": 611, "[": 278, "\\": 278, "]": 278, "^": 469,
    "_": 556, "`": 333, "a": 556, "b": 556, "c": 500, "d": 556, "e": 556,
    "f": 278, "g": 556, "h": 556, "i": 222, "j": 222, "k": 500, "l": 222,
    "m": 833, "n": 556, "o": 556, "p": 556, "q": 556, "r": 333, "s": 500,
    "t": 278, "u": 556, "v": 500, "w": 722, "x": 500, "y": 500, "z": 500,
    "{": 334, "|": 260, "}": 334, "~": 584, "°": 400, "€": 556,
}


def _base_para_ancho(caracter: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", caracter)
    base = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return base[:1] if base else caracter


def _ancho_texto(texto: str, tamano: float, negrita: bool = False) -> float:
    texto = _normalizar_unicode(texto)
    ancho = sum(_ANCHOS.get(_base_para_ancho(c), 556) for c in texto) / 1000.0
    return ancho * tamano * (1.02 if negrita else 1.0)




_REEMPLAZOS_UNICODE = {
    "→": "->",
    "←": "<-",
    "↔": "<->",
    "≥": ">=",
    "≤": "<=",
    "≠": "!=",
    "≈": "~=",
    "∞": "inf",
    "√": "sqrt",
    "∂": "d",
    "Σ": "Sum",
    "∑": "Sum",
    "λ": "lambda",
    "Λ": "Lambda",
    "μ": "mu",
    "ρ": "rho",
    "β": "beta",
    "α": "alpha",
    "γ": "gamma",
    "Δ": "Delta",
    "°": "°",
    "−": "-",
    "–": "-",
    "—": "-",
    "…": "...",
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
    "₀": "0",
    "₁": "1",
    "₂": "2",
    "³": "^3",
    "²": "^2",
    "×": "x",
    "•": "-",
}


def _normalizar_unicode(texto: str) -> str:
    """Sustituye símbolos no representables por equivalentes WinAnsi legibles."""
    return "".join(_REEMPLAZOS_UNICODE.get(caracter, caracter) for caracter in texto)


def _escapar_pdf(texto: str) -> str:
    texto = _normalizar_unicode(texto)
    bytes_latin = []
    for caracter in texto:
        try:
            bytes_latin.extend(caracter.encode("latin-1"))
        except UnicodeEncodeError:
            bytes_latin.extend("?".encode("ascii"))
    resultado = []
    for byte in bytes_latin:
        if byte == 40:
            resultado.append(r"\(")
        elif byte == 41:
            resultado.append(r"\)")
        elif byte == 92:
            resultado.append(r"\\")
        elif byte >= 128:
            resultado.append(f"\\{byte:03o}")
        else:
            resultado.append(chr(byte))
    return "".join(resultado)


def _color(valor: tuple[float, float, float], trazo: bool = False) -> str:
    operador = "RG" if trazo else "rg"
    return f"{valor[0]:.4f} {valor[1]:.4f} {valor[2]:.4f} {operador}"


def _limpiar_texto_ia(texto: str) -> str:
    limpio = texto.replace("**", "").replace("__", "")
    limpio = re.sub(r"(?m)^#{1,6}\s*", "", limpio)
    limpio = re.sub(r"(?m)^\s*[-*]\s+", "• ", limpio)
    limpio = re.sub(r"`([^`]*)`", r"\1", limpio)
    limpio = limpio.replace("|", " / ")
    return limpio.strip()


class DocumentoPDF:
    """Mini motor PDF con paginación, tablas y gráficos vectoriales."""

    def __init__(self) -> None:
        self.paginas: list[list[str]] = []
        self.y = 76.0
        self.numero_pagina = 0
        self._en_portada = False
        self._nueva_pagina()

    # ------------------------------------------------------------------
    # Paginación y primitivas
    # ------------------------------------------------------------------
    def _nueva_pagina(self) -> None:
        self.paginas.append([])
        self.numero_pagina += 1
        self.y = 78.0
        self._en_portada = False

    def _comando(self, texto: str) -> None:
        self.paginas[-1].append(texto)

    def _asegurar_espacio(self, altura: float) -> None:
        if self.y + altura > ALTO_PAGINA - 55.0:
            self._nueva_pagina()

    def rectangulo(
        self,
        x: float,
        y: float,
        ancho: float,
        alto: float,
        relleno: tuple[float, float, float] | None = None,
        borde: tuple[float, float, float] | None = None,
        grosor: float = 0.7,
        radio: float = 0.0,
    ) -> None:
        if relleno is None and borde is None:
            return
        y_pdf = ALTO_PAGINA - (y + alto)
        radio = min(radio, ancho / 2.0, alto / 2.0)
        comandos: list[str] = []
        if radio > 0.5:
            k = 0.5522847498
            x0, y0 = x + radio, y_pdf
            comandos.append(f"{x0:.2f} {y0:.2f} m")
            comandos.append(f"{x + ancho - radio:.2f} {y0:.2f} l")
            comandos.append(
                f"{x + ancho - radio + k * radio:.2f} {y0:.2f} "
                f"{x + ancho:.2f} {y0 + radio - k * radio:.2f} "
                f"{x + ancho:.2f} {y0 + radio:.2f} c"
            )
            comandos.append(f"{x + ancho:.2f} {y_pdf + alto - radio:.2f} l")
            comandos.append(
                f"{x + ancho:.2f} {y_pdf + alto - radio + k * radio:.2f} "
                f"{x + ancho - radio + k * radio:.2f} {y_pdf + alto:.2f} "
                f"{x + ancho - radio:.2f} {y_pdf + alto:.2f} c"
            )
            comandos.append(f"{x + radio:.2f} {y_pdf + alto:.2f} l")
            comandos.append(
                f"{x + radio - k * radio:.2f} {y_pdf + alto:.2f} "
                f"{x:.2f} {y_pdf + alto - radio + k * radio:.2f} "
                f"{x:.2f} {y_pdf + alto - radio:.2f} c"
            )
            comandos.append(f"{x:.2f} {y_pdf + radio:.2f} l")
            comandos.append(
                f"{x:.2f} {y_pdf + radio - k * radio:.2f} "
                f"{x + radio - k * radio:.2f} {y_pdf:.2f} "
                f"{x + radio:.2f} {y_pdf:.2f} c"
            )
            comandos.append("h")
        else:
            comandos.append(
                f"{x:.2f} {y_pdf:.2f} {ancho:.2f} {alto:.2f} re"
            )
        comandos.append("q")
        if relleno:
            comandos.append(_color(relleno))
        if borde:
            comandos.append(_color(borde, trazo=True))
            comandos.append(f"{grosor:.2f} w")
        if relleno and borde:
            comandos.append("B")
        elif relleno:
            comandos.append("f")
        else:
            comandos.append("S")
        comandos.append("Q")
        self._comando(" ".join(comandos))

    def linea(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        color: tuple[float, float, float] = GRIS_CLARO,
        grosor: float = 0.6,
        discontinuo: bool = False,
    ) -> None:
        patron = "[2 2] 0 d " if discontinuo else ""
        self._comando(
            f"q {_color(color, trazo=True)} {grosor:.2f} w {patron}"
            f"{x1:.2f} {ALTO_PAGINA - y1:.2f} m "
            f"{x2:.2f} {ALTO_PAGINA - y2:.2f} l S Q"
        )

    def texto_simple(
        self,
        x: float,
        y: float,
        texto: str,
        tamano: float = 9.5,
        color: tuple[float, float, float] = NEGRO,
        negrita: bool = False,
    ) -> None:
        if not texto:
            return
        fuente = "F2" if negrita else "F1"
        baseline = ALTO_PAGINA - (y + tamano * 0.80)
        self._comando(
            f"BT {_color(color)} /{fuente} {tamano:.2f} Tf "
            f"1 0 0 1 {x:.2f} {baseline:.2f} Tm "
            f"({_escapar_pdf(texto)}) Tj ET"
        )

    def _partir_texto(
        self, texto: str, ancho: float, tamano: float, negrita: bool = False
    ) -> list[str]:
        texto = _normalizar_unicode(texto).replace("\t", " ").replace("\r", "")
        lineas: list[str] = []
        for parrafo in texto.split("\n"):
            palabras = parrafo.split(" ")
            linea_actual = ""
            for palabra in palabras:
                candidata = palabra if not linea_actual else linea_actual + " " + palabra
                if _ancho_texto(candidata, tamano, negrita) <= ancho:
                    linea_actual = candidata
                    continue
                if linea_actual:
                    lineas.append(linea_actual)
                # Divide palabras excepcionalmente largas (URL, hashes, etc.).
                if _ancho_texto(palabra, tamano, negrita) > ancho:
                    fragmento = ""
                    for caracter in palabra:
                        if _ancho_texto(fragmento + caracter, tamano, negrita) > ancho:
                            lineas.append(fragmento)
                            fragmento = caracter
                        else:
                            fragmento += caracter
                    linea_actual = fragmento
                else:
                    linea_actual = palabra
            lineas.append(linea_actual)
        return lineas or [""]

    def parrafo(
        self,
        texto: str,
        tamano: float = 9.3,
        color: tuple[float, float, float] = NEGRO,
        x: float = MARGEN,
        ancho: float = ANCHO_UTIL,
        interlinea: float | None = None,
        negrita: bool = False,
        espacio_despues: float = 5.0,
    ) -> None:
        interlinea = interlinea or tamano * 1.42
        for linea in self._partir_texto(texto, ancho, tamano, negrita):
            self._asegurar_espacio(interlinea)
            self.texto_simple(x, self.y, linea, tamano, color, negrita)
            self.y += interlinea
        self.y += espacio_despues

    def vineta(
        self,
        texto: str,
        tamano: float = 9.2,
        color: tuple[float, float, float] = NEGRO,
        x: float = MARGEN,
        ancho: float = ANCHO_UTIL,
    ) -> None:
        interlinea = tamano * 1.42
        lineas = self._partir_texto(texto, ancho - 16, tamano)
        self._asegurar_espacio(max(interlinea, interlinea * len(lineas)))
        self.rectangulo(x + 2, self.y + 4.1, 4.3, 4.3, relleno=CIAN, radio=1.8)
        for linea in lineas:
            self.texto_simple(x + 15, self.y, linea, tamano, color)
            self.y += interlinea
        self.y += 2.0

    def titulo_seccion(
        self, numero: str, titulo: str, subtitulo: str | None = None
    ) -> None:
        self._asegurar_espacio(46)
        self.rectangulo(MARGEN, self.y + 2, 4.5, 24, relleno=CIAN, radio=1.5)
        self.texto_simple(MARGEN + 14, self.y, f"{numero}. {titulo}", 15.5, AZUL, True)
        self.y += 24
        if subtitulo:
            self.texto_simple(MARGEN + 14, self.y, subtitulo, 8.5, GRIS)
            self.y += 14
        self.y += 7

    def subtitulo(self, texto: str) -> None:
        self._asegurar_espacio(26)
        self.texto_simple(MARGEN, self.y, texto, 10.8, AZUL_MEDIO, True)
        self.y += 17

    def tarjetas_metricas(self, tarjetas: Sequence[tuple[str, str, str]]) -> None:
        cantidad = len(tarjetas)
        espacio = 8.0
        ancho = (ANCHO_UTIL - espacio * (cantidad - 1)) / cantidad
        alto = 58.0
        self._asegurar_espacio(alto + 12)
        for indice, (titulo, valor, detalle) in enumerate(tarjetas):
            x = MARGEN + indice * (ancho + espacio)
            self.rectangulo(x, self.y, ancho, alto, AZUL_CLARO, (0.82, 0.90, 0.95), 0.6, 5)
            self.texto_simple(x + 10, self.y + 10, titulo.upper(), 6.8, GRIS, True)
            self.texto_simple(x + 10, self.y + 23, valor, 17.0, AZUL, True)
            self.texto_simple(x + 10, self.y + 44, detalle, 6.9, GRIS)
        self.y += alto + 15

    def tabla(
        self,
        encabezados: Sequence[str],
        filas: Sequence[Sequence[Any]],
        anchos: Sequence[float],
        tamano: float = 7.7,
        x: float = MARGEN,
        altura_fila: float = 16.0,
    ) -> None:
        if len(encabezados) != len(anchos):
            raise ValueError("La tabla tiene un número inconsistente de columnas.")
        if abs(sum(anchos) - ANCHO_UTIL) > 14:
            # Ajuste proporcional para que la tabla siempre quepa en el margen.
            factor = ANCHO_UTIL / sum(anchos)
            anchos = [ancho * factor for ancho in anchos]
        interlinea = tamano * 1.25
        padding = 4.0

        def dibujar_encabezado() -> None:
            self._asegurar_espacio(altura_fila + 2)
            self.rectangulo(
                x, self.y, sum(anchos), altura_fila, AZUL_MEDIO, None, radio=2
            )
            cursor = x
            for titulo, ancho in zip(encabezados, anchos):
                self.texto_simple(
                    cursor + padding, self.y + 4.5, str(titulo), tamano, BLANCO, True
                )
                cursor += ancho
            self.y += altura_fila

        dibujar_encabezado()
        for indice, fila in enumerate(filas):
            celdas = [str(valor) for valor in fila]
            if len(celdas) != len(anchos):
                raise ValueError("Una fila no coincide con las columnas de la tabla.")
            lineas_por_celda = [
                self._partir_texto(celda, ancho - 2 * padding, tamano)
                for celda, ancho in zip(celdas, anchos)
            ]
            numero_lineas = max(len(lineas) for lineas in lineas_por_celda)
            alto_fila = max(altura_fila, numero_lineas * interlinea + 2 * padding)
            if self.y + alto_fila > ALTO_PAGINA - 55:
                self._nueva_pagina()
                dibujar_encabezado()
            relleno = BLANCO if indice % 2 == 0 else (0.965, 0.975, 0.985)
            self.rectangulo(x, self.y, sum(anchos), alto_fila, relleno, (0.87, 0.90, 0.93), 0.35)
            cursor = x
            for lineas, ancho in zip(lineas_por_celda, anchos):
                for linea_numero, linea in enumerate(lineas):
                    self.texto_simple(
                        cursor + padding,
                        self.y + padding + linea_numero * interlinea,
                        linea,
                        tamano,
                        NEGRO,
                    )
                cursor += ancho
            self.y += alto_fila
        self.y += 10

    # ------------------------------------------------------------------
    # Gráficos vectoriales
    # ------------------------------------------------------------------
    def _panel_grafico(
        self,
        x: float,
        y: float,
        ancho: float,
        alto: float,
        titulo: str,
        unidad: str,
        valores: Sequence[float],
        tiempos: Sequence[float],
        color: tuple[float, float, float],
    ) -> None:
        self.rectangulo(x, y, ancho, alto, BLANCO, (0.84, 0.89, 0.93), 0.65, 6)
        self.texto_simple(x + 12, y + 10, titulo, 9.0, AZUL, True)
        if not valores:
            self.texto_simple(x + 12, y + 38, "Sin datos suficientes", 8.0, GRIS)
            return
        vmin = min(valores)
        vmax = max(valores)
        if math.isclose(vmin, vmax):
            vmin -= 1.0
            vmax += 1.0
        margen_valor = (vmax - vmin) * 0.12
        vmin -= margen_valor
        vmax += margen_valor
        px, py = x + 44, y + 32
        pw, ph = ancho - 58, alto - 60
        # Rejilla y etiquetas del eje Y.
        for i in range(4):
            fraccion = i / 3.0
            gy = py + ph * fraccion
            self.linea(px, gy, px + pw, gy, (0.90, 0.93, 0.95), 0.4)
            valor = vmax - (vmax - vmin) * fraccion
            self.texto_simple(
                x + 8, gy - 3.2, f"{valor:.2f}", 6.3, GRIS
            )
        self.linea(px, py + ph, px + pw, py + ph, GRIS, 0.7)
        self.linea(px, py, px, py + ph, GRIS, 0.7)
        n = len(valores)
        if n == 1:
            valores = [valores[0], valores[0]]
            tiempos = [tiempos[0], tiempos[0] + 1]
            n = 2
        puntos_pdf = []
        puntos_pantalla = []
        for indice, valor in enumerate(valores):
            fx = px + (pw * indice / max(1, n - 1))
            fy = py + ph - (valor - vmin) / (vmax - vmin) * ph
            puntos_pantalla.append((fx, fy))
            puntos_pdf.append((fx, ALTO_PAGINA - fy))
        comandos = [
            "q",
            _color(color, trazo=True),
            "1.6 w",
            f"{puntos_pdf[0][0]:.2f} {puntos_pdf[0][1]:.2f} m",
        ]
        for fx, fy in puntos_pdf[1:]:
            comandos.append(f"{fx:.2f} {fy:.2f} l")
        comandos.append("S Q")
        self._comando(" ".join(comandos))
        # Marcadores discretos (máximo 18 para no saturar).
        paso = max(1, len(puntos_pantalla) // 18)
        for indice in range(0, len(puntos_pantalla), paso):
            fx, fy = puntos_pantalla[indice]
            self.rectangulo(fx - 1.4, fy - 1.4, 2.8, 2.8, relleno=color, radio=1.4)
        self.texto_simple(x + ancho - 60, y + 10, f"{valores[-1]:.2f} {unidad}", 7.4, color, True)
        if tiempos:
            self.texto_simple(px, py + ph + 7, f"t={tiempos[0]:.0f}s", 6.2, GRIS)
            self.texto_simple(
                px + pw - 34, py + ph + 7, f"t={tiempos[-1]:.0f}s", 6.2, GRIS
            )

    def grafico_lineas(
        self,
        x: float,
        y: float,
        ancho: float,
        alto: float,
        titulo: str,
        valores: Sequence[float],
        tiempos: Sequence[float],
        color: tuple[float, float, float],
        unidad: str = "",
    ) -> None:
        self._panel_grafico(x, y, ancho, alto, titulo, unidad, valores, tiempos, color)

    def grafico_barras(
        self,
        x: float,
        y: float,
        ancho: float,
        alto: float,
        titulo: str,
        valores: Sequence[float],
        etiquetas: Sequence[str],
        colores: Sequence[tuple[float, float, float]],
        unidad: str = "kW",
    ) -> None:
        self.rectangulo(x, y, ancho, alto, BLANCO, (0.84, 0.89, 0.93), 0.65, 6)
        self.texto_simple(x + 12, y + 10, titulo, 9.0, AZUL, True)
        if not valores or not etiquetas:
            self.texto_simple(x + 12, y + 38, "Sin datos", 8.0, GRIS)
            return
        maximo = max(valores) * 1.18 or 1.0
        px, py = x + 30, y + 34
        pw, ph = ancho - 46, alto - 65
        self.linea(px, py + ph, px + pw, py + ph, GRIS, 0.7)
        n = len(valores)
        ancho_barra = min(52.0, pw / (n * 1.7))
        paso = pw / max(1, n)
        for indice, (valor, etiqueta, color) in enumerate(zip(valores, etiquetas, colores)):
            altura = 0.0 if maximo <= 0 else max(1.0, ph * valor / maximo)
            bx = px + paso * indice + (paso - ancho_barra) / 2
            by = py + ph - altura
            self.rectangulo(bx, by, ancho_barra, altura, relleno=color, radio=2)
            self.texto_simple(bx, by - 10, f"{valor:.1f} {unidad}", 6.8, color, True)
            self.texto_simple(bx, py + ph + 6, etiqueta, 6.8, GRIS, True)

    # ------------------------------------------------------------------
    # Portada y serialización
    # ------------------------------------------------------------------
    def portada(
        self,
        titulo: str,
        subtitulo: str,
        equipo: str,
        fecha: str,
        resumen: str,
    ) -> None:
        self._asegurar_espacio(0)
        self.rectangulo(0, 0, ANCHO_PAGINA, ALTO_PAGINA, AZUL)
        self.rectangulo(0, 0, ANCHO_PAGINA, 9, CIAN)
        self.rectangulo(MARGEN, 118, 5, 150, CIAN, radio=2)
        self.texto_simple(MARGEN + 20, 112, "DATASTREAM NETWORKS", 12, CIAN, True)
        lineas_titulo = self._partir_texto(titulo, ANCHO_UTIL - 25, 28, True)
        y = 148
        for linea in lineas_titulo:
            self.texto_simple(MARGEN + 20, y, linea, 28, BLANCO, True)
            y += 35
        self.texto_simple(MARGEN + 20, y + 15, subtitulo, 12.5, (0.72, 0.83, 0.90))
        self.rectangulo(MARGEN, 360, ANCHO_UTIL, 1, (0.25, 0.42, 0.55))
        self.texto_simple(MARGEN, 382, "INFORME TÉCNICO DE SIMULACIÓN", 9, CIAN, True)
        self.y = 404
        self.parrafo(
            resumen,
            tamano=10.3,
            color=(0.84, 0.90, 0.94),
            x=MARGEN,
            ancho=ANCHO_UTIL - 10,
            interlinea=16,
            espacio_despues=12,
        )
        self.y = 650
        self.rectangulo(MARGEN, self.y, 2, 66, CIAN)
        self.texto_simple(MARGEN + 14, self.y + 2, equipo, 12.5, BLANCO, True)
        self.texto_simple(MARGEN + 14, self.y + 24, fecha, 9.5, (0.72, 0.83, 0.90))
        self.texto_simple(
            MARGEN + 14,
            self.y + 42,
            "SimPy · Pygame · Programación dinámica · Optimización no lineal",
            8.4,
            (0.65, 0.78, 0.87),
        )
        self._nueva_pagina()

    def _encabezado_pie(self) -> None:
        for indice, comandos in enumerate(self.paginas, start=1):
            if indice == 1:
                continue
            self.paginas[indice - 1] = comandos + [
                "q 0.84 0.88 0.92 RG 0.6 w "
                f"{MARGEN:.2f} {ALTO_PAGINA - 52:.2f} m "
                f"{ANCHO_PAGINA - MARGEN:.2f} {ALTO_PAGINA - 52:.2f} l S Q",
                "q 0.84 0.88 0.92 RG 0.6 w "
                f"{MARGEN:.2f} {ALTO_PAGINA - 796:.2f} m "
                f"{ANCHO_PAGINA - MARGEN:.2f} {ALTO_PAGINA - 796:.2f} l S Q",
            ]
            self.paginas[indice - 1].append(
                f"BT {_color(GRIS)} /F1 7 Tf 1 0 0 1 {MARGEN:.2f} {ALTO_PAGINA - 35:.2f} Tm "
                f"(DataStream Networks · Informe de simulación) Tj ET"
            )
            self.paginas[indice - 1].append(
                f"BT {_color(GRIS)} /F1 7 Tf 1 0 0 1 {ANCHO_PAGINA - MARGEN - 55:.2f} "
                f"{ALTO_PAGINA - 812:.2f} Tm (Página {indice} de {len(self.paginas)}) Tj ET"
            )

    def guardar(self, ruta: str | Path) -> Path:
        """Serializa el documento y escribe el archivo PDF."""
        self._encabezado_pie()
        ruta = Path(ruta)
        ruta.parent.mkdir(parents=True, exist_ok=True)

        cantidad = len(self.paginas)
        id_pagina_base = 6
        id_contenido_base = 7
        kids = " ".join(
            f"{id_pagina_base + 2 * i} 0 R" for i in range(cantidad)
        )
        objetos: list[bytes] = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{kids}] /Count {cantidad} >>".encode("latin-1"),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
            b"<< /Title (Simulacion DataStream Networks) /Author (DataStream Team) /Creator (Simulador DataStream) >>",
        ]
        for indice in range(cantidad):
            page_id = id_pagina_base + 2 * indice
            content_id = id_contenido_base + 2 * indice
            objetos.append(
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {ANCHO_PAGINA:.2f} {ALTO_PAGINA:.2f}] "
                    f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
                    f"/Contents {content_id} 0 R >>"
                ).encode("latin-1")
            )
            flujo = "\n".join(self.paginas[indice]).encode("latin-1", errors="replace")
            objetos.append(
                b"<< /Length " + str(len(flujo)).encode() + b" >>\nstream\n"
                + flujo
                + b"\nendstream"
            )

        salida = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for numero, objeto in enumerate(objetos, start=1):
            offsets.append(len(salida))
            salida.extend(f"{numero} 0 obj\n".encode())
            salida.extend(objeto)
            salida.extend(b"\nendobj\n")
        xref_inicio = len(salida)
        salida.extend(f"xref\n0 {len(objetos) + 1}\n".encode())
        salida.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            salida.extend(f"{offset:010d} 00000 n \n".encode())
        salida.extend(
            (
                f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R /Info 5 0 R >>\n"
                f"startxref\n{xref_inicio}\n%%EOF\n"
            ).encode()
        )
        try:
            ruta.write_bytes(bytes(salida))
        except OSError as exc:
            raise ErrorReporte(f"No se pudo escribir el PDF {ruta}: {exc}") from exc
        return ruta


def _formatear_numero(valor: Any, decimales: int = 2) -> str:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    if math.isinf(numero):
        return "∞"
    if math.isnan(numero):
        return "—"
    return f"{numero:.{decimales}f}"


class GeneradorReporte:
    """Compone el informe académico a partir de una instantánea del simulador."""

    def __init__(self, instantanea: dict[str, Any], analisis: ResultadoIA) -> None:
        self.datos = instantanea
        self.analisis = analisis
        self.metricas = instantanea.get("metricas", {})
        self.config = instantanea.get("configuracion", {})
        self.energia = instantanea.get("energia", {})
        self.termico = instantanea.get("modelo_termico", {})
        self.servidores = instantanea.get("servidores", [])
        self.rutas = instantanea.get("rutas", {})
        self.historial = instantanea.get("historial", [])
        self.topologia = instantanea.get("topologia", {})
        self.documento = DocumentoPDF()

    def construir(self) -> DocumentoPDF:
        self._portada()
        self._resumen()
        self._configuracion()
        self._modulo_uno()
        self._modulo_dos()
        self._modulo_tres()
        self._analisis_ia()
        self._conclusiones()
        return self.documento

    # ------------------------------------------------------------------
    def _portada(self) -> None:
        equipo = self.config.get("nombre_equipo", "Equipo DataStream")
        procesados = int(self.metricas.get("paquetes_procesados", 0))
        aceptacion = 100.0 * float(self.metricas.get("tasa_aceptacion", 0.0))
        resumen = (
            "Simulación de eventos discretos para evaluar enrutamiento dinámico, "
            "admisión de tareas, control térmico y distribución del presupuesto "
            f"eléctrico en la red de centros de datos. En esta corrida se procesaron "
            f"{procesados} paquetes con una aceptación de {aceptacion:.1f} % y se "
            "integraron los modelos de programación dinámica y optimización no lineal "
            "dentro del reloj SimPy."
        )
        self.documento.portada(
            "Optimización de redes de centros de datos",
            "DataStream Networks · Simulador académico con SimPy y Pygame",
            equipo,
            datetime.now().strftime("%d/%m/%Y %H:%M"),
            resumen,
        )

    def _resumen(self) -> None:
        doc = self.documento
        doc.titulo_seccion("0", "Resumen ejecutivo", "Resultados de la corrida y hallazgos principales")
        procesados = int(self.metricas.get("paquetes_procesados", 0))
        generados = int(self.metricas.get("paquetes_generados", 0))
        aceptacion = 100.0 * float(self.metricas.get("tasa_aceptacion", 0.0))
        latencia = float(self.metricas.get("latencia_promedio", 0.0))
        cambios = int(self.metricas.get("cambios_ruta_congestion", 0))
        doc.tarjetas_metricas(
            [
                ("Paquetes procesados", str(procesados), f"de {generados} generados"),
                ("Aceptación", f"{aceptacion:.1f} %", "admisión por mochila"),
                ("Latencia media", f"{latencia:.2f} s", "extremo a extremo"),
                ("Rutas recalculadas", str(cambios), "cambios por congestión"),
            ]
        )
        doc.subtitulo("Diagnóstico general")
        texto = (
            f"La simulación se ejecutó durante {float(self.metricas.get('tiempo_simulado', 0)):.1f} s "
            f"con una tasa de llegada configurada de {float(self.config.get('tasa_llegada', 0)):.1f} "
            f"paquetes/s. Se observaron {int(self.metricas.get('fallas_totales', 0))} eventos de "
            f"falla/recuperación y {int(self.metricas.get('emergencias', 0))} activaciones de "
            f"enfriamiento de emergencia. El reporte integra la ruta de mínima latencia, la "
            "mochila bidimensional CPU/RAM, el control de ventiladores por gradiente y la "
            "asignación de energía por multiplicadores de Lagrange."
        )
        doc.parrafo(texto)
        doc.subtitulo("Indicadores del sistema")
        filas = [
            ("Throughput", f"{float(self.metricas.get('throughput', 0)):.2f} paq/s"),
            ("Paquetes descartados", str(int(self.metricas.get("paquetes_descartados", 0)))),
            ("Latencia P95", f"{float(self.metricas.get('latencia_p95', 0)):.2f} s"),
            ("Valor procesado", f"{float(self.metricas.get('valor_procesado', 0)):.0f} u."),
            ("Energía de ventiladores", f"{float(self.metricas.get('energia_ventiladores_kwh', 0)):.3f} kWh"),
            ("Cambios de ruta totales", str(int(self.metricas.get("cambios_ruta", 0)))),
        ]
        doc.tabla(("Métrica", "Valor"), filas, (300, 211), tamano=8.2)

    def _configuracion(self) -> None:
        doc = self.documento
        doc.titulo_seccion("1", "Configuración y metodología", "Parámetros validados de la corrida")
        doc.subtitulo("Parámetros de simulación")
        claves = [
            ("Duración", f"{float(self.config.get('duracion', 0)):.1f} s"),
            ("Semilla", str(self.config.get("semilla"))),
            ("Tasa de llegada", f"{float(self.config.get('tasa_llegada', 0)):.2f} paq/s"),
            ("Buffer por servidor", str(self.config.get("capacidad_buffer"))),
            ("MTBF routers", f"{float(self.config.get('mtbf_router', 0)):.1f} s"),
            ("MTBF servidores", f"{float(self.config.get('mtbf_servidor', 0)):.1f} s"),
            ("Reparación media", f"{float(self.config.get('tiempo_reparacion', 0)):.1f} s"),
            ("Presupuesto eléctrico", f"{float(self.config.get('presupuesto_energia', 0)):.1f} kW"),
        ]
        doc.tabla(("Parámetro", "Valor"), claves, (300, 211), tamano=8.2)
        doc.subtitulo("Trazabilidad metodológica")
        doc.parrafo(
            "Las llegadas siguen un proceso Poisson y los tiempos de servicio son "
            "exponenciales. Cada paquete se enruta con Bellman-Ford (programación "
            "dinámica) y compite por CPU/RAM mediante una mochila 0/1 bidimensional. "
            "Cada segundo simulado se aplican cinco iteraciones de gradiente proyectado "
            "a los ventiladores; cada dos segundos se reoptimiza la energía de los "
            "clústeres con condiciones KKT. Los datos se validan antes de iniciar la "
            "simulación y pueden cargarse desde un archivo JSON."
        )

    def _modulo_uno(self) -> None:
        doc = self.documento
        doc.titulo_seccion(
            "2",
            "Módulo 1 · Enrutamiento y asignación",
            "Programación dinámica aplicada a red y servidores",
        )
        doc.subtitulo("Ruta más corta (Bellman-Ford)")
        doc.parrafo(
            "La latencia efectiva de cada enlace es w(u,v) = w0·(1 + β·u^1.35), "
            "donde u es la utilización suavizada del enlace. La recurrencia de "
            "programación dinámica es d_k(v) = min(d_{k-1}(v), min_u[d_{k-1}(u) + w(u,v)]). "
            "Las rutas se recalculan periódicamente y ante fallas."
        )
        filas_rutas = []
        for destino, info in sorted(self.rutas.items()):
            camino = info.get("camino", []) if info.get("disponible") else []
            latencia = info.get("latencia") if info.get("disponible") else None
            filas_rutas.append(
                (
                    destino,
                    " → ".join(camino) if camino else "Sin ruta",
                    _formatear_numero(latencia, 2) if latencia is not None else "∞",
                    "Disponible" if info.get("disponible") else "Fuera de servicio",
                )
            )
        doc.tabla(
            ("Destino", "Camino óptimo", "Latencia (s)", "Estado"),
            filas_rutas,
            (55, 300, 75, 81),
            tamano=7.3,
        )

        doc.subtitulo("Admisión por mochila 0/1 bidimensional")
        doc.parrafo(
            "Para cada servidor se resuelve V(i,c,r) = max[V(i-1,c,r), "
            "V(i-1,c-cpu_i,r-ram_i)+valor_i], maximizando el valor de las tareas "
            "admitidas sin superar la capacidad libre de CPU ni RAM. El valor "
            "combina prioridad (1-5) y tamaño. Las tareas que no caben esperan "
            "hasta 6 s y luego se reportan como expiradas."
        )
        filas_servidor = []
        for servidor in self.servidores:
            filas_servidor.append(
                (
                    servidor.get("id"),
                    servidor.get("cluster"),
                    f"{servidor.get('capacidad_cpu')}",
                    f"{servidor.get('capacidad_ram')}",
                    f"{float(servidor.get('carga', 0)) * 100:.0f} %",
                    str(servidor.get("aceptados", 0)),
                    str(servidor.get("procesados", 0)),
                    str(servidor.get("perdidos", 0)),
                    str(servidor.get("decisiones_mochila", 0)),
                )
            )
        doc.tabla(
            ("ID", "Clúster", "CPU", "RAM", "Carga", "Acept.", "Proc.", "Pérd.", "DP"),
            filas_servidor,
            (38, 83, 38, 38, 50, 48, 48, 48, 120),
            tamano=7.2,
        )
        total_celdas = sum(int(servidor.get("celdas_dp", 0)) for servidor in self.servidores)
        doc.parrafo(
            f"En total se resolvieron {sum(int(s.get('decisiones_mochila', 0)) for s in self.servidores)} "
            f"instancias de mochila y se evaluaron {total_celdas:,} celdas de programación "
            f"dinámica. La capacidad se libera al completar o perder una tarea.",
            tamano=8.2,
            color=GRIS,
        )

    def _modulo_dos(self) -> None:
        doc = self.documento
        doc.titulo_seccion(
            "3",
            "Módulo 2 · Energía y rendimiento",
            "Derivadas, gradiente proyectado y multiplicadores de Lagrange",
        )
        doc.subtitulo("Modelo de una variable y puntos críticos")
        coef = self.termico.get("coeficientes", {})
        doc.parrafo(
            "El modelo térmico académico es T(c) = a·c³ + b·c² + d·c + e, con "
            f"a={float(coef.get('a', 0)):.4f}, b={float(coef.get('b', 0)):.4f}, "
            f"d={float(coef.get('c', 0)):.4f}, e={float(coef.get('d', 0)):.4f}. "
            "Se calculan T'(c) y T''(c) de forma analítica para clasificar los "
            "puntos críticos. La temperatura de emergencia configurada es "
            f"{float(self.termico.get('temperatura_emergencia', 0)):.1f} °C."
        )
        filas_puntos = []
        for punto in self.termico.get("puntos", []):
            interpretacion = {
                "máximo": "Punto crítico: activa enfriamiento de emergencia",
                "mínimo": "Mínimo térmico local (zona de throttling)",
                "inflexión": "Cambio de concavidad de la curva térmica",
            }.get(punto.get("tipo"), "Punto de análisis")
            filas_puntos.append(
                (
                    punto.get("tipo", "").capitalize(),
                    f"{float(punto.get('carga', 0)):.3f}",
                    f"{float(punto.get('temperatura', 0)):.2f} °C",
                    f"{float(punto.get('derivada_segunda', 0)):.2f}",
                    interpretacion,
                )
            )
        doc.tabla(
            ("Tipo", "Carga", "Temperatura", "T''(c)", "Interpretación"),
            filas_puntos,
            (56, 48, 70, 55, 282),
            tamano=7.4,
        )
        doc.subtitulo("Control de refrigeración por gradiente")
        doc.parrafo(
            "Se minimiza P(f,L) = P0 + cv·f³ + c1·max(0,T-Tobj)² + c2·max(0,T-Temerg)² "
            "con f en [0.1, 1.0]. La derivada dP/df se calcula analíticamente y "
            "se aplica gradiente proyectado con retroceso de paso. En la simulación "
            "cada ventilador mantiene su propio estado y converge de forma continua."
        )
        filas_fans = []
        for servidor in self.servidores:
            filas_fans.append(
                (
                    servidor.get("id"),
                    f"{float(servidor.get('carga', 0)):.2f}",
                    f"{float(servidor.get('temperatura', 0)):.1f} °C",
                    f"{float(servidor.get('ventilador', 0)):.3f}",
                    f"{float(servidor.get('potencia_refrigeracion', 0)):.3f} kW",
                    "Sí" if servidor.get("emergencia") else "No",
                )
            )
        doc.tabla(
            ("Servidor", "Carga", "Temp.", "Ventilador", "Potencia", "Emergencia"),
            filas_fans,
            (75, 65, 75, 85, 100, 111),
            tamano=7.6,
        )
        if self.historial:
            muestra = self.historial[-90:]
            tiempos = [float(fila.get("tiempo", 0)) for fila in muestra]
            temperaturas = [float(fila.get("temperatura_media", 0)) for fila in muestra]
            ventiladores = [float(fila.get("ventilador_medio", 0)) for fila in muestra]
            doc._asegurar_espacio(160)
            y_grafico = doc.y
            ancho = (ANCHO_UTIL - 10) / 2
            doc.grafico_lineas(
                MARGEN,
                y_grafico,
                ancho,
                142,
                "Temperatura media",
                temperaturas,
                tiempos,
                ROJO,
                "°C",
            )
            doc.grafico_lineas(
                MARGEN + ancho + 10,
                y_grafico,
                ancho,
                142,
                "Ventilador medio (gradiente)",
                ventiladores,
                tiempos,
                CIAN,
                "",
            )
            doc.y = y_grafico + 154

        doc.subtitulo("Asignación de presupuesto con Lagrange")
        doc.parrafo(
            "Para R_i(e_i)=w_i·sqrt(e_i) y la restricción Σe_i=E, las condiciones "
            "KKT igualan los rendimientos marginales: w_i/(2·sqrt(e_i)) = λ. "
            "La solución es e_i = E·w_i²/Σw_j² y λ = 0.5·sqrt(Σw_j²/E). El "
            "multiplicador λ es el valor marginal del presupuesto eléctrico."
        )
        nombres = self.energia.get("nombres_cluster", [])
        asignacion = self.energia.get("asignacion", [])
        pesos = self.energia.get("pesos", [])
        marginales = self.energia.get("marginales", [])
        filas_energia = []
        for indice, nombre in enumerate(nombres):
            valor = float(asignacion[indice]) if indice < len(asignacion) else 0.0
            peso = float(pesos[indice]) if indice < len(pesos) else 0.0
            marginal = float(marginales[indice]) if indice < len(marginales) else 0.0
            cuota = 100.0 * valor / max(1e-9, float(self.energia.get("presupuesto", 0)))
            filas_energia.append(
                (nombre, f"{peso:.3f}", f"{valor:.2f} kW", f"{marginal:.4f}", f"{cuota:.1f} %")
            )
        doc.tabla(
            ("Clúster", "Peso w_i", "Energía óptima", "∂R/∂e", "Cuota"),
            filas_energia,
            (120, 85, 110, 100, 96),
            tamano=7.8,
        )
        doc.parrafo(
            f"λ = {float(self.energia.get('multiplicador_lagrange', 0)):.5f}; "
            f"rendimiento total = {float(self.energia.get('rendimiento_total', 0)):.3f}; "
            f"mejora frente al reparto igualitario = "
            f"{float(self.energia.get('ganancia_porcentual', 0)):.2f} %."
        )
        if nombres:
            doc._asegurar_espacio(168)
            doc.grafico_barras(
                MARGEN,
                doc.y,
                ANCHO_UTIL,
                154,
                "Distribución óptima del presupuesto eléctrico",
                [float(valor) for valor in asignacion],
                [str(nombre) for nombre in nombres],
                [CIAN, VERDE, AMBAR],
                "kW",
            )
            doc.y += 166

    def _modulo_tres(self) -> None:
        doc = self.documento
        doc.titulo_seccion(
            "4",
            "Módulo 3 · Simulación y visualización",
            "SimPy, teoría de colas, fallas y dashboard Pygame",
        )
        doc.subtitulo("Resultados de la simulación")
        filas = [
            ("Paquetes generados", str(int(self.metricas.get("paquetes_generados", 0)))),
            ("Paquetes procesados", str(int(self.metricas.get("paquetes_procesados", 0)))),
            ("Paquetes descartados", str(int(self.metricas.get("paquetes_descartados", 0)))),
            ("Throughput", f"{float(self.metricas.get('throughput', 0)):.3f} paq/s"),
            ("Latencia media", f"{float(self.metricas.get('latencia_promedio', 0)):.3f} s"),
            ("Latencia P95", f"{float(self.metricas.get('latencia_p95', 0)):.3f} s"),
            ("Cambios de ruta por congestión", str(int(self.metricas.get("cambios_ruta_congestion", 0)))),
            ("Fallas/recuperaciones", str(int(self.metricas.get("fallas_totales", 0)))),
            ("Emergencias térmicas", str(int(self.metricas.get("emergencias", 0)))),
            ("Energía de ventiladores", f"{float(self.metricas.get('energia_ventiladores_kwh', 0)):.4f} kWh"),
        ]
        doc.tabla(("Indicador", "Resultado"), filas, (300, 211), tamano=8.0)
        cola = self.metricas.get("teoria_colas", {})
        doc.subtitulo("Teoría de colas (M/M/c por servidor, estimación observada)")
        filas_cola = [
            ("λ total", f"{float(cola.get('lambda_total', 0)):.4f} paq/s"),
            ("λ por servidor", f"{float(cola.get('lambda_por_servidor', 0)):.4f} paq/s"),
            ("μ de servicio", f"{float(cola.get('mu', 0)):.4f} serv/s"),
            ("Canales c", str(int(cola.get('c', 4)))),
            ("ρ", f"{float(cola.get('rho', 0)):.4f}"),
            ("Lq", _formatear_numero(cola.get("Lq"), 4)),
            ("Wq", f"{_formatear_numero(cola.get('Wq'), 4)} s"),
            ("W", f"{_formatear_numero(cola.get('W'), 4)} s"),
            ("Estable", "Sí" if cola.get("estable") else "No (ρ ≥ 1)"),
        ]
        doc.tabla(("Variable", "Valor"), filas_cola, (300, 211), tamano=8.0)
        doc.subtitulo("Motivos de descarte")
        motivos = self.metricas.get("motivos_descarte", {})
        if motivos:
            filas_motivos = [
                (motivo.replace("_", " ").capitalize(), str(cantidad))
                for motivo, cantidad in sorted(motivos.items(), key=lambda item: -item[1])
            ]
            doc.tabla(("Motivo", "Cantidad"), filas_motivos, (370, 141), tamano=8.0)
        else:
            doc.parrafo("No se registraron descartes.", tamano=8.5, color=GRIS)
        doc.subtitulo("Cobertura de la interfaz gráfica")
        doc.parrafo(
            "El dashboard Pygame dibuja la topología con tráfico animado, resalta "
            "la ruta óptima y muestra un aviso cuando cambia por congestión. "
            "Incluye gráficos en tiempo real de temperatura/ventilador, barras "
            "de asignación de energía y el valor del multiplicador de Lagrange, "
            "además de controles de pausa, velocidad y generación de PDF."
        )

    def _analisis_ia(self) -> None:
        doc = self.documento
        doc.titulo_seccion(
            "5", "Análisis asistido por IA", "Interpretación automática de los resultados"
        )
        texto = _limpiar_texto_ia(self.analisis.texto)
        doc.parrafo(f"Fuente: {self.analisis.fuente}.", tamano=8.0, color=GRIS)
        for bloque in texto.split("\n\n"):
            if not bloque.strip():
                continue
            if bloque.strip().startswith(("-", "•")):
                for linea in bloque.splitlines():
                    doc.vineta(linea.lstrip("-• ").strip())
            else:
                doc.parrafo(bloque.strip(), tamano=9.0, interlinea=13.3)
        if self.analisis.error:
            doc.parrafo(
                f"Nota: la API de IA no se utilizó ({self.analisis.error}). "
                "Se presenta el análisis determinista local.",
                tamano=8.0,
                color=AMBAR,
            )

    def _conclusiones(self) -> None:
        doc = self.documento
        doc.titulo_seccion("6", "Conclusiones y recomendaciones", "Cierre técnico del proyecto")
        metricas = self.metricas
        doc.vineta(
            "La programación dinámica permite recalculación de rutas y admisión "
            "óptima con un costo computacional acotado para la red simulada."
        )
        doc.vineta(
            "El control por gradiente mantiene la refrigeración dentro de los "
            "límites operativos y genera una traza verificable de convergencia."
        )
        doc.vineta(
            f"La asignación de Lagrange alcanzó λ={float(self.energia.get('multiplicador_lagrange', 0)):.4f} "
            f"y una mejora de {float(self.energia.get('ganancia_porcentual', 0)):.2f} % "
            "respecto al reparto igualitario."
        )
        if float(metricas.get("tasa_descarte", 0.0)) > 0.15:
            doc.vineta(
                "La tasa de descarte supera 15 %: se recomienda ampliar la capacidad "
                "de los servidores críticos o revisar los tiempos de falla/reparación."
            )
        if int(metricas.get("cambios_ruta_congestion", 0)) > 0:
            doc.vineta(
                "Los cambios de ruta por congestión demuestran la utilidad de la "
                "topología redundante y del recálculo periódico con latencia efectiva."
            )
        doc.parrafo(
            "El simulador integra los tres módulos en un mismo reloj y expone "
            "resultados trazables para defensa: rutas, soluciones de mochila, "
            "puntos críticos térmicos, iteraciones de gradiente, multiplicador "
            "de Lagrange y métricas de colas.",
            tamano=8.7,
            color=GRIS,
            espacio_despues=2,
        )
        doc.parrafo(
            "Informe generado automáticamente por el simulador DataStream Networks.",
            tamano=7.5,
            color=GRIS,
        )


def generar_reporte_pdf(
    instantanea: dict[str, Any],
    analisis: ResultadoIA,
    ruta_salida: str | Path | None = None,
) -> Path:
    """Construye y guarda el informe PDF; devuelve la ruta final."""
    if ruta_salida is None:
        marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        ruta_salida = Path("reportes") / f"reporte_datastream_{marca}.pdf"
    ruta = Path(ruta_salida)
    if ruta.suffix.lower() != ".pdf":
        ruta = ruta.with_suffix(".pdf")
    generador = GeneradorReporte(instantanea, analisis)
    documento = generador.construir()
    return documento.guardar(ruta)

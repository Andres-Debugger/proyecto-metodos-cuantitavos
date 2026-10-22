#!/usr/bin/env python3
"""Punto de entrada del simulador DataStream Networks.

Ejemplos:
    python main.py
    python main.py --sin-ui --duracion 90
    python main.py --config datos_ejemplo.json --usar-ia --reporte reportes/demo.pdf
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from datastream.configuracion import Configuracion, ErrorConfiguracion
from datastream.ia import ClienteIA, ErrorIA, generar_resumen_local
from datastream.reporte import ErrorReporte, generar_reporte_pdf
from datastream.simulador import SimuladorDatacenter


def flotante(valor: str) -> float:
    try:
        numero = float(valor)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"'{valor}' no es un número válido.") from exc
    if not math.isfinite(numero):
        raise argparse.ArgumentTypeError("El valor debe ser finito.")
    return numero


def entero(valor: str) -> int:
    try:
        return int(valor)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"'{valor}' no es un entero válido.") from exc


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datastream",
        description=(
            "Simulador de optimización de centros de datos con SimPy, Pygame, "
            "programación dinámica y optimización no lineal."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python main.py\n"
            "  python main.py --sin-ui --duracion 60\n"
            "  python main.py --sin-ui --tasa-llegada 5 --semilla 7 --reporte reportes/demo.pdf\n"
            "  python main.py --config datos_ejemplo.json --usar-ia\n\n"
            "API de IA: exporte DATASTREAM_IA_API_KEY (o OPENAI_API_KEY) y\n"
            "opcionalmente DATASTREAM_IA_URL / DATASTREAM_IA_MODELO.\n"
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Archivo JSON de configuración (se valida antes de ejecutar).",
    )
    parser.add_argument(
        "--sin-ui",
        action="store_true",
        help="Ejecuta la simulación completa sin abrir Pygame y genera el PDF.",
    )
    parser.add_argument("--duracion", type=flotante, help="Duración simulada en segundos.")
    parser.add_argument(
        "--tasa-llegada", type=flotante, help="Paquetes por segundo (proceso Poisson)."
    )
    parser.add_argument("--semilla", type=entero, help="Semilla aleatoria reproducible.")
    parser.add_argument("--buffer", type=entero, help="Capacidad de admisión por servidor.")
    parser.add_argument(
        "--presupuesto", type=flotante, help="Presupuesto eléctrico total en kW."
    )
    parser.add_argument(
        "--mtbf-routers", type=flotante, help="Tiempo medio entre fallas de routers."
    )
    parser.add_argument(
        "--mtbf-servidores", type=flotante, help="Tiempo medio entre fallas de servidores."
    )
    parser.add_argument(
        "--reparacion", type=flotante, help="Tiempo medio de reparación de un nodo."
    )
    parser.add_argument(
        "--reporte",
        type=Path,
        help="Ruta del PDF de salida (modo --sin-ui). Por defecto, reportes/.",
    )
    parser.add_argument(
        "--sin-reporte",
        action="store_true",
        help="No genera PDF en modo consola.",
    )
    parser.add_argument(
        "--usar-ia",
        action="store_true",
        help="Solicita el análisis a la API de IA configurada; si falla, usa respaldo local.",
    )
    parser.add_argument("--ia-url", help="Endpoint chat/completions compatible con OpenAI.")
    parser.add_argument("--ia-modelo", help="Nombre del modelo de IA.")
    parser.add_argument(
        "--directorio-reportes",
        type=Path,
        default=Path("reportes"),
        help="Carpeta de informes de la interfaz (por defecto: reportes/).",
    )
    parser.add_argument(
        "--version", action="version", version="DataStream Simulator 1.0.0"
    )
    return parser


def cargar_configuracion(args: argparse.Namespace, parser: argparse.ArgumentParser) -> Configuracion:
    try:
        config = (
            Configuracion.cargar_json(args.config)
            if args.config
            else Configuracion()
        )
        reemplazos = {
            "duracion": args.duracion,
            "tasa_llegada": args.tasa_llegada,
            "semilla": args.semilla,
            "capacidad_buffer": args.buffer,
            "presupuesto_energia": args.presupuesto,
            "mtbf_router": args.mtbf_routers,
            "mtbf_servidor": args.mtbf_servidores,
            "tiempo_reparacion": args.reparacion,
        }
        for campo, valor in reemplazos.items():
            if valor is not None:
                setattr(config, campo, valor)
        config.validar()
        return config
    except ErrorConfiguracion as exc:
        parser.error(f"Configuración inválida:\n{exc}")
        raise AssertionError("parser.error debe terminar el programa")


def crear_cliente_ia(args: argparse.Namespace) -> tuple[ClienteIA | None, str | None]:
    try:
        cliente = ClienteIA(url=args.ia_url, modelo=args.ia_modelo)
    except ErrorIA as exc:
        return None, str(exc)
    if args.usar_ia and not cliente.disponible:
        return cliente, (
            "No hay clave de API ni endpoint local. El informe usará el análisis local."
        )
    return cliente, None


def resumen_consola(simulador: SimuladorDatacenter) -> str:
    metricas = simulador.metricas()
    cola = metricas["teoria_colas"]
    rutas = []
    for destino, info in sorted(simulador.rutas_actuales.items()):
        camino = " → ".join(info.get("camino", [])) if info.get("disponible") else "sin ruta"
        rutas.append(f"  {destino}: {camino}")
    motivos = metricas.get("motivos_descarte", {})
    motivos_texto = (
        ", ".join(f"{clave}={valor}" for clave, valor in sorted(motivos.items()))
        if motivos
        else "ninguno"
    )
    energia = simulador.asignacion_energia
    nombres = simulador.optimizador_energia.nombres_cluster
    reparto = ", ".join(
        f"{nombre}={valor:.2f} kW"
        for nombre, valor in zip(nombres, energia.asignacion)
    )
    return (
        "\n" + "=" * 72 + "\n"
        " DATASTREAM NETWORKS · RESUMEN DE SIMULACIÓN\n"
        + "=" * 72 + "\n"
        f"Tiempo simulado............ {metricas['tiempo_simulado']:.1f} s\n"
        f"Paquetes generados........ {metricas['paquetes_generados']}\n"
        f"Paquetes procesados....... {metricas['paquetes_procesados']}\n"
        f"Paquetes descartados...... {metricas['paquetes_descartados']}\n"
        f"Aceptación................ {metricas['tasa_aceptacion'] * 100:.1f} %\n"
        f"Throughput................ {metricas['throughput']:.3f} paq/s\n"
        f"Latencia media............ {metricas['latencia_promedio']:.3f} s\n"
        f"Latencia P95.............. {metricas['latencia_p95']:.3f} s\n"
        f"Cambios de ruta........... {metricas['cambios_ruta']} "
        f"(congestión: {metricas['cambios_ruta_congestion']})\n"
        f"Fallas/recuperaciones..... {metricas['fallas_totales']}\n"
        f"Emergencias térmicas...... {metricas['emergencias']}\n"
        f"Energía ventiladores...... {metricas['energia_ventiladores_kwh']:.4f} kWh\n"
        f"Cola M/M/c: λ={cola['lambda_total']:.2f} paq/s, μ={cola['mu']:.2f} serv/s, "
        f"ρ={cola['rho']:.3f}, Wq={cola['Wq']:.3f} s, estable={cola['estable']}\n"
        f"Lagrange: λ={energia.multiplicador_lagrange:.5f}, {reparto}\n"
        f"Rendimiento de energía.... {energia.rendimiento_total:.2f} "
        f"(+{energia.ganancia_porcentual:.2f} % vs. reparto igual)\n"
        f"Motivos de descarte....... {motivos_texto}\n"
        "Rutas finales:\n"
        + "\n".join(rutas)
        + "\n" + "=" * 72 + "\n"
    )


def ejecutar_consola(
    args: argparse.Namespace,
    config: Configuracion,
    cliente_ia: ClienteIA | None,
    aviso_ia: str | None,
) -> int:
    print(
        f"DataStream Networks · simulación de {config.duracion:.1f} s "
        f"(semilla={config.semilla}, llegadas={config.tasa_llegada:.2f} paq/s)"
    )
    simulador = SimuladorDatacenter(config)
    simulador.ejecutar()
    print(resumen_consola(simulador))

    if args.sin_reporte:
        return 0
    instantanea = simulador.instantanea()
    analisis = generar_resumen_local(instantanea)
    if args.usar_ia:
        if aviso_ia:
            print(f"Aviso IA: {aviso_ia}", file=sys.stderr)
        elif cliente_ia is not None:
            print("Consultando la API de IA para el informe...")
            try:
                analisis = cliente_ia.generar_analisis(instantanea)
                print(f"Análisis IA recibido ({analisis.fuente}).")
            except ErrorIA as exc:
                print(f"Aviso IA: {exc}. Se usará el análisis local.", file=sys.stderr)
                from datastream.ia import ResultadoIA

                analisis = ResultadoIA(
                    texto=analisis.texto,
                    fuente="Análisis determinista local",
                    error=str(exc),
                )
    try:
        ruta = generar_reporte_pdf(instantanea, analisis, args.reporte)
    except ErrorReporte as exc:
        print(f"Error al generar el PDF: {exc}", file=sys.stderr)
        return 1
    print(f"Informe PDF generado: {ruta}")
    return 0


def ejecutar_gui(
    config: Configuracion,
    args: argparse.Namespace,
    cliente_ia: ClienteIA | None,
    aviso_ia: str | None,
) -> int:
    # La importación se hace aquí para que el modo consola funcione sin Pygame.
    from datastream.visualizacion import ErrorVisualizacion, ejecutar_dashboard

    if aviso_ia:
        print(f"Aviso IA: {aviso_ia}", file=sys.stderr)
    simulador = SimuladorDatacenter(config)
    try:
        ejecutar_dashboard(
            simulador=simulador,
            config=config,
            cliente_ia=cliente_ia,
            usar_ia=args.usar_ia,
            directorio_reportes=args.directorio_reportes,
        )
    except ErrorVisualizacion as exc:
        print(f"Error de interfaz: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = construir_parser()
    args = parser.parse_args(argv)
    config = cargar_configuracion(args, parser)

    if args.reporte and args.reporte.exists() and args.reporte.is_dir():
        parser.error("'--reporte' debe apuntar a un archivo PDF, no a una carpeta.")
    cliente_ia, aviso_ia = crear_cliente_ia(args)

    if args.sin_ui:
        return ejecutar_consola(args, config, cliente_ia, aviso_ia)
    return ejecutar_gui(config, args, cliente_ia, aviso_ia)


if __name__ == "__main__":
    raise SystemExit(main())

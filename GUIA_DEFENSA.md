# Guía de defensa · DataStream Networks

Esta guía resume el orden de exposición, las fórmulas que deben poder explicarse
y las preguntas técnicas más probables. La defensa sugerida dura entre 10 y 15
minutos, incluyendo la demostración en vivo.

## 1. Preparación previa

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
python main.py --sin-ui --duracion 30 --semilla 7 --reporte reportes/prueba2.pdf
```

Comprobar antes de entrar al aula:

- El dashboard abre con `python main.py`.
- La tecla `F5` genera el PDF.
- Existe `reportes/prueba2.pdf` como respaldo si falla la generación en vivo.
- `reportes/dashboard_demo.png` sirve como captura de respaldo.
- Si no hay clave de IA, el informe usa el análisis local automáticamente.

## 2. Guion de exposición

### 2.1 Introducción (1 minuto)

> DataStream Networks necesita reducir latencia, congestión y consumo
> energético. El proyecto integra tres módulos en un mismo reloj SimPy:
> enrutamiento y admisión con programación dinámica, optimización térmica y
> energética con cálculo diferencial, y simulación visual con Pygame. El
> resultado se exporta a un informe PDF con análisis de IA o un respaldo local.

Mencionar las decisiones de diseño:

- Programación orientada a objetos y separación por módulos.
- Validación antes de construir la simulación.
- Reproducibilidad mediante semilla.
- Fallback local para que el PDF siempre se genere aunque la API falle.

### 2.2 Arquitectura (1 minuto)

Mostrar `main.py` y explicar el flujo:

```text
CLI / JSON -> Configuracion.validar()
           -> SimuladorDatacenter (SimPy)
           -> Dashboard (Pygame) o resumen de consola
           -> instantanea() -> análisis IA/local -> PDF
```

Archivos clave:

- `datastream/configuracion.py`: validación central.
- `datastream/simulador.py`: procesos, tráfico, fallas y métricas.
- `datastream/red.py` y `datastream/mochila.py`: módulo 1.
- `datastream/termodinamica.py` y `datastream/lagrange.py`: módulo 2.
- `datastream/visualizacion.py`: dashboard.
- `datastream/ia.py` y `datastream/reporte.py`: análisis y salida PDF.

### 2.3 Demostración del dashboard (4 minutos)

1. Abrir `python main.py`.
2. Señalar la topología: Internet, routers R1–R4 y servidores S1–S4.
3. Explicar los colores de los nodos y las rutas resaltadas.
4. Mostrar los paquetes viajando y el panel de tráfico.
5. Presionar `TAB` o hacer clic en un servidor para enfocar su ruta.
6. Bajar la velocidad o pausar para leer los KPIs.
7. Señalar los gráficos de temperatura, ventilador, energía y Lagrange.
8. Presionar `F5` y mostrar el aviso de generación del PDF.

Puntos que conviene nombrar:

- La ruta puede cambiar cuando la latencia efectiva de un enlace sube.
- La histéresis evita que un cambio pequeño de congestión produzca parpadeos.
- Las fallas de routers y servidores se generan con distribuciones exponenciales.
- Los recursos CPU/RAM se liberan al completar o perder una tarea.

### 2.4 Módulo 1: programación dinámica (2 minutos)

**Ruta más corta.**

```text
w(u, v) = w0 * (1 + beta * u^1.35)
d_k(v) = min(d_(k-1)(v), min_u[d_(k-1)(u) + w(u, v)])
```

- `u` es la utilización suavizada del enlace.
- Bellman-Ford es programación dinámica: cada ronda agrega un salto.
- Se usa sobre un grafo no dirigido con latencias positivas; no puede haber
  ciclos negativos.
- Ante una falla, el enrutamiento recalcula y puede encontrar un camino
  alternativo.

**Mochila 0/1 bidimensional.**

```text
V(i, c, r) = max(V(i-1, c, r), V(i-1, c-cpu_i, r-ram_i) + valor_i)
```

- Cada servidor tiene capacidad de CPU y RAM.
- El paquete es indivisible: se acepta completo o se rechaza.
- El valor depende de la prioridad y del tamaño.
- La tabla tridimensional permite reconstruir la selección exacta.

### 2.5 Módulo 2: optimización no lineal (3 minutos)

**Modelo térmico.**

```text
T(c) = 73.3333*c^3 - 187*c^2 + 145.2*c + 25
T'(c) = 220*c^2 - 374*c + 145.2
T''(c) = 440*c - 374
```

Resultados para la defensa:

- `T'(c) = 0` en `c = 0.60` y `c = 1.10`.
- `T''(0.60) = -110`: máximo local, aproximadamente `60.64 °C`.
- `T''(1.10) = 110`: mínimo local.
- `T''(c) = 0` en `c = 0.85`: punto de inflexión.
- El máximo local es el punto que dispara refrigeración de emergencia.

**Gradiente.**

```text
P(f, L) = P0 + cv*f^3 + c1*max(0, T-Tobj)^2 + c2*max(0, T-Temer)^2
```

- El gradiente `dP/df` se calcula analíticamente.
- Se aplica descenso proyectado para mantener `f` entre 0.1 y 1.0.
- Se usa retroceso de paso cuando un intento aumenta la potencia.
- Cada servidor tiene su propio controlador y suavizado térmico.

**Lagrange.**

```text
max  sum(w_i * sqrt(e_i))
s.a. sum(e_i) = E

e_i = E * w_i^2 / sum(w_j^2)
lambda = 0.5 * sqrt(sum(w_j^2) / E)
```

- `lambda` es el valor marginal del presupuesto: cuánto mejora el rendimiento
  aproximado al agregar una unidad de energía.
- En el óptimo, todos los rendimientos marginales igualan a `lambda`.
- El código verifica la restricción y las condiciones KKT numéricamente.

### 2.6 Módulo 3: simulación y reporte (2 minutos)

- Las llegadas son un proceso Poisson con tasa configurable.
- Los tiempos de servicio son exponenciales.
- Se reporta M/M/c con `c = 4`, `rho`, `Lq`, `Wq`, `L` y `W`.
- Las fallas usan MTBF exponencial y reparación exponencial.
- El motor registra paquetes generados, aceptados, procesados, descartados y
  motivos de descarte.
- El PDF se genera con un motor propio y contiene portada, resumen, los tres
  módulos, análisis de IA/local y conclusiones.
- La IA se consulta por HTTP con un payload JSON; no se usa ninguna clave
  embebida.

### 2.7 Cierre (1 minuto)

> El simulador permite observar cómo una decisión de enrutamiento afecta la
> cola, cómo la carga afecta la temperatura y cómo el presupuesto eléctrico se
> redistribuye. La validación, la semilla y el informe reproducible hacen que
> los resultados sean defendibles y verificables.

## 3. Preguntas probables y respuestas

### ¿Por qué Bellman-Ford y no Dijkstra?

Bellman-Ford se implementa como programación dinámica por rondas, que es el
enfoque solicitado y además tolera grafos con pesos que cambian. Aunque las
latencias son positivas, la formulación por relajaciones permite explicar la
recurrencia y el número de iteraciones de forma clara. Dijkstra sería válido y
más rápido, pero no encaja igual de directo con la exposición de programación
dinámica.

### ¿Cómo se evita que la ruta cambie todo el tiempo?

Se compara la nueva latencia con la anterior. El cambio solo se aplica si la
mejora es de al menos 15 % y han pasado 6 segundos desde el último cambio de ese
servidor. Además, la utilización se suaviza con EWMA.

### ¿La mochila es realmente óptima?

Sí. La tabla `V(i,c,r)` evalúa todas las combinaciones relevantes de capacidad
entera de CPU y RAM y la reconstrucción hacia atrás recupera una solución óptima
para la instancia discretizada. La complejidad es
`O(n * CPU * RAM)` en tiempo y memoria.

### ¿Por qué el máximo térmico no es el punto más caliente del rango?

Porque el modelo cúbico representa el efecto de throttling después del máximo.
El máximo local está en `c = 0.60`; el mínimo local en `c = 1.10` y el punto de
inflexión en `c = 0.85`. El punto de emergencia se toma del máximo local.

### ¿El gradiente encuentra el mínimo global?

El problema se resuelve sobre un intervalo acotado y con una función que combina
un término cúbico creciente con penalizaciones térmicas. El descenso proyectado
con retroceso converge a un mínimo local; para la defensa se presenta como
método numérico de ajuste en tiempo real, no como una prueba de optimalidad
global. Los límites físicos del ventilador se respetan siempre.

### ¿Qué significa lambda en Lagrange?

Es el multiplicador asociado a la restricción de presupuesto. En el óptimo,
coincide con el rendimiento marginal de cada clúster. Un `lambda` mayor indica
que una unidad adicional de energía tendría más valor marginal; el presupuesto
está más restrictivo.

### ¿Por qué M/M/c y no M/M/1?

Cada servidor puede atender hasta cuatro tareas concurrentes mediante un
`PriorityResource`. Por eso se usa `c = 4` y `rho = lambda/(c*mu)`, no un modelo
de un solo canal.

### ¿Qué pasa si la simulación no es estable?

Si `rho >= 1`, las métricas de cola se reportan como infinitas y el sistema se
marca como no estable. La interfaz y el informe muestran esa condición para que
sea evidente que la capacidad es insuficiente.

### ¿Cómo se valida la entrada?

`Configuracion.validar()` comprueba tipos, valores finitos, rangos, longitudes,
pesos y relaciones entre parámetros. Los argumentos CLI se convierten y validan
antes de crear el simulador. Los JSON rechazan claves desconocidas. Los errores
se muestran con mensajes que indican el campo y el rango esperado.

### ¿Qué ocurre si no hay Internet o clave de IA?

El cliente lanza un error controlado y el sistema genera un análisis local
determinista con las métricas de la corrida. El PDF se produce igualmente. La
clave se lee de variables de entorno y nunca se escribe en el proyecto.

### ¿Cómo se garantiza la reproducibilidad?

Se usan tres generadores aleatorios con semilla: tráfico, servicio y fallas. Dos
ejecuciones con la misma configuración y semilla generan las mismas métricas
principales. Esto se verifica en `tests/test_simulador.py`.

### ¿Cuál es la complejidad del sistema?

- Bellman-Ford: `O(V*E)` por consulta.
- Mochila 2D: `O(n*C_cpu*C_ram)`.
- Lagrange: `O(k)` para `k` clústeres.
- Gradiente: costo constante por iteración (cinco iteraciones por servidor y
  segundo simulado).
- SimPy procesa eventos de forma discreta, no en tiempo continuo.

### ¿Por qué el PDF no usa ReportLab?

Para evitar dependencias adicionales y demostrar la construcción directa de
objetos PDF, fuentes, tablas y gráficos vectoriales. El archivo resultante es
autocontenido y se puede abrir con cualquier lector PDF.

### ¿Dónde se ve la integración de la IA?

En `datastream/ia.py`. El cliente construye un resumen JSON de la instantánea,
hace un `POST` a `/chat/completions`, extrae el texto de la respuesta y lo pasa
al generador de reportes. La UI lo ejecuta en un hilo para no congelar Pygame.

### ¿Qué mejoraría con más tiempo?

- Comparar el enrutamiento con Dijkstra y A*.
- Añadir ventanas de congestión configurables.
- Incorporar más clústeres y servidores heterogéneos.
- Exportar también CSV/JSON de las métricas.
- Añadir pruebas de rendimiento y perfiles de carga.

## 4. Evidencias que conviene tener abiertas

- `datastream/configuracion.py`: validación.
- `datastream/red.py`: Bellman-Ford y latencia efectiva.
- `datastream/mochila.py`: recurrencia DP y reconstrucción.
- `datastream/termodinamica.py`: derivadas y gradiente.
- `datastream/lagrange.py`: solución analítica y KKT.
- `datastream/simulador.py`: procesos SimPy y métricas.
- `datastream/visualizacion.py`: dashboard.
- `datastream/ia.py` y `datastream/reporte.py`: API y PDF.
- `tests/`: 53 pruebas.
- `reportes/prueba2.pdf` y `reportes/dashboard_demo.png`.

## 5. Comandos de emergencia

```bash
# Verificar que todo compila e importa
python -m compileall -q main.py datastream tests

# Ejecutar pruebas
python -m unittest discover -s tests -v

# Generar el informe de respaldo
python main.py --sin-ui --duracion 30 --semilla 7 --reporte reportes/prueba2.pdf

# Abrir el dashboard
python main.py

# Ver todas las opciones
python main.py --help
```

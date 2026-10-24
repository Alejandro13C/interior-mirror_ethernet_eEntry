# Ensayos y resultados

Fecha de corte: 24/10/2026. Los identificadores E01–E16 remiten al [índice de evidencias](05-evidencias.md). Se distinguen registros de ejecución, resúmenes elaborados a partir de ellos y confirmaciones del usuario.

## Definiciones de medida

| Métrica | Qué mide y qué excluye |
|---|---|
| FPS de vídeo | Fotogramas mostrados / duración del proceso, incluyendo arranque del tramo; no frecuencia de inferencia |
| Tiempo `detect()` | Letterboxing, ejecución del modelo y filtrado/NMS; en el trabajador excluye la conversión previa I420→BGR |
| Edad de resultado | Timestamp local de la imagen → final del análisis; no exposición física de cámara → resultado |
| Tiempo local de vídeo | Primer paquete recibido → llegada del buffer al sink; excluye codificación de cámara y refresco físico del panel |
| CPU total | Fracción de toda la máquina: cuatro núcleos en la Pi y doce CPU lógicas en el PC |
| CPU por proceso a escala de un núcleo | Puede superar 100 %; 100 % equivale a un núcleo completo |
| RAM RSS | Memoria residente del proceso, incluidas páginas compartidas; no memoria exclusiva de los pesos |

Las medidas de CPU/RAM de `measure-detector.py` son muestras cada aproximadamente 2 s. Su campo `rss_peak_mib` es el máximo RSS observado durante el muestreo, no necesariamente el máximo instantáneo de toda la vida del proceso. No deben sumarse RSS como si todas sus páginas fueran privadas.

## Campañas en PC

### Corpus y cobertura

Se utilizaron seis vídeos externos de carreras con vista trasera. Se conservaron sus fps y se identificaron por SHA-256; los originales siguen fuera del repositorio, en la ubicación indicada por los manifiestos. No se aplica Panini a estos vídeos porque no se conoce la calibración de sus cámaras.

| Clip | Fotogramas | FPS originales |
|---|---:|---:|
| 01 Winchester | 7.006 | 29,970 |
| 02 Unity | 5.319 | 30 |
| 03 Hornet | 5.142 | 29,970 |
| 04 Dwarf | 5.559 | 29,970 |
| 05 Texana | 5.475 | 30 |
| 06 X5M | 10.349 | 29,970 |
| **Total** | **38.850** | — |

La primera campaña completa comprende cinco configuraciones: NanoDet-256/416, YOLOv5n-640, YOLOv5s-640 y YOLO11n-640: **194.250 resultados modelo-fotograma**. La segunda añade YOLO26n-640, PicoDet-XS320 y PicoDet-S320: **116.550 resultados**. Son ocho configuraciones sobre los mismos 38.850 fotogramas, no 310.800 imágenes independientes. Evidencias E06 y E07.

Las verificaciones comprueban número de resultados, fps, longitud y decodificación al inicio/mitad/final de los vídeos. **No comprueban la corrección de cada detección.** Los vídeos se renderizan offline y pueden reproducirse a 30 fps aunque el cálculo haya tardado más que la duración del original.

### Comparación de motores y recursos

PC de la campaña: Intel i7-8700, doce CPU lógicas y aproximadamente 31 GB de RAM. La ronda inicial comparó 20 configuraciones sobre 30 imágenes prefijadas, con cinco calentamientos y tres repeticiones por imagen. La ampliación solicitó 18 configuraciones CPU; 14 figuran disponibles, con incidencias conservadas en su resumen. No se afirma que todos los motores funcionaran con todos los modelos.

La tabla siguiente combina la revisión de calidad y el mejor perfil CPU disponible recogido para cada modelo en el estudio. **Calidad y recursos proceden de campañas distintas**, enlazadas por modelo/configuración; no constituyen una única ejecución conjunta. Fuentes: E08, E09 y E10.

| Perfil PC | Aciertos de muestra | Duplicados | FP sin objetivo perseguidor | Vehículos no objetivo | Alarmas falsas totales | Posición incorrecta | Dudosos | RAM RSS MiB | CPU total % | Análisis medio ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| YOLOv5n-640 | 84 | 9 | 8 | 20 | 28 | 3 | 0 | 229,88 | 24,33 | 31,94 |
| NanoDet-256 | 60 | 31 | 11 | 21 | 32 | 4 | 0 | 119,71 | 8,36 | 9,33 |
| YOLO11n-640 | 88 | 5 | 3 | 41 | 44 | 2 | 2 | 244,01 | 32,67 | 25,89 |
| YOLO26n-640 | 84 | 27 | 4 | 41 | 45 | 2 | 0 | 243,70 | 32,15 | 22,48 |
| YOLOv5s-640 | 89 | 6 | 6 | 41 | 47 | 4 | 1 | 279,67 | 28,93 | 68,76 |
| PicoDet-XS320 | 75 | 29 | 38 | 33 | 71 | 4 | 0 | 200,45 | 21,34 | 8,34 |
| NanoDet-416 | 83 | 45 | 25 | 61 | 86 | 8 | 0 | 119,74 | 8,39 | 21,98 |
| PicoDet-S320 | 82 | 34 | 68 | 59 | 127 | 5 | 6 | 205,19 | 22,08 | 9,17 |

Los perfiles NanoDet usan NCNN/un hilo; el resto de esta tabla usa OpenVINO/cuatro hilos. RAM incluye Python, bibliotecas y muestras precargadas. Los tiempos excluyen lectura/codificación de vídeo. **Estas cifras no predicen el rendimiento en la Pi.** Algunas campañas previas usaron distintas entradas de imagen entre familias; no son ensayos de una sola variable.

### Cómo se revisaron los falsos avisos

La revisión empleó **69 fotogramas**, ocho configuraciones y **1.352 apariciones de triángulos**. Se revisaron visualmente los 555 marcadores antes no asociados, agrupados en 341 grupos; las asociaciones correctas/duplicadas previas no se reanotaron todas desde cero. Existen 69 mosaicos clasificados y decisiones por marcador. Evidencia E09.

- Acierto: asociación previa o vehículo perseguidor adicional identificado visualmente.
- Duplicado: otra marca sobre el mismo vehículo en ese fotograma/modelo.
- FP sin objetivo perseguidor: señal, valla, fondo, fotografía de coche o detección incoherente sin perseguidor en el anclaje.
- Vehículo no objetivo: carrocería propia o vehículo aparcado/de servicio fuera del objetivo operativo.
- Alarma falsa total: suma de las dos categorías anteriores; no equivale exclusivamente a un error de clasificación «vehículo».
- Error de posición y dudoso: categorías separadas; los dudosos no se convierten en falsos positivos confirmados.

Las anotaciones son exploratorias, realizadas con asistencia del agente, sin revisión independiente certificada. Los recuentos son **apariciones**, no coches distintos ni falsas alarmas por minuto. No hay referencia exhaustiva para obtener precisión/recall/mAP globales ni un porcentaje de éxito sobre los 38.850 fotogramas.

YOLOv5n se eligió provisionalmente por su equilibrio en esta muestra y recursos, no como ganador universal ni como detector ya validado para pista. Sus seis vídeos individuales reutilizan las predicciones guardadas, dibujan solo triángulos blancos y no eliminan manualmente falsos positivos. Evidencia E11.

## Ensayos en Raspberry Pi 3A+

### Selección inicial de entrada cuadrada

Vídeo activo, detector anterior detenido, imagen de referencia y mediciones calentadas, NCNN nativo. Los RSS corresponden al ejecutable de benchmark, no al trabajador de producción. Evidencia E02.

| Entrada cuadrada | Hilos | Análisis medio ms | RSS máximo del benchmark MiB |
|---|---:|---:|---:|
| 640 | 2 | 1.352,38 | 141,28 |
| 640 | 3 | 1.362,68 | 143,06 |
| 416 | 2 | 562,17 | 97,87 |
| 320 | 2 | 321,75 | 84,43 |
| 320 | 3 | 316,05 | 84,46 |

640 excedía el TTL de 750 ms y no resultaba válido para esa integración. Elegir 320 redujo detalle respecto a PC-640: esa concesión está reconocida y necesita validación propia de calidad. No se concluyó que todo el hardware estuviera agotado; se continuó optimizando.

### Sobrecarga observada y recuperación

El aumento temporal de exportación de imágenes empeoró la carga. Al retirar el detector se recuperó el vídeo; el usuario confirmó la fluidez. Después se reintrodujo la IA con entrada rectangular, una imagen pendiente, afinidad de CPU y simplificación equivalente de Panini. Evidencias E03 y E04.

| Perfil | Duración de vídeo s | Mostrados | FPS | Descartes por edad en pantalla | Tiempo local medio ms | CPU total en muestra separada |
|---|---:|---:|---:|---:|---:|---:|
| Integración sobrecargada | 184,753 | 2.764 | 14,96 | 1.626 | 132,73 | 79,29 % |
| Vídeo solo | 184,436 | 4.565 | 24,75 | 12 | 111,94 | 22,67 % |
| Integración optimizada final | 78,897 | 1.816 | 23,02 | 64 | 122,47 | 55,03 % |

Son **tramos secuenciales en directo**, con duraciones, escenas y muestras de CPU distintas; no un ensayo A/B sincronizado. Los descartes de pantalla no explican por sí solos todas las diferencias de fps: también intervienen imágenes dañadas, colas y arranque. El tramo optimizado tuvo 1.907 imágenes entregadas al pipeline. El p95 local comunicado es inferior a 135 ms, excluyendo cámara y panel.

### Recursos de la integración optimizada

Muestra de 30,008 s; cuatro CPU lógicas; 51,54 °C de temperatura máxima; `get_throttled=0x0`. Evidencia E04.

| Proceso | CPU a escala de un núcleo | CPU sobre toda la Pi | RSS máximo muestreado |
|---|---:|---:|---:|
| Vídeo | 99,01 % | 24,75 % | 18,34 MiB |
| Detector | 105,64 % | 26,41 % | 39,25 MiB |

CPU total del sistema: 55,03 %. Memoria disponible mínima: 249,75 MiB. No se midió consumo eléctrico en W ni energía en Wh. `get_throttled=0x0` describe esa observación, no todas las condiciones de alimentación posibles.

### Regresiones de la optimización

| Ensayo | Resultado registrado | Alcance |
|---|---|---|
| ONNX→NCNN, seis imágenes | Error absoluto máximo de cabezas ≈4,15×10⁻⁵ | Paridad numérica en la muestra; E02 |
| Cuadrado→rectángulo, doce imágenes de seis vídeos | 369,93→225,24 ms; 15 detecciones en cada perfil; 15 emparejadas, IoU mínimo 0,804 | No reduce el tamaño útil escalado; no demuestra igualdad de precisión en todo el corpus; E04 |
| IPC real con referencia positiva | Primera observación oculta, siguientes confirmadas con ID estable; 293,0/272,1/278,9 ms de edad local, incluyendo 50 ms iniciales impuestos por el test; entrada antigua rechazada | Referencia de tráfico, no reconocimiento continuo de la maqueta ni latencia física de cámara; E04 |
| Antigüedad de resultados en muestra final | 162 análisis, 0 expirados, 0 resultados tardíos; media de análisis 224,32 ms, edad media 361,32 ms y máxima 621,50 ms | Contadores temporales de esa muestra; 0 vehículos confirmados; E04 |
| Panini antes/después | 576.000 bytes iguales y SHA-256 coincidente; medias de tres ensayos 21,49→20,80 ms | ≈3,2 % de mejora media observada, con ruido; mismo fotograma I420 determinista y perfil; E04 |
| Caída forzada del detector | Reinicio automático; PID del vídeo sin cambiar | No prueba todas las pérdidas de señal o alimentación; E05 |

## Pruebas automatizadas y evidencia histórica

| Prueba | Qué comprueba | Evidencia de ejecución disponible |
|---|---|---|
| `make test` | Reconstrucción RTP, geometría Panini, protocolo, caducidad y seguimiento | Reejecutado al preparar este dossier, tres ejecutables superados; salida en [trazabilidad](04-trazabilidad-y-reproduccion.md) |
| `make test-video` | Filtro I420, fondo, colocación, caducidad y pérdida del trabajador | Informe histórico E13; ensayo del overlay optimizado registrado en la sesión de desarrollo; no se ha reejecutado al documentar |
| `tests/test_detector_ipc.py` | Trabajador real, coordenadas, confirmación, ID, frescura y rechazo de entrada antigua | Resumen E04 y ejecución durante la optimización; no hay log bruto completo adjunto al resumen |
| `tests/test_vehicle_overlay.c` | Una imagen pendiente, triángulo, observación vacía y expiración sin renovar TTL | Código actual y ejecución superada durante la optimización; no equivale a percepción real |
| `make test-capture` | Captura PCAP de referencia de la cámara | Ensayo histórico; el PCAP ya no está en `captures/camera.pcap`, no reproducible con esa ruta en esta copia |
| `make test-detector IMAGE=...` | Cajas del fallback NanoDet sobre referencia | No valida YOLOv5n; alcance histórico en E13 |
| ASan/UBSan | Memoria/comportamiento indefinido en pruebas de protocolo/seguimiento | Declarado en informe histórico E13; sin registro bruto independiente incorporado a este dossier |

Los fuentes están en [tests](../tests/). La existencia de una prueba o de un servicio activo no se considera por sí sola resultado superado.

## Resultado abierto de percepción

El usuario confirma **vídeo fluido sin triángulo visible** con el perfil optimizado. La [captura guardada durante la optimización](../comparisons/yolov5n_raspberry-pi-3Aplus/optimized/live-car-0.png) contiene una maqueta y el resumen final registra cero detecciones confirmadas. En la lectura SSH del 24/10 los contadores seguían en cero vehículos confirmados.

Por tanto, se ha mejorado la ejecución conjunta, pero **no se declara resuelta la detección continua ni demostrada su causa**. Faltan diagnóstico de las predicciones sin filtrar, contraste de escena/preprocesado/modelo y validación con vehículos reales aproximándose y adelantando.

### Actualización de diagnóstico — 24/10/2026

Se capturaron tres entradas actuales y se ejecutó un inspector nativo aislado. Los umbrales y el binario de producción no se cambiaron. El inspector permite consultar otras clases, bajar el umbral solo offline y obtener la máxima puntuación de vehículo antes de los filtros. Evidencias en [detection-diagnostic](../comparisons/yolov5n_raspberry-pi-3Aplus/detection-diagnostic/).

| Entrada | Comprobación con el perfil 320 rectangular | Resultado |
|---|---|---|
| Captura del Lego que se reconocía el 23/10 | Mismo inspector de hoy; umbral 0,15 | Sigue reconociendo coche, score 0,261545 |
| Captura actual del Lego | Máximo sobre las cuatro clases de vehículo, antes de filtrar | Score máximo 0,00417456; ninguna detección de vehículo retenida con umbrales 0,15 ni 0,01 |
| Fotografía de tráfico de control | Misma configuración | Coche principal 0,802282; el trabajador de producción confirma su pista por IPC |

En la captura actual, de 3.780 anchors, 3.749 se descartan por objectness y los otros 31 por clase con el umbral 0,15. El problema de esa imagen aparece antes de seguimiento y dibujo. La misma captura no produjo vehículos a 416 ni 640 con umbral 0,15; esta última observación no es una medida de sus puntuaciones máximas.

La imagen antigua aún funciona con los pesos actuales: el hecho de ser Lego no basta para explicar la diferencia. El reconocimiento es sensible a la entrada, pero **no se ha aislado la causa física concreta**. Pruebas exploratorias en PC con giros, gamma y contraste no recuperaron el umbral 0,15 en la imagen actual; no se desplegaron como preprocesado. ORT dinámico reprodujo las puntuaciones nativas de ambas imágenes antes de esas pruebas.

La confirmación IPC de control dio cuentas 0/1/1 e ID estable, con edades locales 369,4/356,3/272,0 ms, incluidos 50 ms impuestos por el test. No demuestra el triángulo en la pantalla real a partir de la cámara. Queda pendiente repetir escena/postura de forma controlada con el mismo Lego y comprobar finalmente el dibujo en directo. No se considera solucionada la continuidad.

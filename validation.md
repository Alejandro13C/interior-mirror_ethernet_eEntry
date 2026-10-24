# Validación — 21/10/2026

Pi 3A+, OS Lite 32-bit, pantalla 800×480. Panini 130° activo; lente equidistante de 180° supuesta, sin calibrar.

## Vídeo

| Modo | Duración | Mostrados | Tiempo local medio |
|---|---:|---:|---:|
| Original, prueba anterior | 438,9 s | 10969 | 87,7 ms |
| Panini 130°, sin detector | 28,8 s | 690 | 111,3 ms |
| Panini 130° + NanoDet-Plus 256, activo | 106,7 s | 2573 | 119,5 ms |

Último tramo: ~24,1 fps incluyendo el arranque; percentil 95 inferior a 134 ms. 33 imágenes dañadas y 33 descartadas por superar 150 ms; sin descartes del socket. El tiempo local empieza en el primer paquete recibido y termina en la entrada de pantalla: excluye cámara y refresco del panel.

## Detección y posición

- NanoDet-Plus-m oficial, pesos entrenados a 416 y entrada reducida a 256; umbral 0,40, un hilo y análisis hasta 3 Hz. Inferencia media en directo: 197 ms en la escena de la mesa.
- Foto de tráfico: detecta coche cercano de frente y lejano (scores 0,79 y 0,51). Solapamiento de cajas con anotaciones manuales aproximadas: 0,93 y 0,76. El modelo anterior omitía el cercano. Es una regresión sobre una imagen, no una evaluación general de precisión.
- Dos observaciones asociadas dentro de 750 ms antes de publicar un marcador. Caducidad de 750 ms desde recepción; predicción lineal limitada a 500 ms y a media caja, calculada para el instante del vídeo mostrado. Puede equivocarse en giros, aceleraciones o cambios de identidad.
- `make test` y ASan/UBSan: protocolo, seguimiento, velocidades y límites de predicción. `make test-video` en la Pi: posición del triángulo, rechazo de detección aislada, caducidad y pérdida del trabajador. `make test-detector IMAGE=...`: ambos coches de referencia. IPC real: primera observación oculta, siguientes confirmadas con identidad estable y rechazo de entrada antigua; 250–356 ms hasta respuesta en esa prueba.

## Recursos con la mejora activa

60,2 s, muestras cada 2 s. CPU por proceso: 100 % equivale a un núcleo; la Pi dispone de cuatro. RAM RSS puede contar páginas compartidas en ambos procesos.

| Proceso | CPU media / máxima | RAM media / máxima |
|---|---:|---:|
| Vídeo + Panini | 94,7 / 97,2 % | 18,41 / 18,48 MiB |
| Detector Plus | 57,5 / 61,9 % | 18,91 / 19,43 MiB |

Sistema completo: CPU media 43,1 % de los cuatro núcleos, máxima 48,5 %; RAM usada media 177,0 MiB de 425,0 MiB visibles para Linux (total menos MemAvailable), 248,0 MiB disponibles. Temperatura final 55,8 °C; `get_throttled=0x80000`. Escenas y cachés del sistema pueden variar entre pruebas.

## Recuperación y límites

Arranque automático, fondo negro en tty7 confirmado por el usuario, Wi-Fi tras reinicio y caída forzada del detector sin reinicio del vídeo comprobados. CPU del trabajador limitada a un núcleo. El kernel desactiva el controlador de memoria: `MemoryMax` no es efectivo; `LimitAS=192M` limita su espacio de direcciones.

La maqueta no se reconoce de forma fiable. Faltan coches reales aproximándose y adelantando, validación visual de movimiento, noche/lluvia, pruebas prolongadas, alimentación de coche y latencia de cámara a pantalla. Sin radar, distancia medida ni avisos de colisión validados.

## Vídeo de carreras en PC

Texana Raceway, vista trasera: 182,5 s, 5475 imágenes a 30 fps. Salida completa 800×480, con audio y sin Panini. Vídeo final: `comparisons/texana_YOLOv5n640_frame-by-frame/annotated.mp4`.

- Comparación sobre 9 fotogramas con 20 cajas anotadas visualmente de forma aproximada: NanoDet-256 con umbral 0,40 localiza 3; YOLOv5n-640 con umbral 0,15 localiza 20 y añade una caja falsa. Umbral seleccionado con 4 fotogramas/10 coches; los otros 5/10 quedan para comprobación (10 aciertos y una marca adicional). Muestra pequeña y correlacionada; no es mAP ni precisión general en carreras.
- Perfil final PC: análisis de cada fotograma, 4 hilos OpenCV, sin retraso añadido ni simulación de edad de pantalla. Umbral de seguimiento 0,15, arranque de pista 0,25, dos observaciones, asociación común coche/bus/camión y conservación de pistas hasta 100 ms ante una omisión, limitada además por la captura original de 750 ms.
- Marcadores finales en la muestra: tras la confirmación inicial, 18/18 cajas coinciden (IoU ≥0,5) y una marca adicional. El fotograma inicial no muestra aún sus dos coches por la confirmación. Persisten omisiones de vehículos muy lejanos, cajas duplicadas y fragmentación de IDs fuera de esta muestra.

| Prueba PC | Análisis medio | Procesado completo | CPU tarea / 12 hilos | RAM RSS máxima sumada |
|---|---:|---:|---:|---:|
| NanoDet-256, 3 Hz, retraso simulado 200 ms | 11,7 ms | 96,3 fps | 31,1 % | 259,5 MiB |
| YOLOv5n-640, cada fotograma, sin retraso añadido | 45,3 ms | 19,1 fps | 31,3 % | 295,9 MiB |

El vídeo final tarda 287,2 s en procesar 182,5 s: todavía no procesa 30 fps en tiempo real. CPU y RAM incluyen lectura, dibujo y codificación H.264/audio; RSS puede contar páginas compartidas dos veces. Proceso de análisis YOLO: pico 195,1 MiB. Cambian modelo, resolución, cadencia y simulación temporal; la tabla no aísla una sola variable ni predice velocidad en la Pi.

Comprobados: 5475 imágenes, H.264/audio AAC; inicio, mitad y final decodificables. Regresiones PC/nativo, conversión ONNX de cajas/scores, NMS, confirmación de coches quietos, cambio coche/camión, umbral doble y caducidad durante omisiones superadas. La Pi mantiene su configuración anterior; falta optimizar y medir el nuevo detector antes de desplegarlo allí. Anotaciones en `tests/fixtures/texana_boxes.json`; evaluaciones y fotogramas de revisión en `comparisons/texana-model-evaluation_9frames_20vehicles`.

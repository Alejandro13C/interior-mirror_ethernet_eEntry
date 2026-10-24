# Trazabilidad, configuración y reproducción

Fecha: 24/10/2026. Este documento identifica la copia inspeccionada; no asigna retrospectivamente un commit a campañas antiguas. No se ha identificado metadato Git de esta copia de trabajo. Las huellas de código actuales no demuestran por sí solas qué revisión ejecutó cada ensayo histórico.

## Lectura del despliegue durante la preparación

Lectura de solo consulta por SSH iniciada a **2026-10-24T07:51:32Z** (09:51:32 en Europe/Madrid). Transcripción de los valores recibidos:

| Elemento | Resultado |
|---|---|
| `rear-camera` | `active`, `enabled`, PID 4888, `NRestarts=0`, afinidad 0–1 |
| `rear-camera-detector` | `active`, `enabled`, PID 4970, `NRestarts=0`, afinidad 2–3 |
| Parámetros efectivos | YOLOv5n, tamaño máximo 320, rectangular 1, dos hilos, observación 0,15, alta 0,25; Panini 130° y detección habilitados |
| Última línea de detector en esa lectura | 62.374 análisis; `vehicles=0`, `expired=0`, `late_results=0`, `confirmed_frames=0`; análisis medio acumulado 205,344 ms |
| Memoria cgroup | `cgroup_disable=memory` presente en el arranque |

La media acumulada de esa lectura no es la muestra de 30 s de E04, ni prueba el reconocimiento de coches. No se han reiniciado servicios ni repetido ensayos de carga en la Pi para redactar este dossier. El último comentario del usuario confirma fluidez y ausencia de triángulo; se mantiene ese hallazgo abierto.

### Cambios posteriores al snapshot

Tras preparar el dossier se continuó el diagnóstico de la ausencia de triángulos el 24/10. Se añadieron opciones de inspección offline en `src/yolov5.hpp` y `tools/benchmark-yolov5.cpp`; por tanto, la huella local posterior del encabezado ya no coincide con la tabla del snapshot. El binario de producción conserva la huella `ef0303ae29390d731d5499756eadc4489ea866d707904b0ae138a9b6ab0751a9` y los pesos no cambiaron. La nueva instrumentación no se desplegó en el servicio.

Para capturar entradas se detuvo brevemente solo el trabajador y se restauró automáticamente, manteniendo el vídeo. Esto pertenece al diagnóstico posterior, no a la consulta documental de las 07:51 UTC. Los resultados y límites están en la actualización de [ensayos](02-ensayos-y-resultados.md) y E17 del [índice](05-evidencias.md).

## Herramientas y versiones identificadas

| Entorno | Identificación | Procedencia |
|---|---|---|
| Pi / SO | Raspbian GNU/Linux 13 Trixie, `armv7l` | Lectura SSH |
| Pi / compilador | GCC Raspbian 14.2.0-19+rpi1 | `cc --version` |
| Pi / vídeo | GStreamer 1.26.2 | `pkg-config --modversion gstreamer-1.0` |
| Pi / OpenCV | Paquetes core/imgproc/imgcodecs armhf 4.10.0+dfsg-5+rpi1 | `dpkg-query -W` |
| Pi / Python auxiliar | 3.13.5 | Lectura SSH; no ejecuta la inferencia de producción |
| NCNN fijado por construcción | 20250916, revisión `c4193aadbbb56582aa87b1850dd3d98fb8fd936d` | [tools/build-ncnn.py](../tools/build-ncnn.py) |
| Exportación ONNX | ONNX 1.23.2 y PNNX wheel 20261020 | [metadatos del modelo](../models/yolov5n-ncnn.json) |
| PC / primera ronda | Python 3.12.15; NCNN 1.0.20250916; OpenCV 5.0.0; ORT 1.31.0; OpenVINO 2026.4.1 | [informe histórico](../detector-study.md) y E10; no confundir con OpenCV de la Pi |

`pkg-config opencv4` no está disponible en la Pi inspeccionada. Los paquetes y cabeceras sí están instalados; el Makefile usa `/usr/include/opencv4` y bibliotecas explícitas. Es una particularidad de reproducción, no evidencia de ausencia de OpenCV.

SHA-256 observado de la biblioteca estática NCNN de la Pi:

```text
032f2a0ecbd258916c9abf815ead6be27079c5279885830450f42ccf3311e038
```

## Modelo, conversión y licencias declaradas

El ONNX fuente de YOLOv5n corresponde a v7.0 y su SHA-256 exigido por el exportador es:

```text
04f0e55c26f58d17145b36045780fe1250d5bd2187543e11568e5141d05b3262
```

El [exportador](../tools/export-yolov5-ncnn.py) comprueba esa huella, promueve constantes FP16 a FP32 preservando sus valores, extrae tres cabezas y sustituye SiLU por `x * sigmoid(x)` usando capas disponibles. No se realizó entrenamiento ni cuantización adicional. La paridad de seis imágenes está en E02. La entrada rectangular se selecciona en el adaptador/ejecución, no cambiando los pesos; puede modificar predicciones en bordes y requiere su propia validación.

Según [models/NOTICE](../models/NOTICE): NCNN BSD-3-Clause, NanoDet Apache-2.0, YOLOv5 GPL-3.0 y YOLO11/YOLO26 AGPL-3.0; PicoDet se registra como Apache-2.0. Se conservan textos locales de [NCNN](../models/LICENSE-NCNN), [NanoDet](../models/LICENSE-NanoDet), [YOLOv5](../models/LICENSE-YOLOv5) y [YOLO11](../models/LICENSE-YOLO11). Estas son identificaciones del inventario, no una revisión legal de distribución. Motor, pesos, exportaciones y vídeos tienen procedencias distintas.

## Comprobación local ejecutada al preparar el dossier

Se ejecutó `make test` en el PC el 24/10/2026. Salida observada:

```text
./build/test-rtp
RTP tests passed
./build/test-panini
Panini geometry tests passed
./build/test-vehicles
Vehicle protocol, expiration and tracking tests passed
```

Esta ejecución no comprueba la pantalla, el decodificador hardware ni el reconocimiento de la maqueta. Los demás ensayos se referencian como anteriores, con el nivel de evidencia indicado en [02-ensayos-y-resultados.md](02-ensayos-y-resultados.md).

## Reproducción por etapas

Ejecutar desde la raíz del proyecto, en un entorno preparado con las dependencias del [readme](../readme.md). Las pruebas con vídeo/detector real necesitan la Pi, sus modelos y las entradas; no se deben mezclar compilaciones o benchmarks con una ventana usada para medir rendimiento en directo.

```bash
# Construcción del receptor y pruebas sin pantalla.
make
make test

# En la Pi con GStreamer de desarrollo: filtros y overlay sintéticos.
make test-video

# Solo si se dispone de la captura de referencia original.
make test-capture CAPTURE=/ruta/camera.pcap

# Motor y trabajador nativos: requiere fuentes NCNN y modelos disponibles.
python3 tools/build-ncnn.py
make build/vehicle-detector build/benchmark-yolov5

# Comparación nativa con idéntica imagen; 0=cuadrado, 1=rectangular.
./build/benchmark-yolov5 models/yolov5n /ruta/referencia.png 320 2 5 0
./build/benchmark-yolov5 models/yolov5n /ruta/referencia.png 320 2 5 1

# Benchmark determinista de Panini; contrastar dos revisiones con cmp/sha256sum.
make build/benchmark-panini
./build/benchmark-panini /tmp/panini.i420

# Estado y muestra de recursos, con ambos servicios ya ejecutándose.
systemctl is-active rear-camera rear-camera-detector
python3 tools/measure-detector.py --seconds 30 --output /tmp/recursos.json
python3 tools/check-pi.py --seconds 300
```

La exportación requiere Python con ONNX, NumPy y el ejecutable PNNX en la ubicación que utiliza el script (`build/model-tools-python/pnnx/pnnx`). No basta con copiar los pesos a un directorio vacío. La procedencia, versión del exportador y sus hashes deben conservarse junto con el nuevo ensayo.

Para IPC, la entrada debe ser una imagen positiva I420 de **416×250 = 156.000 bytes**, sin la cabecera IPC. Los flags de rango/color deben corresponder a su codificación. Ejemplo para BT.601 de rango limitado:

```bash
CAMERA_DETECTOR_SIZE=320 CAMERA_DETECTOR_THREADS=2 \
CAMERA_DETECTOR_RECT=1 CAMERA_DETECTOR_THRESHOLD=0.15 \
CAMERA_DETECTOR_START_THRESHOLD=0.25 OMP_WAIT_POLICY=PASSIVE MALLOC_ARENA_MAX=2 \
python3 tests/test_detector_ipc.py /ruta/positiva-416x250.i420 --model yolov5n --flags 0
```

Para campañas PC, conservar los manifiestos de fuentes y parámetros y usar [study-detectors.py](../tools/study-detectors.py), [compare-all-frames.py](../tools/compare-all-frames.py) y las herramientas de [tools](../tools/) correspondientes al ensayo. Reutilizar detecciones guardadas para dibujar vídeos no es una nueva ejecución del modelo. Repetir las campañas completas exige originales, modelos, espacio y tiempo; no se ejecutó esa repetición para redactar el dossier.

## Conservación de evidencia

Los directorios `build/`, `comparisons/`, los pesos `.bin/.param/.onnx` y los datos de acceso están excluidos por el `.gitignore` actual. Por tanto, **subir solo Markdown y código a Git no entrega las evidencias enlazadas**. La captura `captures/camera.pcap` no existe en esta copia. Los vídeos fuente están fuera de la raíz del proyecto.

Para el siguiente cierre de ensayo, conservar: revisión de código, configuración efectiva, hashes de entradas/modelos/binarios, versiones, comando exacto, logs originales, medidas y escena/condiciones; separar fallos y éxitos. No incluir credenciales en el paquete de auditoría. Las observaciones de Wi-Fi/reinicio previas se recogen en E13, pero no hay un informe específico de recuperación de red completo en este dossier.

## Huellas de la copia inspeccionada

Los binarios siguientes son los leídos en la Pi, no ejecutables del PC:

| Binario desplegado | SHA-256 |
|---|---|
| `build/rear-camera` | `5be8c7cda123f620e9b4fd15c5e80cb3a3dd0f64bec147bdafabf6ce4f57983c` |
| `build/vehicle-detector` | `ef0303ae29390d731d5499756eadc4489ea866d707904b0ae138a9b6ab0751a9` |

Las huellas de los fuentes, servicios y modelos indicados como «Pi y PC» se compararon entre ambas máquinas y coincidían. La del protocolo se registra como fuente local de esta copia.

| Archivo | Verificación | SHA-256 |
|---|---|---|
| [src/rear_camera.c](../src/rear_camera.c) | Pi y PC | `ca25750c8cd5bc6120d878630d2abae182519e13b70f9844ebe8b758e84b83e6` |
| [src/camera_rtp.c](../src/camera_rtp.c) | Pi y PC | `da6ad458c19766ccb635ec16874446593e8ab89d08a192819f6406e8a814578c` |
| [src/panini.c](../src/panini.c) | Pi y PC | `d9cda5da1114c5b77ed00910e2ceb330375082d6ea0f7b11afb9c7f859eb7867` |
| [src/gst_panini.c](../src/gst_panini.c) | Pi y PC | `a5d2c01315483d1201564be26a8d5d3ca5a6a9ba35b21e84c818eecc2dbfc68f` |
| [src/gst_vehicles.c](../src/gst_vehicles.c) | Pi y PC | `eff76c651c5fdfff239edebf0f4db275eb8049b0212128d700b615d1f8dd4458` |
| [src/vehicle_detector.cpp](../src/vehicle_detector.cpp) | Pi y PC | `1a5c1008e402c2ed14eacf91bc9f6e23ee94caabe9a5c76e006a5905408f0f94` |
| [src/yolov5.hpp](../src/yolov5.hpp) | Pi y PC | `621873f8e9cb265fad902df5eb84659bc4f4c0cf24137acaabae7ddbbc4ce9f4` |
| [src/vehicle_protocol.h](../src/vehicle_protocol.h) | PC | `261a5d20bc3e542dae247378cc8f3f7827c6a62b4fcf503cad5bb079225422e9` |
| [src/vehicle_tracking.c](../src/vehicle_tracking.c) | Pi y PC | `e374c438ca7c68481fce2da8f50eb0ec64b8e6bc2f28c60e58f8db100bd15c4f` |
| [systemd/rear-camera.service](../systemd/rear-camera.service) | Pi y PC | `b0a6f8aca978b8d644a356dcf645a6ba342b8993db9e24490d30e24c8167e436` |
| [systemd/rear-camera-detector.service](../systemd/rear-camera-detector.service) | Pi y PC | `396810c1baba8cfb8032bdae9995c6b6b04e690857b1a67d5e94b01283ea0ea6` |
| [models/yolov5n.param](../models/yolov5n.param) | Pi y PC | `117c4ed8151217b67612aa3ae9948321dbaca977bb603266a0014d39f6471c28` |
| [models/yolov5n.bin](../models/yolov5n.bin) | Pi y PC | `0f5dc13935fce2c513f2d38b8227f5181b560c33214e19b18a6ce4443fae9804` |

### Huellas de evidencias principales

Estas huellas identifican los archivos consultados para esta edición; no añaden validación independiente a su contenido.

| Evidencia | SHA-256 |
|---|---|
| [yolov5n_raspberry-pi-3Aplus/optimized/summary.json](../comparisons/yolov5n_raspberry-pi-3Aplus/optimized/summary.json) | `ed552e11564ac9d5a4233868d481a536ed02dd3b0a295e8cd9a9e6c4694a0e1a` |
| [yolov5n_raspberry-pi-3Aplus/optimized/live-resources-final.json](../comparisons/yolov5n_raspberry-pi-3Aplus/optimized/live-resources-final.json) | `7bc5253a62c91848ba19c5db52ba324ead3f6c128fa4f586a1f47d4421750be7` |
| [yolov5n_raspberry-pi-3Aplus/optimized/reference-comparison.json](../comparisons/yolov5n_raspberry-pi-3Aplus/optimized/reference-comparison.json) | `69cf3e20201ba1d6cd7d0b9d4c559cced568aaa036a9fdad8ead009a86db415f` |
| [yolov5n_raspberry-pi-3Aplus/optimized/recovery.json](../comparisons/yolov5n_raspberry-pi-3Aplus/optimized/recovery.json) | `ed573627f59c1280d62862b0db42be52c9154e89fc9da71dc85397e15f6ec0b1` |
| [new-models_all-frames_6-videos/combined-comparison.csv](../comparisons/new-models_all-frames_6-videos/combined-comparison.csv) | `441f4efd153554d2194d0a5e401649b61eac58ed33d57f8612bc831404052ae9` |
| [new-models_all-frames_6-videos/marker-audit/summary.json](../comparisons/new-models_all-frames_6-videos/marker-audit/summary.json) | `168c285ea4b64d98ad0bea85ef335ec03cc0a42f4dcda3f281f5c94485b1fbed` |
| [new-models_all-frames_6-videos/manifest.json](../comparisons/new-models_all-frames_6-videos/manifest.json) | `87439188377a2afb72bf401595b3cd81c6e26186f222e4711e47db7fb79026b9` |

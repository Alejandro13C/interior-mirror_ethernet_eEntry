# Estudio de detección — primera ronda, 21/10/2026

Objetivo: identificar vehículos que siguen al coche y mostrar **un triángulo encima**, sin rectángulos. La caja interna puede excluir ruedas. Separamos modelo (qué reconoce), motor (cómo lo ejecuta) y seguimiento (continuidad del marcador).

## Qué se ha medido

- PC Intel i7-8700, 12 CPU lógicas, 31 GB RAM; CPU, sin acelerador GPU.
- Seis vídeos de Downloads: Winchester, Unity, Hornet, Dwarf, Texana y X5M. Cinco instantes por vídeo, elegidos al 5/20/40/60/80 % antes de ejecutar los modelos: **30 imágenes**.
- **20 configuraciones:** NanoDet-Plus 256/416 con NCNN; YOLOv5n, YOLOv5s y YOLO11n a 640 con OpenCV, ONNX Runtime y OpenVINO, a 1/4 hilos.
- Cada configuración en proceso separado; 5 calentamientos y 3 repeticiones por imagen. Tiempo de preprocesado + inferencia + filtrado/NMS; sin lectura de vídeo ni codificación en el tramo medido. Ejecución secuencial entre configuraciones.
- YOLO: mismos archivos ONNX, entrada, umbral 0,15 y NMS entre motores. v5 usa entrada FP16 según el archivo; OpenVINO solicita cálculo FP32. No es una comparación de kernels con idéntica precisión interna.
- NanoDet recibe la miniatura I420 de 416×250 del sistema actual; YOLO recibe el lienzo 800×480. **Los resultados entre modelos incluyen esa diferencia de resolución**, mientras que la comparación de motores mantiene el mismo modelo y entrada.
- CPU expresada como porcentaje de las 12 CPU lógicas. RAM: máximo RSS del proceso completo, incluidos Python, bibliotecas y muestras precargadas; no memoria exclusiva del modelo ni RAM de la Pi. NCNN usa espera OpenMP pasiva para evitar consumo en espera.

## Rendimiento observado

Media/p95 en milisegundos. FPS = inversa del tiempo medio de análisis; **no son FPS de cámara a pantalla**.

| Modelo | Motor | Hilos | Media / p95 ms | FPS análisis | CPU total | RSS MiB |
|---|---|---:|---:|---:|---:|---:|
| NanoDet-Plus 256 | NCNN | 1 | 9,3 / 10,0 | 107,2 | 8,4 % | 120 |
| NanoDet-Plus 416 | NCNN | 1 | 22,0 / 27,9 | 45,5 | 8,4 % | 120 |
| YOLOv5n 640 | OpenCV | 4 | 35,4 / 41,4 | 28,2 | 28,0 % | 189 |
| YOLOv5n 640 | ONNX Runtime | 4 | 44,3 / 53,1 | 22,6 | 20,5 % | 207 |
| YOLOv5n 640 | OpenVINO | 4 | 31,9 / 34,5 | 31,3 | 24,3 % | 230 |
| YOLOv5s 640 | OpenVINO | 4 | 68,8 / 76,3 | 14,5 | 28,9 % | 280 |
| YOLO11n 640 | OpenCV | 4 | 37,2 / 37,8 | 26,9 | 30,4 % | 184 |
| YOLO11n 640 | ONNX Runtime | 4 | 37,7 / 46,8 | 26,5 | 24,7 % | 239 |
| YOLO11n 640 | OpenVINO | 4 | 25,9 / 30,2 | 38,6 | 32,7 % | 244 |

Versiones: NCNN 1.0.20250916, OpenCV 5.0.0, ORT 1.31.0, OpenVINO 2026.4.1, Python 3.12.15. Resultados completos, versiones y hashes: `comparisons/engine-study_6-real-videos/` (`summary.json`, `table.md`, `samples.json`). Es una ronda corta, sin controlar frecuencia/temperatura del PC; diferencias pequeñas requieren repetir en orden alterno.

Con el mismo YOLO11n, las 83 detecciones de OpenCV se emparejan con las de ORT/OpenVINO a IoU ≥0,9 y misma clase. YOLOv5n tiene una detección adicional en OpenCV cerca del umbral. Ver `engine-agreement.json`: cambiar de motor conserva casi toda la detección, **no resuelve por sí solo los coches que el modelo no reconoce**.

## Calidad y vídeo

Se han anotado aproximadamente 8 vehículos en 6 imágenes nuevas de Hornet/Dwarf, sin ajustar umbrales con ellas. Criterio principal de identificación: centro horizontal del marcador dentro del vehículo y anclaje vertical a menos de media altura de su borde superior, con asociación uno a uno. Es una tolerancia exploratoria para el triángulo, no una exigencia de cubrir ruedas.

Coincidencias de marcador: NanoDet256 **5/8**, NanoDet416 **8/8**, YOLOv5n **7/8**, YOLOv5s **6/8**, YOLO11n **7/8**. Los tres motores YOLO coinciden en esos resultados. **Ocho vehículos no permiten elegir un ganador**: no son precisión global ni validación de carreras. Vehículos minúsculos/de fondo/aparcados no están anotados exhaustivamente; las predicciones sin pareja no se presentan como falsos positivos certificados. El ensayo anterior de Texana ya se usó para elegir parámetros y tampoco es validación independiente.

`triangles_6-clips_4-models.mp4`: cuatro paneles, NanoDet416/YOLOv5n/YOLOv5s/YOLO11n, cuatro segundos continuos de cada uno de los seis vídeos, empezando al 20 % de su duración. Solo triángulos blancos. Todos analizan cada fotograma de salida, umbral 0,15, alta de seguimiento 0,25, confirmación en dos observaciones y mismo asociador C. Se produce **offline a 30 fps de reproducción**, sin retraso artificial: su fluidez no demuestra funcionamiento en tiempo real. Configuración y marcadores por fotograma guardados junto al vídeo. No se aplica Panini a cámaras ajenas sin conocer su lente.

Pendiente para una decisión: ampliar anotaciones a secuencias continuas con identidad de cada coche; medir omisiones, falsos avisos, duplicados, desapariciones e intercambios de identidad, y estabilidad del triángulo respecto al movimiento del coche. No usar el salto del marcador en pantalla como única medida: el vehículo también se mueve.

## Bibliotecas consideradas

| Opción | Estado y utilidad |
|---|---|
| [NCNN](https://github.com/Tencent/ncnn) | Medido con NanoDet. Orientado a dispositivos móviles/ARM; mantener como candidato para la Pi. Falta convertir y comparar el mismo YOLO con NCNN. |
| [OpenCV DNN](https://docs.opencv.org/5.0/main_modules/dnn.html) | Medido, carga ONNX y sirve como referencia sencilla. Se ha probado el motor CPU de OpenCV 5. |
| [ONNX Runtime](https://onnxruntime.ai/docs/performance/tune-performance/threading.html) | Medido con CPUExecutionProvider e hilos explícitos; no resulta siempre más rápido. |
| [OpenVINO](https://docs.openvino.ai/2024/openvino-workflow/running-inference/inference-devices-and-modes/cpu-device.html) | Medido directamente con CPU y un stream. Mejor tiempo YOLO en este Intel; falta verificar compilación, operadores y recursos en el SO ARM de la Pi. |
| [LiteRT/TFLite](https://github.com/tensorflow/tensorflow/blob/master/tensorflow/lite/g3doc/guide/inference.md) | Alternativa de inferencia para Linux/Raspberry; no medida en esta ronda. Requiere exportación equivalente y validar conversión/cuántización. |
| [MNN](https://github.com/alibaba/MNN) | Alternativa móvil, no medida. Necesita conversión y comprobación de salidas con los mismos pesos. |
| [Ultralytics](https://docs.ultralytics.com/modes/export) / PyTorch | Herramientas de entrenamiento/exportación e inferencia; no equivalen a un motor ligero para la Pi. No se ha instalado la pila de entrenamiento para medir estos ONNX. |
| [TensorRT](https://docs.nvidia.com/deeplearning/tensorrt/latest/installing-tensorrt/prerequisites.html) | Requiere plataforma CUDA compatible; no es un motor para la Pi 3A+ por sí sola. No medido. |
| [ByteTrack](https://github.com/FoundationVision/ByteTrack) | Asociador de detecciones, no detector. Candidato para comparar continuidad usando exactamente las mismas detecciones; no probado todavía. El asociador actual no es ByteTrack. |

Licencias de modelos: NanoDet Apache-2.0, YOLOv5 v7 GPL-3.0, YOLO11 AGPL-3.0; referencias en `models/NOTICE`. La licencia del motor y la de los pesos se revisan por separado.

## Decisión y reproducción

**No desplegar otro detector todavía.** OpenVINO + YOLO11n merece seguir probándose como referencia rápida en este PC; NanoDet416 sigue siendo candidato ligero. Falta calidad temporal suficiente para elegir y una medición en la Pi para decidir viabilidad. Ningún FPS de este Intel se extrapola a la Raspberry.

Con un entorno Python 3.12 y los modelos de `tools/fetch-video-model.py`:

```bash
pip install -r tools/study-requirements.txt
make build/liboffline-tracking.so
python tools/study-detectors.py comparisons/new-study --videos-dir /ruta/videos
python tools/report-study.py comparisons/new-study
python tools/preview-study.py comparisons/new-study
```

Las etiquetas incluidas corresponden solo al manifiesto de esta ronda; para otros vídeos hay que anotar sus propias imágenes. Los originales se conservan.

## Ampliación: todos los fotogramas con NanoDet416

Se han procesado los **38.850 fotogramas de los seis vídeos**, sin saltos, y generado salidas completas con triángulos. La revisión confirma omisiones incluso de coches cercanos, duplicados y marcas ajenas a los vehículos que siguen: el 8/8 inicial no representa la fiabilidad en los vídeos completos. Véanse los [vídeos y resultados](full-video-results.md). Las detecciones por imagen no sustituyen una referencia anotada para medir precisión/recall.

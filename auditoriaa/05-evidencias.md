# Índice de evidencias

Fecha: 24/10/2026. Los enlaces apuntan a artefactos existentes en la copia revisada. **Un resumen JSON transcrito no tiene el mismo nivel de detalle que un log bruto; se señala su naturaleza cuando corresponde.** Este índice no sustituye la entrega de los archivos enlazados.

| ID | Evidencia | Qué permite comprobar / límite |
|---|---|---|
| E01 | [Código](../src/), [servicios](../systemd/), [arranque](../run-pi.sh), [consola](../display-console.sh), [readme](../readme.md) | Diseño implementado y configuración de ejemplo. El estado efectivo por SSH se transcribe en [04](04-trazabilidad-y-reproduccion.md) |
| E02 | [Perfiles Pi iniciales](../comparisons/yolov5n_raspberry-pi-3Aplus/profile-benchmarks.json), [paridad ONNX/NCNN](../comparisons/yolov5n_raspberry-pi-3Aplus/conversion-parity.json), [despliegue inicial](../comparisons/yolov5n_raspberry-pi-3Aplus/deployment-verification.json), [metadatos de exportación](../models/yolov5n-ncnn.json) | Medidas nativas de entrada cuadrada, errores numéricos y primer despliegue. No describe por sí solo el perfil rectangular final |
| E03 | [Sobrecarga y vídeo solo](../comparisons/yolov5n_raspberry-pi-3Aplus/live-car/video-recovery.json), [recursos de la etapa sobrecargada](../comparisons/yolov5n_raspberry-pi-3Aplus/live-car/resources-final.json) | Resumen transcrito de contadores de vídeo y muestras CPU/RAM de una etapa anterior; su estado «detector desactivado» es histórico |
| E04 | [Resumen optimizado](../comparisons/yolov5n_raspberry-pi-3Aplus/optimized/summary.json), [recursos con muestras](../comparisons/yolov5n_raspberry-pi-3Aplus/optimized/live-resources-final.json), [doce imágenes y predicciones](../comparisons/yolov5n_raspberry-pi-3Aplus/optimized/reference-comparison.json), [imágenes de entrada](../comparisons/yolov5n_raspberry-pi-3Aplus/optimized/samples/) | Resumen elaborado durante la sesión, datos de `/proc` y predicciones nativas. El resumen conserva tiempos IPC, paridad Panini y estadísticas de vídeo; no adjunta todos los stdout brutos |
| E05 | [Recuperación optimizada](../comparisons/yolov5n_raspberry-pi-3Aplus/optimized/recovery.json), [recuperación inicial](../comparisons/yolov5n_raspberry-pi-3Aplus/recovery.json) | Detector reiniciado tras fallo forzado y PID del vídeo sin cambiar. Los PIDs identifican cada ensayo, no requisitos permanentes |
| E06 | [Campaña completa de cinco configuraciones](../comparisons/old/all-models_all-frames_6-videos/), [verificación](../comparisons/old/all-models_all-frames_6-videos/verification.json), [manifiesto](../comparisons/old/all-models_all-frames_6-videos/manifest.json) | 194.250 resultados sobre 38.850 imágenes únicas; predicciones por modelo/clip y vídeos sincronizados |
| E07 | [Campaña de tres modelos adicionales](../comparisons/new-models_all-frames_6-videos/), [verificación](../comparisons/new-models_all-frames_6-videos/verification.json), [cierre de proceso](../comparisons/new-models_all-frames_6-videos/completed.json), [pipeline](../comparisons/new-models_all-frames_6-videos/pipeline.json) | 116.550 resultados, seis vídeos y 72 comparativas estáticas. Integridad de salidas, no corrección exhaustiva |
| E08 | [Tabla combinada CSV](../comparisons/new-models_all-frames_6-videos/combined-comparison.csv) | Ocho perfiles con categorías de calidad y recursos PC. La tabla de [02](02-ensayos-y-resultados.md) conserva su separación de categorías |
| E09 | [Resumen de revisión](../comparisons/new-models_all-frames_6-videos/marker-audit/summary.json), [decisiones visuales](../comparisons/new-models_all-frames_6-videos/marker-audit/visual-decisions.json), [marcadores clasificados](../comparisons/new-models_all-frames_6-videos/marker-audit/classified-markers.json), [verificación de categorías](../comparisons/new-models_all-frames_6-videos/marker-audit/verification.json) | 69 imágenes, 1.352 marcas, 555 antes no asociadas revisadas en 341 grupos; anotación exploratoria asistida, no independiente |
| E10 | [Estudio inicial de motores](../comparisons/old/engine-study_6-real-videos/summary.json), [muestras y fuentes](../comparisons/old/engine-study_6-real-videos/samples.json), [acuerdo entre motores](../comparisons/old/engine-study_6-real-videos/engine-agreement.json), [benchmark de motores nuevos](../comparisons/new-models_all-frames_6-videos/cpu-benchmarks/summary.json) | Contexto, parámetros, versiones y motores disponibles/no disponibles. No extrapolar a ARM ni a toda la calidad temporal |
| E11 | [Vídeos individuales YOLOv5n](../comparisons/yolov5n_full-videos_6-videos/), [verificación y hashes](../comparisons/yolov5n_full-videos_6-videos/verification.json) | Seis vídeos completos 800×480; reutilizan predicciones guardadas sin eliminación manual de FP, reproducción offline |
| E12 | [Fuentes y SHA-256](../comparisons/new-models_all-frames_6-videos/manifest.json), [manifiesto anterior](../comparisons/old/nanodet416_all-frames_6-videos/source-manifest.json), [obtención de modelos](../tools/fetch-video-model.py), [modelos nuevos](../tools/fetch-modern-models.py) | Identificación de originales externos y modelos. No contiene los originales de Downloads ni recupera el PCAP ausente |
| E13 | [Validación histórica 21/10](../validation.md), [primera ronda](../detector-study.md), [etiquetas Texana](../tests/fixtures/texana_boxes.json), [otras etiquetas de comprobación](../tests/fixtures/racing_holdout_boxes.json) | Antecedentes, pruebas declaradas y muestras pequeñas. Documentos con conclusiones/rutas anteriores; no son el estado operativo actual |
| E14 | [Modelo de proyección](../calibration/panini.md), [geometría](../src/panini.c), [filtro I420](../src/gst_panini.c), [benchmark determinista](../tools/benchmark-panini.c) | Fórmulas, supuestos y reproducción de comparación de imágenes. No contiene calibración física de la lente |
| E15 | [Pruebas](../tests/), [Makefile](../Makefile), [protocolo](../src/vehicle_protocol.h), [medidor de recursos](../tools/measure-detector.py), [diagnóstico de estabilidad](../tools/check-pi.py), [snapshot y huellas](04-trazabilidad-y-reproduccion.md) | Código verificable, comandos y resultados fechados; no todos los ensayos históricos tienen log bruto conservado |
| E16 | [Referencias de pantalla](../documentation_pantalla/referencias.md), [manual asociado](../documentation_pantalla/7inch-DSI-Display-manual.pdf), [procedencia/licencias](../models/NOTICE), [plan](../plan.md), [criterios del proyecto](../AGENTS.md) | Identificación todavía incompleta del hardware, inventario y objetivos pendientes |

## Revisión visual de errores

**E17 — Diagnóstico posterior del 24/10:** [predicciones por perfil](../comparisons/yolov5n_raspberry-pi-3Aplus/detection-diagnostic/predictions.json), [filtros y puntuación previa de la captura actual](../comparisons/yolov5n_raspberry-pi-3Aplus/detection-diagnostic/filter-diagnostics.json), [captura de ayer analizada hoy](../comparisons/yolov5n_raspberry-pi-3Aplus/detection-diagnostic/yesterday-rect.json), [control positivo nativo](../comparisons/yolov5n_raspberry-pi-3Aplus/detection-diagnostic/positive-inspector-final.json), [resumen del control IPC](../comparisons/yolov5n_raspberry-pi-3Aplus/detection-diagnostic/positive-control.json) y [pruebas exploratorias de imagen](../comparisons/yolov5n_raspberry-pi-3Aplus/detection-diagnostic/image-conditions-probe.json). Son ensayos aislados, no cambios de umbral en producción ni una solución de la incidencia. El resumen del control IPC está transcrito de la ejecución observada; no es un log bruto independiente.

La clasificación permite revisar categorías sin mezclarlas:

- [Aciertos](../comparisons/new-models_all-frames_6-videos/marker-audit/correct.csv).
- [Duplicados](../comparisons/new-models_all-frames_6-videos/marker-audit/duplicates.csv).
- [Falsas alarmas y subtipos](../comparisons/new-models_all-frames_6-videos/marker-audit/false-positives.csv).
- [Errores de posición](../comparisons/new-models_all-frames_6-videos/marker-audit/position-errors.csv).
- [Dudosos](../comparisons/new-models_all-frames_6-videos/marker-audit/uncertain.csv).
- [Mosaicos clasificados](../comparisons/new-models_all-frames_6-videos/marker-audit/classified-samples/) y [hojas de revisión](../comparisons/new-models_all-frames_6-videos/marker-audit/review-sheets/).

Una marca sobre el capó propio se registra como vehículo no objetivo; no debe presentarse como perseguidor correcto por el mero hecho de reconocer un coche.

## Vídeos completos de YOLOv5n para inspección

| Clip | Vídeo | Datos de renderizado |
|---|---|---|
| Winchester | [MP4](../comparisons/yolov5n_full-videos_6-videos/01_winchester.mp4) | [JSON](../comparisons/yolov5n_full-videos_6-videos/01_winchester.json) |
| Unity | [MP4](../comparisons/yolov5n_full-videos_6-videos/02_unity.mp4) | [JSON](../comparisons/yolov5n_full-videos_6-videos/02_unity.json) |
| Hornet | [MP4](../comparisons/yolov5n_full-videos_6-videos/03_hornet.mp4) | [JSON](../comparisons/yolov5n_full-videos_6-videos/03_hornet.json) |
| Dwarf | [MP4](../comparisons/yolov5n_full-videos_6-videos/04_dwarf.mp4) | [JSON](../comparisons/yolov5n_full-videos_6-videos/04_dwarf.json) |
| Texana | [MP4](../comparisons/yolov5n_full-videos_6-videos/05_texana.mp4) | [JSON](../comparisons/yolov5n_full-videos_6-videos/05_texana.json) |
| X5M | [MP4](../comparisons/yolov5n_full-videos_6-videos/06_x5m.mp4) | [JSON](../comparisons/yolov5n_full-videos_6-videos/06_x5m.json) |

## Imágenes de la prueba en Raspberry

- [Escena guardada con maqueta visible y detección pendiente](../comparisons/yolov5n_raspberry-pi-3Aplus/optimized/live-car-0.png): observación del campo real que recibe el detector; no captura del framebuffer final de la pantalla.
- [Previsualización anterior de posición del triángulo](../comparisons/yolov5n_raspberry-pi-3Aplus/live-car/live-car-triangle-preview.png) y [predicción usada](../comparisons/yolov5n_raspberry-pi-3Aplus/live-car/live-car-detection.json): reproducción diagnóstica de un fotograma capturado, **no prueba de triángulo continuo en pantalla**.

Las capturas, predicciones y vídeo renderizado no deben confundirse con una grabación sincronizada de cámara y pantalla para medir latencia física.

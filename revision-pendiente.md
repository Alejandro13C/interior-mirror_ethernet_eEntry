# Seis hallazgos pendientes de revisión

Revisión del commit `4c87b69`, realizada el 24/10/2026. P1 indica prioridad alta; P2, prioridad media. Este documento recoge los problemas y las correcciones propuestas; no implica que estén corregidos.

## 1. P1 — El despliegue puede conservar un detector incompatible con IPC v3

**Ubicación:** `tools/deploy-pi.sh:49–53` y `src/vehicle_protocol.h:8`.

El despliegue recompila el detector únicamente cuando se proporciona la variable opcional `NCNN`. Sin ella, puede conservar un ejecutable anterior al cambio del protocolo IPC de v2 a v3, que rechaza las imágenes nuevas. También pueden quedar sin compilar cambios posteriores del detector y del seguimiento.

**Corrección propuesta:** recompilar siempre vídeo y detector; condicionar a `NCNN` únicamente la sustitución de la biblioteca.

## 2. P2 — La validación del despliegue acepta vídeo a 0 fps

**Ubicación:** `tools/deploy-pi.sh:63–70`.

Se comprueba que los servicios estén activos y que aparezcan mensajes de estadísticas y de inicio del detector, pero no que avancen los fotogramas ni que lleguen respuestas recientes del detector. Las comprobaciones aceptaron una reproducción local con `fps=0.00 presented=0 rendered=0`.

**Corrección propuesta:** exigir avance sostenido de los fotogramas y respuestas recientes del detector durante una ventana de validación conjunta.

## 3. P2 — La vuelta atrás del despliegue es incompleta

**Ubicación:** `tools/deploy-pi.sh:27–41`, `:54` y `:57–58`.

La restauración recupera fuentes, ejecutables, unidades y el archivo de reglas udev, pero no la copia instalada de `/usr/local/lib/rear-camera/display-console.sh`. Tampoco recarga las reglas restauradas ni restablece el estado RPS anterior de la interfaz tras haber aplicado la configuración nueva.

**Corrección propuesta:** respaldar y restaurar el script instalado; recargar las reglas udev y restablecer la configuración de red anterior, incluido el estado RPS efectivo.

## 4. P2 — El diagnóstico offline utiliza el seguidor antiguo

**Ubicación:** `tools/diagnose-detection.py:378–397` y `src/vehicle_tracking.c`.

El diagnóstico sigue asociando detecciones por IoU y orden de entrada. El código en vivo incorpora predicción, límites de distancia y tamaño, y ordenación global de candidatos. En una secuencia local de tres detecciones, el seguidor C mantuvo el identificador 1 con tres aciertos; el diagnóstico creó el identificador 2 con un solo acierto.

**Corrección propuesta:** reutilizar el seguidor C mediante la biblioteca offline o actualizar el diagnóstico y verificar su equivalencia.

## 5. P2 — El diagnóstico offline omite la relación de aspecto del píxel

**Ubicación:** `tools/diagnose-detection.py:58–71`, `systemd/rear-camera.defaults` y `src/panini.c`.

La proyección del diagnóstico no incorpora `pixel_aspect`, mientras que la configuración desplegada utiliza `CAMERA_DISPLAY_PAR=16/15`. En una comparación local con entrada de 1344×968 y salida de 800×480, la coordenada vertical de origen en el centro del borde superior difirió aproximadamente 13,2 píxeles. Las imágenes y regiones evaluadas offline pueden diferir de las utilizadas en vivo.

**Corrección propuesta:** incorporar la relación de aspecto de píxel de la configuración efectiva y verificar la proyección frente a la implementación C.

## 6. P2 — La previsualización colorea clases desconocidas que deberían ser blancas

**Ubicación:** `tools/calibrate-markers.py:98–102` y `src/vehicle_marker.h`.

La previsualización convierte la categoría desconocida 0 en la categoría 7 para intentar mantener un estilo neutro. Ahora la categoría 7 corresponde a camiones y recibe color según distancia. Una reproducción local con el código C produjo amarillo en lugar de blanco.

**Corrección propuesta:** aplicar un estilo neutro explícito a las clases desconocidas, sin convertirlas en camiones.

# Hallazgos y acciones pendientes

Fecha de corte: 24/10/2026. «Alta/media» expresa prioridad interna de desarrollo, no una clasificación de seguridad funcional, ASIL ni dictamen de auditor externo. Los criterios siguientes son propuestas de cierre; faltan umbrales de aceptación acordados.

| ID | Prioridad / estado | Hallazgo y evidencia | Acción y evidencia exigida para cierre |
|---|---|---|---|
| H01 | Alta / abierto | Vídeo fluido, pero detección continua no demostrada; maqueta visible sin confirmaciones y usuario sin triángulo. E04 y comprobación SSH | Registrar predicciones antes y después de filtrado, asociación y dibujo; verificar con secuencias de coches reales anotadas e imagen final sincronizada. Distinguir fallo de reconocimiento de fallo de renderizado |
| H02 | Alta / abierto | El ranking PC-640 no valida el perfil Pi-320 rectangular. Las doce imágenes de regresión no cubren todo el uso. E02, E04, E08 | Evaluar el perfil realmente desplegado con secuencias separadas de ajuste y prueba: omisiones, FP, duplicados, continuidad, identidad y posición del triángulo |
| H03 | Alta / abierto | Lente sin calibración real; modelo equidistante y FOV 180° supuestos. E14 | Obtener parámetros de Ficosa o calibrar con tablero; documentar modelo, resolución, recorte y errores geométricos. No intercambiar coeficientes entre modelos |
| H04 | Alta / pendiente | No existe distancia validada ni triángulos de colores. E01 y código de dibujo | Tras cerrar la base de detección, validar estimación de distancia con referencias físicas, definir bandas y tratamiento de incertidumbre, y probar colores/umbrales. La confianza del detector no es distancia |
| H05 | Alta / abierto | Falta validación de instalación en coche: energía, apagado, almacenamiento, calor, vibración, lluvia/noche y cortes de señal. E01, E13 | Ensayos físicos documentados con condiciones, duración, criterio de aceptación y registros; medir latencia física de cámara a pantalla |
| H06 | Media / mejora parcial | Sobrecarga inicial: 14,96 fps y 79,29 % CPU; optimización: 23,02 fps y 55,03 %. Todavía no iguala vídeo solo a 24,75 fps. E03/E04 | Continuar optimizando recursos existentes; repetir perfiles en condiciones comparables y confirmar estabilidad prolongada y percepción. No resolverlo solo aumentando caducidad o bajando calidad sin declararlo |
| H07 | Media / abierto | No hay referencia exhaustiva para los 38.850 fotogramas. Las anotaciones son exploratorias y no independientes. E06–E10 | Crear evaluación temporal anotada, separar calibración/ajuste/prueba y conservar decisiones de revisión; definir tasas de FP por unidad de tiempo y omisiones por vehículo, no solo recuentos de marcas |
| H08 | Media / limitación conocida | Kernel observado con `cgroup_disable=memory`: `MemoryHigh`/`MemoryMax` configurados no están haciendo cumplir esos límites. E01 y lectura de arranque | Mantener claro que `LimitAS=256M` limita espacio virtual, no RSS. Verificar reacción ante agotamiento y memoria del conjunto; cualquier cambio de kernel exige regresión de arranque y vídeo |
| H09 | Media / abierto | Documentación histórica con rutas antiguas, enlaces rotos y estado anterior del detector. E13 | Mantenerla identificada como histórica; reparar índice de consulta y sustituir conclusiones operativas por este estado fechado, sin reescribir mediciones antiguas |
| H10 | Media / abierto | Falta el PCAP de referencia local, parte de las evidencias vive fuera del repositorio y no hay commit Git identificado en esta copia. E12, E15 | Conservar un paquete de evidencia con huellas, versiones y entradas accesibles; recuperar la captura antes de repetir `test-capture`; asignar una revisión controlada al siguiente ensayo |
| H11 | Media / abierto | Pantalla y cámara sin referencia exacta confirmada; carcasa sin encaje validado. E01, E16 | Identificar físicamente componentes, cotas y anclajes; verificar montaje, acceso a conectores y ventilación |
| H12 | Media / por definir | Faltan valores de aceptación aprobados: fps sostenidos, latencia física, omisiones/FP tolerables, alcance, duración y condiciones de prueba | Definir requisitos medibles y su procedimiento de verificación; no convertir las cifras observadas en requisitos aprobados a posteriori |

## Discrepancias documentales concretas

- [validation.md](../validation.md) describe principalmente NanoDet y configuración del 21/10: un núcleo y `LimitAS=192M`; el perfil actual es YOLOv5n, dos hilos, afinidad por proceso y `LimitAS=256M`.
- [detector-study.md](../detector-study.md) conserva una decisión inicial de no desplegar otro modelo y rutas anteriores al traslado a `comparisons/old/`. Es un antecedente de la primera ronda, no el estado vigente.
- `readme.md` y `detector-study.md` enlazan a `full-video-results.md`, que no existe en la copia revisada. Las evidencias completas disponibles se enlazan en [05-evidencias.md](05-evidencias.md).
- El campo `input` de [yolov5n-ncnn.json](../models/yolov5n-ncnn.json) dice «letterboxed square» y describe la exportación original; el adaptador desplegado permite entrada rectangular y la configuración observada la habilita. Preservar la procedencia original y añadir metadatos de ejecución evita confundir ambos perfiles.
- `deployment-verification.json` documenta el primer despliegue cuadrado. `live-car/video-recovery.json` documenta una desactivación temporal de IA. **Ninguno de los dos describe el estado final optimizado**, que se acredita por E04/E05 y la lectura SSH fechada.
- Los resúmenes de pruebas de optimización incluyen transcripciones de resultados; no todos disponen de stdout bruto y manifiesto completo de herramientas asociados. Esto reduce la fuerza de la trazabilidad y debe mejorarse en próximos ensayos.

## Orden de trabajo conservado

1. **Base actual:** preservar vídeo fluido mientras se diagnostica la falta de detección y se aprovecha el hardware existente.
2. **Percepción:** cerrar seguimiento y calidad sobre el perfil realmente desplegado; conservar resultados buenos y fallos sin eliminarlos manualmente.
3. **Calibración y distancia:** completar la geometría necesaria y contrastar distancia con referencias físicas.
4. **Triángulos de colores:** implementar después de estabilizar la base, según distancia validada e incertidumbre explícita. Colores y umbrales todavía por definir; no inferirlos a partir de la puntuación del modelo.
5. **Integración automotriz:** alimentación protegida, montaje y validación de condiciones reales, antes de atribuir prestaciones de un sistema de asistencia terminado.

No se ha seleccionado ni comprado radar. El presupuesto indicado de 300 € es una restricción futura, no una aprobación técnica de un sensor. La referencia Bosch del [plan](../plan.md) describe un objetivo funcional; este prototipo no reproduce ni acredita sus prestaciones.

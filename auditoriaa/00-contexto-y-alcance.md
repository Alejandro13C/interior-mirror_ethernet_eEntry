# Contexto, alcance y trabajo del asistente

**Destinatario:** otra IA o persona que deba revisar críticamente este proyecto sin acceso a la conversación. **Fecha de corte:** 24/10/2026. Este documento explica qué pidió el usuario, qué ha hecho el asistente y qué debe auditarse. El código, los resúmenes y este dossier han sido preparados con intervención del mismo asistente: su coherencia interna no constituye validación independiente.

## Qué estamos construyendo y para quién

El usuario quiere montar un retrovisor digital dentro de un coche de carreras. Una cámara Ficosa de ojo de pez, orientada hacia atrás, entrega vídeo comprimido por Ethernet. Una Raspberry Pi 3 Model A+ recibe ese vídeo mediante USB Ethernet y lo presenta en una pantalla DSI de 7 pulgadas, 800×480.

La finalidad inmediata es que el piloto vea lo que ocurre detrás con una imagen fluida y poco retraso. El sistema debe arrancar al recibir alimentación y funcionar durante el uso sin PC, Wi-Fi ni Internet. SSH y el PC se utilizan para desarrollo y ensayos, no como dependencia del funcionamiento final.

Sobre ese vídeo se quiere identificar a los vehículos perseguidores mediante **un triángulo encima**, sin rectángulos visibles. Después de estabilizar vídeo y detección se añadirán **triángulos de colores según la distancia**. El interés no es localizar cualquier coche: el capó del propio vehículo y coches aparcados o de servicio pueden ser avisos irrelevantes.

Bosch CAS-M3 EVO fue aportado como referencia visual y funcional. No es una especificación que ya se haya cumplido ni una equivalencia técnica: este prototipo no dispone de radar, distancia medida o prestaciones de colisión validadas.

## Peticiones y restricciones del usuario

| Petición | Implicación para el trabajo y su revisión |
|---|---|
| Usar la Pi 3A+ y pantalla existentes | Optimizar y medir antes de atribuir el problema a falta de hardware |
| Evitar gastar dinero innecesariamente | No recomendar compras sin investigar límites y optimizaciones posibles |
| Hacer las pruebas y desplegar por SSH | El asistente ha modificado código, configuración y servicios de la Pi; hay que contrastar fuente local y despliegue |
| Arranque automático y recuperación | Verificar algo más que la ejecución manual de un reproductor |
| Conservar la imagen completa | No dar un recorte o menor detalle por mejora equivalente sin explicarlo; el FOV Panini seleccionado también debe validarse |
| Mantener el vídeo fluido | La IA no debe bloquear ni degradar indebidamente el retrovisor |
| Comparar modelos antes de integrarlos | Separar calidad, falsos avisos, posición, CPU, RAM y latencia con vídeos reales |
| Usar los 38.850 fotogramas disponibles | Se procesaron todos; esto no significa que todos hayan sido anotados o revisados manualmente |
| Dibujar triángulos, no cajas | Las cajas internas sirven para NMS/seguimiento; no es obligatorio incluir las ruedas |
| Añadir colores por distancia después | No confundir la confianza del detector con distancia o riesgo |
| Documentación ordinaria breve | Se evitaban informes Markdown nuevos para las comparativas; la petición posterior de preparar esta auditoría autoriza expresamente este dossier |

El radar tiene un presupuesto futuro indicado de hasta 300 €, pero no se ha seleccionado ni instalado. El usuario llegó a ofrecer una Pi más potente, pero después fijó explícitamente que se debe aprovechar primero el hardware actual. Esa es la prioridad vigente, conservada en [AGENTS.md](../AGENTS.md).

## Alcance del desarrollo

**Incluido y trabajado:** identificación de la encapsulación de cámara; receptor C; vídeo hardware; arranque, consola y recuperación; corrección Panini aproximada; estudio de detectores y motores en PC; conversión de YOLOv5n; trabajador C++/NCNN; IPC, seguimiento y dibujo; optimización de la ejecución conjunta; medidas de CPU, RAM, fps y tiempo local; preparación de evidencias.

**Incluido como objetivo, pero pendiente:** reconocimiento continuo fiable; validación de la geometría de lente; evaluación del perfil realmente desplegado; distancia; colores y sus umbrales; integración eléctrica/mecánica y pruebas en coche.

**No realizado ni acreditado:** homologación, certificación, validación de seguridad funcional, referencia exhaustiva de todos los coches de los vídeos, precisión/recall globales, medición eléctrica de consumo, distancia real de radar o latencia física completa cámara-pantalla. No se ha entrenado un detector propio ni añadido cuantización al exportar YOLOv5n.

## Alcance de esta auditoría del asistente

La auditoría debe evaluar tanto el resultado técnico como **la calidad de las decisiones y afirmaciones del asistente**:

1. Fidelidad a las peticiones, prioridades y cambios de dirección del usuario.
2. Corrección de recepción, geometría, preprocesado, inferencia, seguimiento y presentación.
3. Representatividad de ensayos y justicia de comparaciones entre modelos, motores y hardware.
4. Capacidad de reproducir resultados y vincularlos a una revisión/configuración concreta.
5. Exactitud al comunicar cifras, resultados negativos, límites y trabajo pendiente.
6. Protección del funcionamiento de vídeo durante despliegues y pruebas, y alcance de la recuperación.

La revisión inicial puede realizarse con código y artefactos locales. Si faltan pesos, vídeos, logs o acceso a la Pi, debe declararse qué conclusión queda sin verificar. El dossier enlaza archivos que `.gitignore` excluye: **copiar solo `auditoria/` no entrega toda la evidencia**.

## Evolución del trabajo y decisiones que hay que revisar

| Etapa | Trabajo o decisión del asistente | Resultado y punto de auditoría |
|---|---|---|
| Recepción y pantalla | Diagnóstico del tráfico, adaptación a la encapsulación Ficosa, receptor y vídeo por hardware | Base de vídeo operativa. La compatibilidad observada es específica de esa cámara, no de cualquier RTP |
| Autonomía y presentación | Servicios systemd, consola `tty7`, fondo negro y recuperación | Confirmaciones de arranque/fondo negro y pruebas de recuperación; falta validación automotriz |
| Óptica | Comparativas visuales original/rectilínea/Panini con varios FOV; selección de 130° | Elección visual con modelo equidistante supuesto; no calibración ni distancia métrica |
| Primeros detectores | Pruebas de NanoDet y ejemplos de coches con marcadores | El usuario observó omisiones, falsos positivos y mala colocación. Las muestras iniciales pequeñas no bastaban para concluir calidad |
| Ampliación de evaluación | Estudio de motores/modelos, todos los fotogramas de seis vídeos y separación de categorías de error | Hay cobertura de procesamiento, pero anotación de calidad parcial. No convertirla en una tasa global de éxito |
| Elección provisional | YOLOv5n por equilibrio observado entre marcas útiles, falsas alarmas y recursos PC | Decisión exploratoria; revisar si la muestra y el motor justifican esa elección para la Pi |
| Primer despliegue YOLO | 640 resultó demasiado lento; se utilizó 320 cuadrado | La reducción respecto a PC-640 era una concesión de detalle que requiere evaluar calidad de nuevo |
| Intento de mejorar continuidad | Incremento de frecuencia de exportación y conservación acotada de marcas ante una omisión | El aumento de exportación elevó carga y empeoró vídeo; no fue una optimización conseguida y se retiró |
| Aislamiento del problema | Desactivar IA y medir vídeo solo | Recuperó aproximadamente 24,75 fps. Es un diagnóstico y una recuperación temporal, no cumplimiento de vídeo más detección |
| Conclusión prematura del asistente | Propuso probar una placa más potente como siguiente paso | El usuario lo corrigió: había que optimizar la Pi actual. La mejora posterior demuestra que esa conclusión era prematura |
| Reintegración optimizada | Entrada rectangular, una imagen pendiente, núcleos separados y Panini equivalente | Aproximadamente 23,02 fps y 55,03 % CPU, frente a 14,96 fps y 79,29 % en el tramo sobrecargado. Las condiciones no constituyen un A/B perfecto |
| Última observación | Usuario: vídeo fluido, ningún triángulo visible; contadores sin vehículos confirmados | **Problema de percepción/confirmación todavía abierto.** No declarar el sistema terminado por mejorar rendimiento o tener servicios activos |

La explicación y las evidencias de cada etapa están en [ensayos](02-ensayos-y-resultados.md) y [índice](05-evidencias.md). No todas las etapas conservan un historial completo de commits, comandos y logs; esta carencia forma parte de la auditoría.

## Qué hemos cambiado en la última optimización

| Cambio | Propósito | Qué se conserva / qué debe comprobarse |
|---|---|---|
| 320×320 → 320×192 rectangular | Evitar procesar relleno gris en la imagen panorámica | Mismos pesos y escala de contenido útil respecto al perfil 320; los bordes/contexto cambian y pueden cambiar predicciones. Sigue siendo distinto de PC-640 |
| Una imagen pendiente de análisis | Evitar colas y copias inútiles durante la inferencia | Vídeo no bloqueante; hay que comprobar confirmación de respuesta, reintento y recuperación |
| Afinidad 0–1 para vídeo, 2–3 para IA | Reducir competencia de planificación | Sigue existiendo caché/memoria compartida; no prueba aislamiento temporal estricto |
| Conservación ante una observación vacía | Reducir parpadeo de una marca ya confirmada | No se renueva el timestamp ni se supera TTL de 750 ms; no crea una detección donde no existe |
| Precalcular offsets y simplificar interpolación Panini | Reducir operaciones por píxel | Salida idéntica para la entrada determinista comprobada; mejora temporal pequeña y con ruido |

Fuentes prioritarios: [receptor](../src/rear_camera.c), [filtro Panini](../src/gst_panini.c), [exportación/dibujo](../src/gst_vehicles.c), [trabajador](../src/vehicle_detector.cpp), [YOLO](../src/yolov5.hpp), [seguimiento](../src/vehicle_tracking.c), [protocolo](../src/vehicle_protocol.h) y [servicios](../systemd/).

## Estado que una IA revisora debe tomar como punto de partida

La optimización de recursos ha producido una mejora medible, pero el objetivo funcional completo no está cumplido. La última prueba tiene vídeo fluido y ausencia de triángulo. El contador `vehicles` se incrementa tras confirmación/filtrado: **cero vehículos publicados no demuestra por sí solo cero propuestas crudas del modelo**, ni identifica la causa.

Luz, pose, maqueta frente a coches reales, resolución, preprocesado, umbrales, asociación y dibujo son hipótesis a contrastar, no causas ya demostradas. El próximo diagnóstico técnico debe seguir la misma imagen desde la propuesta del modelo hasta el píxel mostrado, manteniendo trazabilidad temporal y la fluidez del vídeo.

Después se abordarán distancia y colores. No corresponde «resolver» la ausencia de triángulos alargando indefinidamente su caducidad, ocultando falsos positivos en vídeos o comprando hardware antes de justificarlo.

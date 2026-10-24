# Plan del retrovisor

Objetivo: alimentación → arranque → vídeo trasero para carreras, sin depender de PC ni Wi-Fi durante el uso.

## Implementado y probado en la Pi

- Receptor C para la encapsulación Ethernet/RTP observada de la cámara.
- Decodificación H.264 y escalado por hardware; proporciones conservadas, espejo y giro de 180° configurables.
- Arranque con systemd, detección de ausencia de vídeo y reinicio automático. Recuperación tras caída forzada del proceso comprobada.
- Pruebas de reconstrucción con captura de referencia y diagnóstico de estabilidad, memoria, temperatura y alimentación. Medidas vigentes en el [plan de correcciones](auditoria/07-plan-correcciones.md).

## Siguientes pasos

Wi-Fi: autoconexión y reintentos ilimitados configurados; recuperación de radio y reinicio comprobados. Registros persistentes acotados para investigar futuras incidencias.

1. **Base desplegada:** limpieza de archivos antiguos completada en la Pi. Fondo negro en `tty7` confirmado por el usuario; tras reinicio, Wi-Fi y vídeo arrancan y se conserva la consola dedicada. Registros persistentes disponibles.
2. **Calibrar la lente:** [Panini](calibration/panini.md) activo a 130°, con modelo equidistante supuesto. Obtener del fabricante de la cámara la matriz K, distorsión, modelo de proyección y resolución/recorte correspondientes; alternativamente calibrar con un tablero. Validar geometría y campo visible con la cámara instalada.
3. **Detectar vehículos:** YOLOv5n reintegrado en la Pi 3A+ con entrada rectangular 320×192, dos hilos, una sola imagen pendiente y núcleos separados del vídeo. Pruebas de IPC y recuperación pasadas; validar continuidad y calidad en directo. Los 640 px del estudio en PC exceden el presupuesto de frescura en esta placa. Evaluar el perfil de 320 px con vídeos reales, ampliar etiquetas y medir falsos avisos/continuidad; agotar optimizaciones y medir sus efectos antes de proponer otro hardware. La comparación en PC no representa el rendimiento ni la calidad del perfil reducido.
4. **Triángulos de colores según distancia:** escala experimental de color/tamaño por ancho aparente desplegada en la Pi el 24/10/2026, con perfil provisional de seis vídeos en `calibration/marker-widths.defaults` y previsualizaciones generadas por `tools/calibrate-markers.py --render` (no incluidas en esta copia). Pruebas nativas y medición conjunta de vídeo/detección realizadas; quedan investigar imágenes dañadas/tardías, evaluar coches reales y calibrar con la cámara instalada antes de activar avisos operativos. Evidencia en `comparisons/marker-calibration/pi-deployment/`. Los vídeos no aportan distancias medidas: evaluar distancia y aproximación con confianza explícita antes de fijar umbrales físicos. No presentar metros estimados como mediciones. No hay radar; presupuesto máximo para sensor y conexión: **300 €**. Elegir hardware solo tras verificar interfaz documentada, seguimiento de varios vehículos, alcance, velocidad relativa, alimentación y coste total.
5. **Validar en el coche:** diseñar alimentación protegida y apagado/protección de la microSD. Medir latencia de cámara a pantalla, estabilidad prolongada y recuperación tras cortes de cámara, enlace y alimentación. Probar montaje, vibración, noche y lluvia; retirar imagen y detecciones antiguas al perder señal.

Referencia funcional: [Bosch CAS-M3 EVO](https://www.bosch-motorsport.com/products/electronics/collision-avoidance-systems/collision-avoidance-system-cas-m-3-evo/). Ese sistema combina cámara y radar; su funcionamiento sirve de referencia para los avisos, sin atribuir sus prestaciones a este prototipo.

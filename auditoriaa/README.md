# Dossier de auditoría — retrovisor con cámara

**Fecha:** 24/10/2026. **Estado:** prototipo en desarrollo. Este dossier reúne el diseño, los ensayos y sus evidencias; no constituye una certificación ni una validación independiente.

## Qué estamos haciendo y cuál es el scope

Estamos desarrollando un retrovisor digital autónomo para un coche de carreras, con una Raspberry Pi 3A+, cámara Ficosa por Ethernet y pantalla DSI. Primero debe ofrecer vídeo fluido y detección estable de vehículos perseguidores mediante triángulos; después incorporará colores según distancia validada. La fase actual consiste en optimizar y diagnosticar la ejecución conjunta sobre el hardware existente.

El scope de la auditoría incluye **el código, los ensayos, los despliegues y las decisiones/afirmaciones del asistente**. Debe contrastar si el trabajo cumple lo pedido, si las comparaciones son válidas y si las conclusiones exceden la evidencia. No se presentan como terminados el reconocimiento continuo, la distancia, los colores ni la validación en coche.

**Para otra IA:** empieza por [contexto y alcance completo](00-contexto-y-alcance.md) y utiliza la [guía de auditoría independiente](06-guia-auditoria-ia.md), que incluye un encargo listo para copiar, preguntas concretas y el formato de hallazgos esperado. No hace falta disponer de la conversación para entender el objetivo o las limitaciones.

## Documentos

0. [Contexto y alcance](00-contexto-y-alcance.md): encargo del usuario, restricciones, evolución, decisiones y errores que debe revisar otra IA.
1. [Sistema y requisitos](01-sistema-y-requisitos.md): hardware, arquitectura, algoritmos y configuración.
2. [Ensayos y resultados](02-ensayos-y-resultados.md): campañas en PC y Raspberry, métricas y limitaciones.
3. [Hallazgos y acciones pendientes](03-hallazgos-y-acciones.md): desviaciones, criterios de cierre y próximos pasos.
4. [Trazabilidad y reproducción](04-trazabilidad-y-reproduccion.md): versiones, huellas, comandos y alcance de las comprobaciones.
5. [Índice de evidencias](05-evidencias.md): acceso directo a datos, vídeos, imágenes y pruebas.
6. [Guía para otra IA](06-guia-auditoria-ia.md): procedimiento crítico de revisión y matriz de afirmaciones a contrastar.

## Situación que se presenta a auditoría

| Elemento | Estado documentado |
|---|---|
| Vídeo y arranque automático | Implementados; confirmación del usuario de vídeo fluido con la integración optimizada |
| Corrección óptica | Panini a 130° implementado con lente equidistante supuesta, todavía sin calibración física |
| YOLOv5n en Raspberry Pi 3A+ | Activo; entrada rectangular 320×192 y dos hilos |
| Rendimiento combinado | 23,02 fps en un tramo de 78,897 s; CPU total 55,03 % en una muestra distinta de 30 s |
| Reconocimiento continuo | **No resuelto:** el coche/maqueta visible no se confirma de forma estable; el usuario no ve triángulos en la última comprobación |
| Distancia y triángulos de colores | **Pendientes:** actualmente solo existen triángulos blancos, sin distancia medida ni niveles de riesgo |
| Validación en coche de carreras | Pendiente: alimentación, montaje, condiciones de pista y comportamiento prolongado |

La lectura SSH de **24/10/2026, 07:51 UTC** confirmó ambos servicios activos y habilitados. No se cambió su configuración al preparar esta documentación. La lectura de servicios no demuestra que se reconozcan vehículos ni que un marcador se vea en pantalla.

## Cómo interpretar las evidencias

- Los resultados de PC, Raspberry, vídeo completo y muestras anotadas se distinguen expresamente.
- Los 38.850 fotogramas de los vídeos se procesaron, pero **no existe anotación exhaustiva de todos sus vehículos**. No se dispone de una tasa global de éxito, precisión, recall o mAP validada.
- Las medidas de latencia local excluyen la codificación de la cámara y el refresco físico del panel.
- Los informes antiguos se conservan como antecedentes. Sus configuraciones y conclusiones no sustituyen el estado actual.
- Los enlaces son relativos al proyecto. Este dossier referencia los artefactos originales; no duplica vídeos, modelos ni credenciales. Para entregarlo fuera de esta carpeta deben acompañarse las evidencias correspondientes.

El criterio de desarrollo es aprovechar y optimizar el hardware disponible antes de proponer compras, preservando la calidad y haciendo explícitas las concesiones. Véase [AGENTS.md](../AGENTS.md).

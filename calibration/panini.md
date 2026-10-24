# Panini: estrategia y formulación

**Configuración actual en la Pi:** salida 800 × 480, campo horizontal 130°, compresión `d=1`, pitch 0°. Se eligió visualmente comparando el mismo fotograma con varios campos de visión. La lente de entrada se supone equidistante, centrada y de 180°; **todavía no está calibrada**.

## Estrategia

Para cada píxel de salida calculamos qué punto de la imagen original debe muestrearse:

`salida → inversa de Panini → rayo 3D → orientación → lente fisheye → imagen original`

El mapa se precalcula al iniciar o cambiar la resolución. El filtro C trabaja en I420, copia la imagen decodificada secuencialmente a RAM y aplica interpolación bilineal. Las zonas sin muestra válida quedan negras. Las colas conservan el último fotograma y se descartan imágenes en directo con más de 150 ms desde su primer paquete recibido al llegar a la pantalla.

## Fórmulas utilizadas

Todos los ángulos de las ecuaciones están en **radianes**. `λ` es longitud, `φ` latitud y `d` compresión. Panini básico:

```text
S = (d + 1) / (d + cos λ)
h = S · sin λ
v = S · tan φ
```

`d=0` corresponde a la rectilínea; usamos `d=1`. Para una salida de tamaño `W × H`, campo horizontal `α` y píxel `(x,y)`:

```text
B = (d + 1) · sin(α/2) / (d + cos(α/2))
a = W / (2B)
h = (x − (W−1)/2) / a
v = ((H−1)/2 − y) / a

λ = atan2(h, d+1) + asin(h·d / sqrt((d+1)² + h²))
S = (d + 1) / (d + cos λ)
φ = atan(v / S)
```

Esta inversa usa la rama frontal del dominio soportado. El rayo y su rotación de pitch `p` son:

```text
rx = cos φ · sin λ
ry = sin φ
rz = cos φ · cos λ

rx′ = rx
ry′ = cos p · ry + sin p · rz
rz′ = −sin p · ry + cos p · rz
```

Para la entrada de tamaño `Ws × Hs` y FOV horizontal supuesto `β`, el modelo equidistante es `radio = f·θ`:

```text
f = Ws / β
ρ = sqrt(rx′² + ry′²)
θ = atan2(ρ, rz′)
sx = (Ws−1)/2 + f·θ·rx′/ρ
sy = (Hs−1)/2 − f·θ·ry′/ρ
```

En el eje óptico se usa el límite continuo para evitar dividir por cero. `(sx,sy)` se interpola en la imagen original.

## Referencias y límites

Modelo básico de [Paul Bourke](https://paulbourke.net/dome/fish2pannini/) y [General Panini](https://wiki.panotools.org/The_General_Panini_Projection). La inversa se ha contrastado numéricamente con la derivación cuadrática de Bourke.

Código: [geometría](../src/panini.c), [filtro I420](../src/gst_panini.c). Pruebas: `make test` y `make test-video`. Ajustes de arranque: [rear-camera.defaults](../systemd/rear-camera.defaults).

Pendiente: calibrar centro, escala y distorsión reales, o conseguir el modelo del fabricante de la cámara. No se han implementado polinomios de lente, roll/pan ni la corrección vertical adicional `b` de los ejemplos. Panini puede curvar horizontales fuera del centro y recortar el encuadre según el FOV seleccionado.

## Calibración de la lente (F10.1)

**Medido sin tablero** (`tools/calibrate-lens.py circle`, borde de viñeteo por máximo gradiente de log-luminancia + RANSAC): centro (659,3–661,2; 478,5–478,8), R = 733,2–737,5 px, RMS 1,0–1,6 px en las tres capturas antiguas `original-native.png` (en el respaldo `rear_view-respaldo-2026-10-24.zip`). La captura de banco del 24/10 (cables y PC en las esquinas) no fija el centro; con el centro fijo da R = 733,4–733,8 px, igual que antes. El centro está desplazado unos (−10; −5) px (−1,4°; −0,6°). El borde puede ser mecánico: no fija ni la focal ni el modelo. El lienzo actual (130°, pitch 0) solo muestrea sx 187–1156 y sy 205–762 (θ ≤ 68°, elevación ≥ −37° en el centro y ≥ −28,5° en los lados), así que el viñeteo no se ve. La carrocería propia solo entraría si sube por encima de esos ángulos, como una franja en la parte inferior del lienzo (6.6). Hay que comprobarlo con la cámara montada.

**Qué pedir al fabricante de la cámara:** modelo de proyección y sus parámetros (K = fx, fy, cx, cy; D de Kannala-Brandt k1–k4 o la tabla r(θ)), la resolución a la que se refieren, el recorte o escalado del ISP hasta 1344×968, si el ISP aplica espejo o flip, y la altura, el cabeceo y la posición de montaje.

**Procedimiento si hay que calibrar:**
1. Tablero: `calibrate-lens.py board --out board.png` genera un ChArUco 11×8 (cuadro 50 mm, marcador 37 mm, DICT_5X5_100) de 570×420 mm. Cabe en A2. Hay que imprimirlo al 100 %, pegarlo sobre un soporte rígido y medir el cuadro con calibre.
2. Captura en la Pi, unos 200 MB/min (3,3 MB/s medidos): `sudo timeout 90 tcpdump -i eth0 -s 0 -w board.pcap ether src 44:3c:88:24:70:22`. Después, en el PC: `python tools/extract-h264.py board.pcap board.h264 --png frames --every 12`. El flujo es todo IDR, así que perder un fotograma no corrompe los demás.
3. Vistas: al menos 20 (mejor 30–40), con el tablero quieto a 0,4–1 m. Deben cubrir el centro, los cuatro bordes y las esquinas hasta el círculo, con inclinaciones de hasta ±40°. Hay que descartar las movidas.
4. `calibrate-lens.py calibrate frames --square-mm <medido> --marker-mm <medido> --circle 661.1 478.8 733 --out calibration/lens.json` genera un JSON con K, D, el RMS y el RMS por vista. Incluye también el hold-out (1 de cada 5 vistas fuera), la cobertura, la comparación con f = 1344/π = 427,8 px/rad (focal, CAMERA_LENS_FOV equivalente y desplazamiento del centro) y los ajustes EUCM y Mei (`cv2.omnidir`, solo con opencv-contrib).
5. Criterios de aceptación: RMS ≤ 0,5 px, también en el hold-out; al menos 20 vistas válidas, y al menos el 60 % de las celdas del círculo cubiertas. KB y EUCM deben coincidir en r(θ). Para validar, se compara con los datos del fabricante de la cámara y se repite la calibración con otra tanda de vistas; la diferencia en r(θ) hasta 68° debe ser ≤ 1 px. Los datos solo se pasan a `panini.c` después de medir.

**Herramienta comprobada en sintético** (`synth` + `calibrate --truth`, ChArUco renderizado con KB conocido, ruido 2 niveles): error de K ≤ 0,16 px y de r(θ) ≤ 0,06 px, con RMS de 0,12 px. `selftest` (puntos, 25 vistas, ruido 0,3 px) da una dispersión de ±0,45 px en f y de ±0,55 px en el centro. El `CharucoDetector` de OpenCV 4.13 devuelve las esquinas desplazadas +0,5 px; la herramienta las refina con `cornerSubPix`.

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

Pendiente: calibrar centro, escala y distorsión reales, o conseguir el modelo de Ficosa. No se han implementado polinomios de lente, roll/pan ni la corrección vertical adicional `b` de los ejemplos. Panini puede curvar horizontales fuera del centro y recortar el encuadre según el FOV seleccionado.

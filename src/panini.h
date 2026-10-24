#ifndef CAMERA_PANINI_H
#define CAMERA_PANINI_H
#include <stdbool.h>

typedef struct {
    double compression, lens_fov, view_fov, pitch;
} PaniniConfig;
/* Angles in degrees. lens_fov is the assumed horizontal equidistant lens FOV.
   This initial lens model is approximate, not a replacement for calibration. */
bool panini_valid(const PaniniConfig *c);
bool panini_source(const PaniniConfig *c, int sw, int sh, int dw, int dh,
                   double x, double y, double *sx, double *sy);
#endif

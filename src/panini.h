#ifndef CAMERA_PANINI_H
#define CAMERA_PANINI_H
#include <stdbool.h>

typedef struct {
    double compression, lens_fov, view_fov, pitch;
    double pixel_aspect; /* display pixel width/height; 0 means square */
} PaniniConfig;
/* Angles in degrees. lens_fov is the assumed horizontal equidistant lens FOV.
   This initial lens model is approximate, not a replacement for calibration.
   pixel_aspect keeps proportions on a panel with non-square pixels: the
   output covers the full width and correspondingly less vertical angle. */
bool panini_valid(const PaniniConfig *c);
bool panini_source(const PaniniConfig *c, int sw, int sh, int dw, int dh,
                   double x, double y, double *sx, double *sy);
#endif

#include "panini.h"
#include <math.h>
#define PI 3.14159265358979323846
#define RAD (PI / 180.0)

bool panini_valid(const PaniniConfig *c) {
    return c && isfinite(c->compression) && c->compression >= 0 && c->compression <= 2 &&
        isfinite(c->lens_fov) && c->lens_fov >= 90 && c->lens_fov <= 220 &&
        isfinite(c->view_fov) && c->view_fov >= 40 && c->view_fov <= 160 &&
        isfinite(c->pitch) && c->pitch >= -45 && c->pitch <= 45;
}

bool panini_source(const PaniniConfig *c, int sw, int sh, int dw, int dh,
                   double x, double y, double *sx, double *sy) {
    if (!panini_valid(c) || sw < 2 || sh < 2 || dw < 2 || dh < 2 ||
        !isfinite(x) || !isfinite(y) || !sx || !sy) return false;
    double d = c->compression;
    double half = c->view_fov * RAD / 2;
    double xmax = (d + 1) * sin(half) / (d + cos(half));
    double scale = dw / (2 * xmax);
    double u = (x - (dw - 1) / 2.0) / scale;
    /* u = (d+1) sin(lon)/(d+cos(lon)); invert on the monotonic front view.
       Rewriting as A sin(lon)-u cos(lon)=u*d avoids iterative inversion. */
    double a = d + 1;
    double q = u * d / hypot(a, u);
    if (fabs(q) > 1) return false;
    double lon = atan2(u, a) + asin(q);
    double s = (d + 1) / (d + cos(lon));
    double lat = atan(((dh - 1) / 2.0 - y) / (scale * s));
    double rx = cos(lat) * sin(lon);
    double ry = sin(lat), rz = cos(lat) * cos(lon);
    double p = c->pitch * RAD;
    double py = cos(p) * ry + sin(p) * rz;
    double pz = -sin(p) * ry + cos(p) * rz;
    double radial = hypot(rx, py);
    double theta = atan2(radial, pz);
    double f = sw / (c->lens_fov * RAD);
    double factor = radial > 1e-12 ? f * theta / radial : f;
    *sx = (sw - 1) / 2.0 + factor * rx;
    *sy = (sh - 1) / 2.0 - factor * py;
    return isfinite(*sx) && isfinite(*sy) && *sx >= 0 && *sx <= sw - 1 &&
        *sy >= 0 && *sy <= sh - 1;
}

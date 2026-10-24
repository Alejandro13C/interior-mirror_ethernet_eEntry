#include "panini.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#define PI 3.14159265358979323846
/* Independent inverse from Bourke's saved derivation: quadratic in cos(lon).
   Compare to our atan2/asin inverse on the supported forward-facing branch. */
static void check_bourke_inverse(void) {
    const double ds[] = {0, .25, .5, 1, 2};
    for (unsigned j = 0; j < sizeof ds / sizeof ds[0]; ++j) {
        PaniniConfig c = {ds[j],180,140,0};
        double half = c.view_fov*PI/360;
        double extent = (c.compression+1)*sin(half)/(c.compression+cos(half));
        double scale = 800/(2*extent);
        for (int px = 0; px < 800; px += 17) {
            double h = (px-399.5)/scale;
            double k = h*h/((c.compression+1)*(c.compression+1));
            double d = c.compression;
            double cosine = (-k*d + sqrt(k*k*d*d-(k+1)*(k*d*d-1)))/(k+1);
            double lon = copysign(acos(fmax(-1,fmin(1,cosine))),h);
            for (int py = 120; py <= 360; py += 120) {
                double vertical = (239.5-py)/scale;
                double lat = atan(vertical*(d+cos(lon))/(d+1));
                double rx = cos(lat)*sin(lon), ry = sin(lat), rz = cos(lat)*cos(lon);
                double radial = hypot(rx,ry);
                double factor = (1344/PI)*atan2(radial,rz)/radial;
                double expected_x = 671.5+factor*rx, expected_y = 483.5-factor*ry;
                double sx,sy;
                bool valid = panini_source(&c,1344,968,800,480,px,py,&sx,&sy);
                bool expected_valid = expected_x>=0 && expected_x<=1343 &&
                    expected_y>=0 && expected_y<=967;
                assert(valid==expected_valid);
                assert(fabs(sx-expected_x)<1e-7 && fabs(sy-expected_y)<1e-7);
            }
        }
    }
}
int main(void) {
    check_bourke_inverse();
    PaniniConfig c = {1,180,120,0};
    double x,y,x2,y2;
    assert(panini_source(&c,1344,968,800,480,399.5,239.5,&x,&y));
    assert(fabs(x-671.5)<1e-8 && fabs(y-483.5)<1e-8);
    /* Horizontal symmetry and equidistant angular mapping of a known ray. */
    for (int k=0;k<=2;k++) {
        c.compression=k;
        double theta=30*PI/180, half=c.view_fov*PI/360;
        double extent=(k+1)*sin(half)/(k+cos(half));
        double u=(k+1)*sin(theta)/(k+cos(theta));
        double px=399.5+u*800/(2*extent);
        assert(panini_source(&c,1344,968,800,480,px,239.5,&x,&y));
        assert(fabs(x-(671.5+1344*theta/PI))<1e-7);
        assert(panini_source(&c,1344,968,800,480,799-px,239.5,&x2,&y2));
        assert(fabs(x+x2-1343)<1e-7 && fabs(y-y2)<1e-7);
    }
    c.pitch=20;
    assert(panini_source(&c,1344,968,800,480,399.5,239.5,&x,&y));
    assert(fabs(x-671.5)<1e-8 && fabs(y-(483.5-1344*20/180.0))<1e-7);
    c.compression=NAN;assert(!panini_valid(&c));
    c=(PaniniConfig){1,180,120,0};
    assert(!panini_source(&c,1344,968,800,480,NAN,0,&x,&y));
    c.view_fov=179;assert(!panini_valid(&c));
    c=(PaniniConfig){1,90,160,45};
    assert(!panini_source(&c,1344,968,800,480,799,0,&x,&y));
    puts("Panini geometry tests passed");
}

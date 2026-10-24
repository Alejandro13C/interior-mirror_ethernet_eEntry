#ifndef CAMERA_GST_PANINI_H
#define CAMERA_GST_PANINI_H
#include <gst/gst.h>
#include "panini.h"
bool camera_panini_register(void);
void camera_panini_configure(GstElement *element, const PaniniConfig *config);
#endif

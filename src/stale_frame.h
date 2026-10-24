#ifndef CAMERA_STALE_FRAME_H
#define CAMERA_STALE_FRAME_H
#include <gst/gst.h>
#include <stdbool.h>

/* Black frame with "SIN VIDEO" in the negotiated YUV layout, or NULL.
   mirror/rotate180: the display plane's transform; the text is drawn
   pre-inverted so it reads correctly on screen. */
GstBuffer *camera_stale_frame(GstCaps *caps, bool mirror, bool rotate180);
#endif

#ifndef CAMERA_H264_SPS_H
#define CAMERA_H264_SPS_H
#include <stddef.h>
#include <stdint.h>

/* Rewrite an SPS NAL (header byte included, emulation-prevented) so that its
   VUI declares bitstream_restriction with max_num_reorder_frames = 0. The
   camera's SPS omits it, so the hardware decoder holds every picture until
   the next one arrives (one frame of latency). Only the simple Baseline/Main
   layout the camera uses is handled; the parsed SPS must re-encode to the
   original bytes first. Returns the new length, or 0 to leave it unchanged. */
size_t h264_sps_restrict(const uint8_t *nal, size_t size, uint8_t *out, size_t capacity);
#endif

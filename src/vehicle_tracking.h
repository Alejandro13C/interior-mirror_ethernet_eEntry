#ifndef CAMERA_VEHICLE_TRACKING_H
#define CAMERA_VEHICLE_TRACKING_H
#include "vehicle_protocol.h"
typedef struct {
    CameraVehicle vehicles[CAMERA_AI_MAX];
    uint64_t last_ns[CAMERA_AI_MAX];
    uint32_t next_id;
} CameraTracks;
#ifdef __cplusplus
extern "C" {
#endif
void camera_track(CameraTracks *tracks,CameraVehicle *vehicles,size_t count,uint64_t captured_ns);
#ifdef __cplusplus
}
#endif
#endif

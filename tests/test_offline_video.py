#!/usr/bin/env python3
"""Optional PC regression against the same street image used by the native test."""
import importlib.util
from pathlib import Path
import sys

root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('offline_video',root/'tools/analyze-video.py')
tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)
if len(sys.argv)!=2:raise SystemExit('Usage: test_offline_video.py street.png')
image=tool.cv2.imread(sys.argv[1]);assert image is not None and image.shape[:2]==(512,512)
found=tool.Detector(256).detect(image)
truth=[(334/512,236/512,82/512,100/512),(231/512,240/512,23/512,26/512)]
for x,y,w,h in truth:
    best=0
    for v in found:
        area=max(0,min(x+w,v.x+v.w)-max(x,v.x))*max(0,min(y+h,v.y+v.h)-max(y,v.y))
        best=max(best,area/(w*h+v.w*v.h-area))
    assert best>=.5, 'Missed or misplaced reference car'
assert len(found)==2
assert abs(found[0].confidence-.78862)<.005 and abs(found[1].confidence-.505088)<.005, 'PC/native decode mismatch'
tracker=tool.Tracker()
assert not tracker.update(found,1000000000), 'Unconfirmed detection shown'
static=tracker.update(found,1333333333)
assert len(static)==2 and all(v.hits==2 and v.vx==0 and v.vy==0 for v in static)
again=tracker.update(found,1666666666)
assert [v.track for v in static]==[v.track for v in again]
for v in found:v.x+=.02
moving=tracker.update(found,1999999999)
assert [v.track for v in moving]==[v.track for v in static] and all(v.vx>0 for v in moving)
for v in moving:
    x,y=tracker.anchor(v,200000000)
    assert v.x+v.w/2<x<=v.x+v.w
    assert abs(y-v.y)<.0001
tracker.close()
tracker=tool.Tracker(start_threshold=.25,group_vehicles=True)
weak=tool.Vehicle(.2,.3,.3,.2,.18,7,0,0,0,0)
assert not tracker.update([weak],1000000000)
assert not tracker.update([weak],1200000000), 'Weak-only new track must stay hidden'
weak.confidence=.35;weak.category=2
confirmed=tracker.update([weak],1400000000)
assert len(confirmed)==1 and confirmed[0].hits==3
identity=confirmed[0].track
weak.confidence=.17;weak.category=5
continued=tracker.update([weak],1600000000)
assert len(continued)==1 and continued[0].track==identity and continued[0].hits==4
tracker.close()
car=tool.Vehicle(.2,.3,.3,.2,.9,2,1,2,0,0)
markers=tool.MarkerBuffer(350)
markers.accept(1200000000,1000000000,[car])
markers.accept(1366666666,1166666666,[])
assert markers.visible(1400000000), 'One missed analysis erased the track'
assert not markers.visible(1550000001), 'Hold limit was exceeded'
markers=tool.MarkerBuffer(500)
markers.accept(1600000000,1000000000,[car])
assert markers.visible(1620000000)
assert not markers.visible(1630000001), 'Coasting must not reset the original capture TTL'
markers=tool.MarkerBuffer(350)
markers.accept(1200000000,1000000000,[car])
new=tool.Vehicle.from_buffer_copy(car);new.track=2
markers.accept(1300000000,1100000000,[new])
assert [v.track for _,v in markers.visible(1300000000)]==[2], 'Duplicate old/new IDs remained visible'
markers=tool.MarkerBuffer()
markers.accept(1200000000,1000000000,[car]);markers.accept(1300000000,1100000000,[])
assert not markers.visible(1300000000), 'Default preview semantics changed'
# Synthetic ONNX outputs check score semantics, letterbox reversal and NMS.
class OutputNet:
    def __init__(self,data):self.data=data
    def setInput(self,blob):pass
    def forward(self):return self.data
for name in ('yolov5n','yolo11n'):
    detector=tool.YoloDetector.__new__(tool.YoloDetector);detector.name=name
    if name=='yolov5n':
        data=tool.np.zeros((1,25200,85),tool.np.float32)
        data[0,0,:4]=[320,320,64,64];data[0,0,4]=.9;data[0,0,7]=.8
        data[0,1]=data[0,0];data[0,1,4]=.8
    else:
        data=tool.np.zeros((1,84,8400),tool.np.float32)
        data[0,:4,0]=[320,320,64,64];data[0,6,0]=.8
        data[0,:,1]=data[0,:,0];data[0,6,1]=.7
    detector.net=OutputNet(data)
    boxes=detector.detect(tool.np.zeros((100,200,3),tool.np.uint8),.4)
    assert len(boxes)==1
    v=boxes[0]
    assert abs(v.x-.45)<.0001 and abs(v.y-.4)<.0001 and abs(v.w-.1)<.0001 and abs(v.h-.2)<.0001
    assert abs(v.confidence-(.72 if name=='yolov5n' else .8))<.0001
print('PC/native detections, stationary confirmation, stable IDs and motion prediction passed')

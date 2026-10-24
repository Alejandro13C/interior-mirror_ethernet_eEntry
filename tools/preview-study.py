#!/usr/bin/env python3
"""Short synchronized real-video comparison: neutral triangles, no visible boxes.
Offline per-frame inference, two-hit confirmation, no artificial delivery delay.
Playback speed must not be confused with measured inference speed.
"""
import os
os.environ.setdefault('OMP_WAIT_POLICY','PASSIVE')
import argparse,importlib.util,json,subprocess,time
from pathlib import Path
import cv2,numpy as np,imageio_ffmpeg
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('study',ROOT/'tools/study-detectors.py');s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
p=argparse.ArgumentParser(description=__doc__);p.add_argument('folder',type=Path);a=p.parse_args();folder=a.folder
manifest=json.loads((folder/'samples.json').read_text());cv2.setNumThreads(4)
models=[s.v.Detector(416)];names=['NanoDet416 / NCNN']
for name in ('yolov5n','yolov5s','yolo11n'):
 model=s.v.YoloDetector.__new__(s.v.YoloDetector);model.name=name;model.net=s.Backend('openvino',ROOT/'models'/f'{name}.onnx',4);models.append(model);names.append(name+' / OpenVINO')
out=folder/'triangles_6-clips_4-models.mp4'
if out.exists():raise RuntimeError('Output exists')
encoder=subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-f','rawvideo','-pix_fmt','bgr24','-s','1280x768','-r','30','-i','pipe:0','-an','-c:v','libx264','-threads','2','-preset','fast','-crf','21','-pix_fmt','yuv420p',str(out)],stdin=subprocess.PIPE)
logs=[];started=time.perf_counter()
try:
 for clip_index,clip in enumerate(manifest['clips']):
  cap=cv2.VideoCapture(clip['path']);start=clip['duration_s']*.2;cap.set(cv2.CAP_PROP_POS_MSEC,start*1000)
  trackers=[s.v.Tracker(start_threshold=.25,group_vehicles=True) for _ in models]
  previous=-1;image=None
  for index in range(120):
   target=round(index*clip['fps']/30)
   while previous<target:
    ok,frame=cap.read()
    if not ok:raise RuntimeError('Short source')
    image=s.v.canvas(frame);previous+=1
   panels=[];record={'clip':clip_index,'time_s':start+index/30,'models':{}}
   for j,(model,tracker,name) in enumerate(zip(models,trackers,names)):
    found=model.detect(s.v.thumbnail(image) if j==0 else image,.15)
    confirmed=tracker.update(found,1000000000+round(index/30*1e9));panel=image.copy()
    record['models'][name]=[{'id':int(b.track),'x':float(b.x+b.w/2),'y':float(b.y),'score':float(b.confidence)} for b in confirmed]
    for b in confirmed:
     x=int((b.x+b.w/2)*800);y=max(18,int(b.y*480)-4)
     cv2.polylines(panel,[np.array([[x,y],[x-8,y-13],[x+8,y-13]],np.int32)],True,(255,255,255),2)
    cv2.rectangle(panel,(0,0),(800,52),(0,0,0),-1)
    cv2.putText(panel,name,(8,23),0,.6,(255,255,255),1)
    cv2.putText(panel,f'Clip {clip_index+1} | {start+index/30:.1f}s | OFFLINE - not live FPS',(8,45),0,.45,(255,255,255),1)
    panels.append(cv2.resize(panel,(640,384)))
   encoder.stdin.write(np.vstack((np.hstack(panels[:2]),np.hstack(panels[2:]))).tobytes());logs.append(record)
  for tracker in trackers:tracker.close()
  cap.release();print(f'Completed clip {clip_index+1}/{len(manifest["clips"])}',flush=True)
finally:
 encoder.stdin.close();code=encoder.wait()
if code:raise RuntimeError('Encoder failed')
(folder/'preview-markers.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in logs))
(folder/'preview-settings.json').write_text(json.dumps({'clips':manifest['clips'],'duration_per_clip_s':4,'start_fraction':.2,'threshold':.15,'start_threshold':.25,'confirmation_hits':2,'models':names,'inference':'every output frame, offline, no artificial delay','output_fps':30,'elapsed_s':time.perf_counter()-started},indent=2)+'\n')

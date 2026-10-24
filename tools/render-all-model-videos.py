#!/usr/bin/env python3
"""Render every source frame with all five confirmed marker streams + original.
Waits for each clip's inference to complete; generates no Markdown.
"""
import argparse,fcntl,importlib.util,json,subprocess,time
from pathlib import Path
import cv2,numpy as np,imageio_ffmpeg
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('video',ROOT/'tools/analyze-video.py');v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
MODELS=('nanodet256','nanodet416','yolov5n','yolov5s','yolo11n')
NAMES=('01_winchester','02_unity','03_hornet','04_dwarf','05_texana','06_x5m')
p=argparse.ArgumentParser(description=__doc__);p.add_argument('folder',type=Path);p.add_argument('--clip',choices=NAMES);a=p.parse_args();root=a.folder;out=root/'synchronized-videos';out.mkdir(exist_ok=True)
manifest=json.loads((root/'manifest.json').read_text());MODELS=tuple(manifest['models']);cv2.setNumThreads(1)
for n,name in enumerate(NAMES):
 if a.clip and a.clip!=name:continue
 with (out/f'{name}.lock').open('w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  target=out/f'{name}.mp4'
  while not all((root/name/model/'metrics.json').exists() for model in MODELS):time.sleep(2)
  cap=cv2.VideoCapture(manifest['sources'][n]['path'],cv2.CAP_FFMPEG,[cv2.CAP_PROP_N_THREADS,1]);fps=cap.get(cv2.CAP_PROP_FPS);total=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
  if target.exists():
   check=cv2.VideoCapture(str(target));valid=int(check.get(cv2.CAP_PROP_FRAME_COUNT))==total
   if valid:check.set(cv2.CAP_PROP_POS_FRAMES,total-1);valid=check.read()[0]
   check.release()
   if valid:cap.release();continue
   target.unlink()
  streams=[(root/name/model/'detections.jsonl').open() for model in MODELS];partial=out/f'{name}.partial.mp4'
  command=[imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-y','-v','error','-f','rawvideo','-pix_fmt','bgr24','-s',f'{1200 if len(MODELS)==5 else 800}x480','-r',str(fps),'-i','pipe:0','-i',manifest['sources'][n]['path'],'-map','0:v:0','-map','1:a?','-c:v','libx264','-threads','2','-preset','ultrafast','-crf','24','-pix_fmt','yuv420p','-c:a','aac','-b:a','96k','-t',str(total/fps),'-movflags','+faststart',str(partial)]
  with (out/f'{name}.encoder.log').open('w') as log:
   encoder=subprocess.Popen(command,stdin=subprocess.PIPE,stderr=log)
   try:
    for index in range(total):
     ok,source=cap.read()
     if not ok:raise RuntimeError('Source ended early')
     source=v.canvas(source);panels=[]
     for j,model in enumerate(MODELS+('original',)):
      panel=source.copy()
      if j<len(MODELS):
       row=json.loads(next(streams[j]));assert row.get('frame_index',index)==index
       if abs(row['time_s']-index/fps)>1e-4:raise RuntimeError('Marker/source desynchronization')
       for b in row['confirmed']:
        x,y,w,h=b['box'];cx=min(787,max(12,round((x+w/2)*800)));tip=min(479,max(22,round(y*480)-4));points=np.array([[cx,tip],[cx-10,tip-20],[cx+10,tip-20]],np.int32)
        cv2.fillConvexPoly(panel,points,(255,255,255));cv2.polylines(panel,[points],True,(0,0,0),2)
      cv2.rectangle(panel,(0,0),(800,32),(0,0,0),-1);cv2.putText(panel,f'{model} | {index/fps:.2f}s | OFFLINE',(8,22),0,.6,(255,255,255),1);panels.append(cv2.resize(panel,(400,240)))
     cols=3 if len(MODELS)==5 else 2
     encoder.stdin.write(np.vstack((np.hstack(panels[:cols]),np.hstack(panels[cols:]))).tobytes())
     if index%300==0:(out/f'{name}.progress.json').write_text(json.dumps({'frames':index+1,'expected_frames':total}))
    for stream in streams:
     if next(stream,None) is not None:raise RuntimeError('Extra marker records')
    encoder.stdin.close();code=encoder.wait(timeout=60)
    if code:raise RuntimeError('Encoder failure')
    partial.replace(target);print(name,total,'frames rendered',flush=True)
   finally:
    cap.release()
    for stream in streams:stream.close()
    if encoder.poll() is None:encoder.terminate();encoder.wait(timeout=10)

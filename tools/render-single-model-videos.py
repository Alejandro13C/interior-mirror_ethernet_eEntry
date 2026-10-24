#!/usr/bin/env python3
"""Replay saved confirmed detections on full-size source videos. No inference or label edits."""
import argparse,concurrent.futures,hashlib,importlib.util,json,subprocess
from pathlib import Path
import cv2,numpy as np,imageio_ffmpeg
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('video',ROOT/'tools/analyze-video.py');v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
NAMES=('01_winchester','02_unity','03_hornet','04_dwarf','05_texana','06_x5m')

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path)
 p.add_argument('--input',type=Path,default=ROOT/'comparisons/old/all-models_all-frames_6-videos');p.add_argument('--model',default='yolov5n');p.add_argument('--workers',type=int,default=2);a=p.parse_args()
 a.output.mkdir(parents=True,exist_ok=True);manifest=json.loads((a.input/'manifest.json').read_text());cv2.setNumThreads(1)
 if a.model not in manifest['models']:raise RuntimeError('Model not present in study')
 def verify(target,total,fps):
  cap=cv2.VideoCapture(str(target))
  try:
   if int(cap.get(cv2.CAP_PROP_FRAME_COUNT))!=total or abs(cap.get(cv2.CAP_PROP_FPS)-fps)>.001:raise RuntimeError('Encoded video frame count/fps mismatch')
   if (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))!=(800,480):raise RuntimeError('Unexpected resolution')
   for index in (0,total//2,total-1):
    cap.set(cv2.CAP_PROP_POS_FRAMES,index)
    if not cap.read()[0]:raise RuntimeError('Encoded frame cannot be decoded')
  finally:cap.release()
 def render(n):
  name=NAMES[n];source=manifest['sources'][n];folder=a.input/name/a.model
  metadata=json.loads((folder/'metrics.json').read_text());target=a.output/f'{name}.mp4'
  if hashlib.sha256(Path(source['path']).read_bytes()).hexdigest()!=source['sha256']:raise RuntimeError('Source changed')
  cap=cv2.VideoCapture(source['path'],cv2.CAP_FFMPEG,[cv2.CAP_PROP_N_THREADS,1]);fps=cap.get(cv2.CAP_PROP_FPS);total=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
  if total!=metadata['frames']:raise RuntimeError('Inference/source count mismatch')
  record=a.output/f'{name}.json'
  if target.exists() and record.exists():
   data=json.loads(record.read_text())
   if data['detection_sha256']!=hashlib.sha256((folder/'detections.jsonl').read_bytes()).hexdigest():raise RuntimeError('Saved detections changed')
   verify(target,total,fps);cap.release();return data
  partial=a.output/f'{name}.partial.mp4';logpath=a.output/f'{name}.encoder.log'
  cmd=[imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-y','-v','error','-f','rawvideo','-pix_fmt','bgr24','-s','800x480','-r',str(fps),'-i','pipe:0','-i',source['path'],'-map','0:v:0','-map','1:a?','-c:v','libx264','-threads','2','-preset','ultrafast','-crf','22','-pix_fmt','yuv420p','-c:a','aac','-b:a','96k','-t',str(total/fps),'-movflags','+faststart',str(partial)]
  with logpath.open('w') as log,(folder/'detections.jsonl').open() as stream:
   encoder=subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=log)
   try:
    for index in range(total):
     ok,frame=cap.read()
     if not ok:raise RuntimeError('Unexpected source EOF')
     row=json.loads(next(stream))
     if row.get('frame_index',index)!=index or abs(row['time_s']-index/fps)>1e-4:raise RuntimeError('Detection/video desynchronization')
     frame=v.canvas(frame)
     for b in row['confirmed']:
      x,y,w,h=b['box'];cx=min(787,max(12,round((x+w/2)*800)));tip=min(479,max(22,round(y*480)-4));pts=np.array([[cx,tip],[cx-10,tip-20],[cx+10,tip-20]],np.int32)
      cv2.fillConvexPoly(frame,pts,(255,255,255));cv2.polylines(frame,[pts],True,(0,0,0),2)
     cv2.rectangle(frame,(0,0),(800,28),(0,0,0),-1);cv2.putText(frame,f'{a.model} | {index/fps:.2f}s | OFFLINE',(8,20),0,.55,(255,255,255),1)
     encoder.stdin.write(frame.tobytes())
     if index%500==0:(a.output/f'{name}.progress.json').write_text(json.dumps({'frames':index+1,'expected_frames':total}))
    if next(stream,None) is not None:raise RuntimeError('Extra inference records')
    encoder.stdin.close()
    if encoder.wait(timeout=60):raise RuntimeError('Encoder failed; see '+str(logpath))
    verify(partial,total,fps);partial.replace(target)
   finally:
    cap.release()
    if encoder.poll() is None:encoder.terminate();encoder.wait(timeout=10)
  data={'clip':name,'model':a.model,'frames':total,'fps':fps,'size':[800,480],'source':source['path'],'source_sha256':source['sha256'],'detections':str(folder/'detections.jsonl'),'detection_sha256':hashlib.sha256((folder/'detections.jsonl').read_bytes()).hexdigest(),'model_sha256':metadata['model_sha256'],'first_middle_last_decode':True,'markers':'Original saved confirmed predictions, neutral white triangles; no manual removal, no bounding boxes.','limits':'Offline playback at source fps; no measured Raspberry Pi timing.'}
  record.write_text(json.dumps(data,indent=2)+'\n');print(name,total,'frames verified',flush=True)
  if logpath.stat().st_size==0:logpath.unlink()
  (a.output/f'{name}.progress.json').unlink(missing_ok=True)
  return data
 with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:results=list(pool.map(render,range(6)))
 (a.output/'verification.json').write_text(json.dumps({'model':a.model,'videos':results,'source_frames':sum(x['frames'] for x in results),'limits':'Saved model predictions replayed, not manually corrected. Playback speed does not measure real-time processing.'},indent=2)+'\n')

if __name__=='__main__':main()

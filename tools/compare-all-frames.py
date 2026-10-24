#!/usr/bin/env python3
"""Run all five detector profiles on every frame of the six original racing clips.
Stores raw + confirmed triangles; presence statistics are not accuracy measurements.
Resume at clip/model boundaries. No live Pi configuration is changed.
"""
import os
os.environ.setdefault('OMP_WAIT_POLICY','PASSIVE')
import argparse,concurrent.futures,hashlib,importlib.util,json,resource,subprocess,sys,time
from pathlib import Path
import cv2
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('study',ROOT/'tools/study-detectors.py');s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
NAMES=('01_winchester','02_unity','03_hornet','04_dwarf','05_texana','06_x5m')
MODELS=('nanodet256','nanodet416','yolov5n','yolov5s','yolo11n')
def save(path,data):
 temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,indent=2)+'\n');temp.replace(path)
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);p.add_argument('--worker',nargs=2);p.add_argument('--workers',type=int,default=3);p.add_argument('--threads',type=int,default=2)
 p.add_argument('--models',nargs='+',default=list(MODELS));p.add_argument('--engine',choices=['cpu','cuda'],default='cpu');p.add_argument('--manifest',type=Path,default=ROOT/'comparisons/old/nanodet416_all-frames_6-videos/source-manifest.json');a=p.parse_args();global_models=tuple(a.models);a.output.mkdir(parents=True,exist_ok=True)
 manifest=json.loads(a.manifest.read_text());clips=manifest['clips']
 if len(clips)!=6:raise RuntimeError('This study expects the original six videos')
 if not a.worker:
  for clip in clips:
   if hashlib.sha256(Path(clip['path']).read_bytes()).hexdigest()!=clip['sha256']:raise RuntimeError('Source changed')
  save(a.output/'manifest.json',{'sources':clips,'models':list(global_models),'threshold':.15,'start_threshold':.25,'threads_per_yolo_worker':a.threads,'parallel_jobs':a.workers,'yolo_engine':a.engine,'limits':'All frames, no manual all-frame ground truth. Runtime metrics are concurrent, not isolated benchmarks.'})
  def run(job):
   n,name=job;cmd=[sys.executable,str(Path(__file__).resolve()),str(a.output),'--worker',str(n),name,'--threads',str(a.threads),'--engine',a.engine,'--manifest',str(a.manifest),'--models',*global_models]
   folder=a.output/NAMES[n]/name;folder.mkdir(parents=True,exist_ok=True)
   with (folder/'worker.log').open('a') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
   print('Completed '+NAMES[n]+'/'+name,flush=True)
  # More expensive jobs first, interleaved with other models to keep all CPUs useful.
  jobs=[(n,name) for n in range(6) for name in global_models]
  with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:list(pool.map(run,jobs))
  results=[json.loads((a.output/NAMES[n]/name/'metrics.json').read_text()) for n in range(6) for name in global_models]
  save(a.output/'summary.json',{'models':list(global_models),'clips':list(NAMES),'model_frames':sum(r['frames'] for r in results),'results':results,'limits':'Detection counts are not accuracy or unique vehicles. Ground truth is required for success rates.'})
  return
 n=int(a.worker[0]);name=a.worker[1];clip=clips[n];folder=a.output/NAMES[n]/name;folder.mkdir(parents=True,exist_ok=True)
 if (folder/'metrics.json').exists():return
 if name=='nanodet416':
  previous=ROOT/'comparisons/old/nanodet416_all-frames_6-videos'/NAMES[n];old=json.loads((previous/'metrics.json').read_text())
  if old['input_size']!=416 or old['threshold']!=.15 or old['analysis_calls']!=old['source_frames']:raise RuntimeError('Incompatible cached inference')
  (folder/'detections.jsonl').symlink_to((previous/'detections.jsonl').resolve())
  save(folder/'metrics.json',{'clip':NAMES[n],'model':name,'frames':old['source_frames'],'reused':True,'raw_present_frames':None,'source':clip['path'],'source_sha256':clip['sha256'],'model_sha256':old['model_sha256']});return
 cv2.setNumThreads(1);cap=cv2.VideoCapture(clip['path'],cv2.CAP_FFMPEG,[cv2.CAP_PROP_N_THREADS,1]);fps=cap.get(cv2.CAP_PROP_FPS);expected=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
 if not cap.isOpened() or fps<=0:raise RuntimeError('Cannot open source')
 if name in ('yolo26n','picodet_xs320','picodet_s320'):
  from modern_detectors import Detector
  model=Detector(name,a.engine,a.threads,vehicle_type=s.v.Vehicle);weights=ROOT/'models'/f'{name}.onnx';engine='onnxruntime-'+a.engine
 elif name.startswith('nanodet'):model=s.v.Detector(256);weights=ROOT/'models/nanodet_plus_m.bin';engine='ncnn'
 else:
  model=s.v.YoloDetector.__new__(s.v.YoloDetector);model.name=name;weights=ROOT/'models'/f'{name}.onnx';
  if a.engine=='cuda':
   from gpu_backend import CudaBackend
   model.net=CudaBackend(weights);engine='onnxruntime-cuda'
  else:model.net=s.Backend('openvino',weights,a.threads,pinning=False);engine='openvino'
 tracker=s.v.Tracker(start_threshold=.25,group_vehicles=True);raw_present=confirmed_present=0;calls=0;sum_ms=0;started=time.perf_counter();cpu=time.process_time()
 partial=folder/'detections.partial.jsonl'
 resumed=0
 if partial.exists():
  valid=[]
  for line in partial.read_text().splitlines():
   try:row=json.loads(line)
   except json.JSONDecodeError:break
   if row['frame_index']!=resumed:raise RuntimeError('Noncontiguous partial results')
   found=[s.v.Vehicle(*b['box'],b['score'],b['class'],0,0,0,0) for b in row['raw']]
   tracker.update(found,1000000000+round(row['time_s']*1e9));resumed+=1;calls+=1
   sum_ms+=row['inference_ms'];raw_present+=bool(row['raw']);confirmed_present+=bool(row['confirmed']);valid.append(line)
  partial.write_text(''.join(line+'\n' for line in valid));cap.set(cv2.CAP_PROP_POS_FRAMES,resumed)
 try:
  with partial.open('a') as records:
   for index in range(resumed,expected):
    ok,frame=cap.read()
    if not ok:raise RuntimeError('Unexpected EOF')
    image=s.v.canvas(frame);image=s.v.thumbnail(image) if name.startswith('nanodet') else image
    before=time.perf_counter();found=model.detect(image,.15);ms=(time.perf_counter()-before)*1000;sum_ms+=ms;calls+=1
    t=index/fps;confirmed=tracker.update(found,1000000000+round(t*1e9));raw_present+=bool(found);confirmed_present+=bool(confirmed)
    records.write(json.dumps({'frame_index':index,'time_s':t,'inference_ms':ms,'engine':engine,'raw':[{'box':[b.x,b.y,b.w,b.h],'score':b.confidence,'class':b.category} for b in found],'confirmed':[{'id':b.track,'box':[b.x,b.y,b.w,b.h],'score':b.confidence,'vx':b.vx,'vy':b.vy} for b in confirmed]})+'\n')
    if (index+1)%100==0:
     records.flush();save(folder/'progress.json',{'frames':index+1,'expected_frames':expected,'elapsed_s':time.perf_counter()-started})
 finally:cap.release();tracker.close()
 if calls!=expected:raise RuntimeError('Not every frame analyzed')
 partial.replace(folder/'detections.jsonl');elapsed=time.perf_counter()-started
 save(folder/'metrics.json',{'clip':NAMES[n],'model':name,'frames':calls,'source':clip['path'],'source_sha256':clip['sha256'],'model_sha256':hashlib.sha256(weights.read_bytes()).hexdigest(),'resumed_cached_frames':resumed,'cpu_pinning':False,'engine':engine,'threads':1 if engine=='ncnn' else a.threads,'wall_s':elapsed,'analysis_mean_ms':sum_ms/calls,'cpu_one_core_percent':100*(time.process_time()-cpu)/elapsed,'peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,'raw_present_frames':raw_present,'confirmed_present_frames':confirmed_present,'limits':'Concurrent job; speed/RAM are not isolated or Pi benchmarks. Presence is not correctness.'})
if __name__=='__main__':main()

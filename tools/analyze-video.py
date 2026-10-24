#!/usr/bin/env python3
"""PC diagnostic using the live NanoDet-Plus weights, C tracker and marker geometry.

Dependencies: ncnn==1.0.20250916, numpy, opencv-python-headless, imageio-ffmpeg.
Output is an offline preview: analysis delivery delay is explicitly simulated.
No Panini warp is applied to an unrelated camera's video.
"""
import argparse
from collections import deque
import ctypes as C
import hashlib
import json
import math
from pathlib import Path
import resource
import subprocess
import threading
import time

import cv2
import imageio_ffmpeg
import ncnn
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 800, 480


class Vehicle(C.Structure):
    _fields_ = [(k,C.c_float) for k in ('x','y','w','h','confidence')] + [
        (k,C.c_uint32) for k in ('category','track','hits')] + [(k,C.c_float) for k in ('vx','vy')]


if C.sizeof(Vehicle)!=40:
    raise RuntimeError('C/Python vehicle layout mismatch')


class Tracker:
    def __init__(self,start_threshold=None,group_vehicles=False):
        self.start_threshold=start_threshold
        self.group_vehicles=group_vehicles
        self.peaks={}
        self.lib=C.CDLL(str(ROOT/'build/liboffline-tracking.so'))
        self.lib.offline_tracks_new.restype=C.c_void_p
        self.lib.offline_tracks_free.argtypes=[C.c_void_p]
        self.lib.offline_tracks_update.argtypes=[C.c_void_p,C.POINTER(Vehicle),C.c_size_t,C.c_uint64]
        self.lib.offline_anchor.argtypes=[C.POINTER(Vehicle),C.c_uint64,C.POINTER(C.c_float),C.POINTER(C.c_float)]
        self.state=self.lib.offline_tracks_new()
        if not self.state:raise MemoryError('Cannot allocate tracker')

    def update(self,found,ns):
        vehicles=(Vehicle*len(found))(*found)
        categories=[v.category for v in vehicles]
        if self.group_vehicles:
            # A car/truck/bus classification change must not create a new vehicle ID.
            for v in vehicles:
                if v.category in (2,5,7):v.category=2
        self.lib.offline_tracks_update(self.state,vehicles,len(vehicles),ns)
        self.peaks={k:p for k,p in self.peaks.items() if ns-p[1]<=750000000}
        result=[]
        for v,category in zip(vehicles,categories):
            v.category=category
            peak=max(v.confidence,self.peaks.get(v.track,(0,0))[0]);self.peaks[v.track]=(peak,ns)
            if v.hits>=2 and (self.start_threshold is None or peak>=self.start_threshold):
                result.append(Vehicle.from_buffer_copy(v))
        return result

    def anchor(self,v,age):
        x,y=C.c_float(),C.c_float()
        self.lib.offline_anchor(C.byref(v),age,C.byref(x),C.byref(y))
        return x.value,y.value

    def close(self):
        self.lib.offline_tracks_free(self.state)


class Detector:
    def __init__(self,size):
        self.size=size
        ncnn.set_omp_num_threads(1)
        self.net=ncnn.Net()
        self.net.opt.num_threads=1
        self.net.opt.use_vulkan_compute=False
        self.net.opt.use_fp16_storage=False
        self.net.opt.use_bf16_storage=False
        prefix=ROOT/'models/nanodet_plus_m'
        if self.net.load_param(str(prefix)+'.param') or self.net.load_model(str(prefix)+'.bin'):
            raise RuntimeError('Cannot load NanoDet-Plus')

    def detect(self,image,threshold=.4):
        h,w=image.shape[:2];scale=self.size/max(w,h)
        iw,ih=max(1,int(w*scale)),max(1,int(h*scale))
        pw,ph=(iw+63)//64*64,(ih+63)//64*64;left,top=(pw-iw)//2,(ph-ih)//2
        mat=ncnn.Mat.from_pixels_resize(np.ascontiguousarray(image),ncnn.Mat.PixelType.PIXEL_BGR,w,h,iw,ih)
        mat=ncnn.copy_make_border(mat,top,ph-ih-top,left,pw-iw-left,ncnn.BorderType.BORDER_CONSTANT,0.)
        mat.substract_mean_normalize([103.53,116.28,123.675],[1/57.375,1/57.12,1/58.395])
        ex=self.net.create_extractor()
        if ex.input('data',mat):raise RuntimeError('NCNN input failed')
        ret,out=ex.extract('output')
        if ret:raise RuntimeError('NCNN output failed')
        data=np.array(out,copy=True)
        if data.ndim!=2 or data.shape[1]!=112:raise RuntimeError('Unexpected output shape')
        offset=0;candidates=[]
        for stride in (8,16,32,64):
            grid=(pw+stride-1)//stride;rows=grid*((ph+stride-1)//stride)
            level=data[offset:offset+rows];offset+=rows
            if len(level)!=rows:raise RuntimeError('Unexpected output grid')
            labels=level[:,:80].argmax(axis=1);scores=level[np.arange(rows),labels]
            selected=np.flatnonzero(np.isin(labels,[2,3,5,7]) & (scores>=threshold))
            if not len(selected):continue
            logits=level[selected,80:].reshape(-1,4,8)
            weights=np.exp(logits-logits.max(axis=2,keepdims=True));weights/=weights.sum(axis=2,keepdims=True)
            distances=(weights*np.arange(8,dtype=np.float32)).sum(axis=2)*stride
            cx=(selected%grid)*stride;cy=(selected//grid)*stride
            boxes=np.stack(((cx-distances[:,0]-left)/scale/w,(cy-distances[:,1]-top)/scale/h,
                            (cx+distances[:,2]-left)/scale/w,(cy+distances[:,3]-top)/scale/h),axis=1)
            for index,box in zip(selected,np.clip(boxes,0,1)):
                x1,y1,x2,y2=map(float,box)
                if x2>x1 and y2>y1:candidates.append(Vehicle(x1,y1,x2-x1,y2-y1,float(scores[index]),int(labels[index]),0,0,0,0))
        if offset!=len(data):raise RuntimeError('Unexpected output rows')
        kept=[]
        for v in sorted(candidates,key=lambda v:v.confidence,reverse=True):
            for prior in kept:
                area=max(0,min(v.x+v.w,prior.x+prior.w)-max(v.x,prior.x))*max(0,min(v.y+v.h,prior.y+prior.h)-max(v.y,prior.y))
                if area/(v.w*v.h+prior.w*prior.h-area)>.5:break
            else:
                kept.append(v)
                if len(kept)==16:break
        return kept


class YoloDetector:
    """CPU ONNX diagnostic; Ultralytics letterbox, RGB/255 and decoded boxes.

    v5 output: xywh, objectness, 80 class scores.
    11 output: xywh, 80 class scores (no separate objectness).
    """
    def __init__(self,name):
        self.name=name
        self.net=cv2.dnn.readNetFromONNX(str(ROOT/'models'/f'{name}.onnx'))
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

    def detect(self,image,threshold=.25):
        h,w=image.shape[:2];scale=640/max(w,h)
        iw,ih=round(w*scale),round(h*scale);left,top=(640-iw)//2,(640-ih)//2
        fitted=cv2.copyMakeBorder(cv2.resize(image,(iw,ih)),top,640-ih-top,left,640-iw-left,
                                 cv2.BORDER_CONSTANT,value=(114,114,114))
        self.net.setInput(cv2.dnn.blobFromImage(fitted,1/255.,(640,640),swapRB=True,crop=False))
        data=np.asarray(self.net.forward())[0]
        if self.name.startswith('yolo11'):
            if data.shape!=(84,8400):raise RuntimeError('Unexpected YOLO11 output')
            data=data.T;labels=data[:,4:].argmax(axis=1);scores=data[np.arange(len(data)),4+labels]
        else:
            if data.shape!=(25200,85):raise RuntimeError('Unexpected YOLOv5 output')
            labels=np.asarray(data[:,5:],dtype=np.float32).argmax(axis=1);scores=data[:,4]*data[np.arange(len(data)),5+labels]
        selected=np.flatnonzero(np.isin(labels,[2,3,5,7]) & (scores>=threshold))
        if not len(selected):return []
        boxes=[];valid=[]
        for index in selected:
            cx,cy,bw,bh=data[index,:4]
            x1=max(0.,float((cx-bw/2-left)/scale));y1=max(0.,float((cy-bh/2-top)/scale))
            x2=min(float(w),float((cx+bw/2-left)/scale));y2=min(float(h),float((cy+bh/2-top)/scale))
            if x2>x1 and y2>y1:boxes.append([x1,y1,x2-x1,y2-y1]);valid.append(int(index))
        if not boxes:return []
        kept=cv2.dnn.NMSBoxes(boxes,[float(scores[i]) for i in valid],threshold,.45)
        result=[]
        for item in np.asarray(kept).reshape(-1)[:16]:
            x,y,bw,bh=boxes[item];index=valid[item]
            result.append(Vehicle(x/w,y/h,bw/w,bh/h,float(scores[index]),int(labels[index]),0,0,0,0))
        return result


def canvas(frame):
    h,w=frame.shape[:2];scale=min(WIDTH/w,HEIGHT/h)
    nw,nh=int(w*scale),int(h*scale)
    out=np.zeros((HEIGHT,WIDTH,3),np.uint8);x,y=(WIDTH-nw)//2,(HEIGHT-nh)//2
    out[y:y+nh,x:x+nw]=cv2.resize(frame,(nw,nh),interpolation=cv2.INTER_AREA)
    return out


def thumbnail(frame):
    # Match the live 416x250 I420 export and limited-range BT.601 reconstruction.
    flat=cv2.cvtColor(frame,cv2.COLOR_BGR2YUV_I420).ravel();pixels=WIDTH*HEIGHT
    yy=cv2.resize(flat[:pixels].reshape(HEIGHT,WIDTH),(416,250),interpolation=cv2.INTER_NEAREST).astype(np.int32)-16
    u=cv2.resize(flat[pixels:pixels+pixels//4].reshape(HEIGHT//2,WIDTH//2),(208,125),interpolation=cv2.INTER_NEAREST).astype(np.int32)-128
    v=cv2.resize(flat[pixels+pixels//4:].reshape(HEIGHT//2,WIDTH//2),(208,125),interpolation=cv2.INTER_NEAREST).astype(np.int32)-128
    u=u.repeat(2,0).repeat(2,1);v=v.repeat(2,0).repeat(2,1)
    return np.stack((np.clip((298*yy+516*u+128)>>8,0,255),np.clip((298*yy-100*u-208*v+128)>>8,0,255),
                     np.clip((298*yy+409*v+128)>>8,0,255)),axis=2).astype(np.uint8)


class MarkerBuffer:
    """Coast briefly through missed analyses, bounded by original capture age."""
    def __init__(self,hold_ms=0,display_age_ms=120):
        self.hold_ns=round(hold_ms*1e6);self.display_ns=round(display_age_ms*1e6)
        self.items={}

    @staticmethod
    def overlap(a,b):
        area=max(0,min(a.x+a.w,b.x+b.w)-max(a.x,b.x))*max(0,min(a.y+a.h,b.y+b.h)-max(a.y,b.y))
        return area/(a.w*a.h+b.w*b.h-area)

    def accept(self,received,captured,vehicles):
        if not self.hold_ns:self.items.clear()
        incoming={v.track for v in vehicles}
        for v in vehicles:
            # Only retire nearly identical old boxes; never combine current observations.
            if self.hold_ns and v.track not in self.items:
                for key,(_,_,old) in list(self.items.items()):
                    if key not in incoming and self.overlap(v,old)>.7:self.items.pop(key)
            self.items[v.track]=(received,captured,v)

    def visible(self,now):
        result=[]
        for key,(received,captured,v) in list(self.items.items()):
            stale=now<captured or now+self.display_ns-captured>750000000
            if stale or (self.hold_ns and now-received>self.hold_ns):self.items.pop(key)
            else:result.append((captured,v))
        return result


class Resources:
    def __init__(self,child):
        self.pids=[__import__('os').getpid(),child];self.rows=[];self.done=threading.Event()
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()

    def run(self):
        while not self.done.is_set():
            rss=[]
            for pid in self.pids:
                try:
                    lines=Path(f'/proc/{pid}/status').read_text().splitlines()
                    rss.append(int(next(v for v in lines if v.startswith('VmRSS:')).split()[1])/1024)
                except (OSError,StopIteration):rss.append(0)
            self.rows.append(rss);self.done.wait(.25)

    def stop(self):
        self.done.set();self.thread.join()
        return {'analysis_rss_peak_mib':round(max(r[0] for r in self.rows),2),
                'encoder_rss_peak_mib':round(max(r[1] for r in self.rows),2),
                'sum_rss_peak_mib':round(max(sum(r) for r in self.rows),2)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--detector',choices=['nanodet','yolov5n','yolov5s','yolo11n'],default='nanodet')
    parser.add_argument('--size',type=int,choices=[256,320,416,640])
    parser.add_argument('--hz',type=float,default=3);parser.add_argument('--threshold',type=float,default=.4)
    parser.add_argument('--start-threshold',type=float,help='Require this score at least once before displaying a track')
    parser.add_argument('--group-vehicles',action='store_true',help='Associate car, bus and truck as one vehicle family')
    parser.add_argument('--hold-ms',type=float,default=0,help='Briefly retain confirmed tracks across missing observations, still subject to capture TTL')
    parser.add_argument('--threads',type=int,default=1,help='OpenCV CPU threads; NCNN inference stays single-threaded')
    parser.add_argument('--display-age-ms',type=float,default=120,help='Simulated display age used only for marker expiration')
    parser.add_argument('--delay-ms',type=float,default=200);parser.add_argument('--seconds',type=float)
    args=parser.parse_args()
    if args.size is None:args.size=256 if args.detector=='nanodet' else 640
    if (args.detector!='nanodet' and args.size!=640) or (args.detector=='nanodet' and args.size==640):
        parser.error('YOLO ONNX models require 640; NanoDet accepts 256, 320 or 416')
    if not all(math.isfinite(v) for v in (args.hz,args.threshold,args.delay_ms)) or not 0<args.hz<=60 or not 0<args.threshold<=1 or args.delay_ms<0:
        parser.error('Invalid frequency, threshold or delay')
    if args.seconds is not None and (not math.isfinite(args.seconds) or args.seconds<=0):parser.error('seconds must be positive')
    if args.start_threshold is not None and (not math.isfinite(args.start_threshold) or not args.threshold<=args.start_threshold<=1):
        parser.error('start-threshold must be between threshold and 1')
    if not math.isfinite(args.hold_ms) or not 0<=args.hold_ms<=500:parser.error('hold-ms must be between 0 and 500')
    if not 1<=args.threads<=8:parser.error('threads must be between 1 and 8')
    if not math.isfinite(args.display_age_ms) or not 0<=args.display_age_ms<750:parser.error('Invalid display age')
    args.output.mkdir(parents=True,exist_ok=True)
    out=args.output/'annotated.mp4'
    if out.exists():raise RuntimeError('Output already exists; choose another output directory')
    cv2.setNumThreads(args.threads)
    cap=cv2.VideoCapture(str(args.video),cv2.CAP_FFMPEG,[cv2.CAP_PROP_N_THREADS,1])
    fps=cap.get(cv2.CAP_PROP_FPS);total=int(cap.get(cv2.CAP_PROP_FRAME_COUNT));source_size=[int(cap.get(3)),int(cap.get(4))]
    if not cap.isOpened() or fps<=0 or total<=0:raise RuntimeError('Cannot open video or read timing')
    limit=min(total,int(math.ceil(args.seconds*fps))) if args.seconds else total
    detector=Detector(args.size) if args.detector=='nanodet' else YoloDetector(args.detector)
    tracker=Tracker(start_threshold=args.start_threshold,group_vehicles=args.group_vehicles)
    label=f'NanoDet-Plus {args.size}' if args.detector=='nanodet' else args.detector.upper()+' 640'
    command=[imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-y','-loglevel','error','-f','rawvideo','-pixel_format','bgr24',
             '-video_size',f'{WIDTH}x{HEIGHT}','-framerate',str(fps),'-i','pipe:0','-i',str(args.video),
             '-map','0:v:0','-map','1:a?','-c:v','libx264','-preset','fast','-threads','2','-crf','20',
             '-pix_fmt','yuv420p','-c:a','aac','-b:a','96k','-t',str(limit/fps),'-movflags','+faststart',str(out)]
    child_before=resource.getrusage(resource.RUSAGE_CHILDREN)
    log=open(args.output/'encoder.log','wb');encoder=subprocess.Popen(command,stdin=subprocess.PIPE,stderr=log)
    monitor=Resources(encoder.pid);before=resource.getrusage(resource.RUSAGE_SELF);started=time.monotonic()
    interval=int(1e9/args.hz);next_analysis=0;events=deque();markers=MarkerBuffer(args.hold_ms,args.display_age_ms)
    timings=[];raw_count=confirmed_count=marked_frames=0;tracks=set();frames=0;snapshots=[]
    snapshot_indices={round(t*fps) for t in (5,15,30,45,90,150)}
    records=open(args.output/'detections.jsonl','w');succeeded=False
    try:
        for index in range(limit):
            ok,source=cap.read()
            if not ok:raise RuntimeError(f'Unexpected EOF at frame {index}/{limit}')
            frame=canvas(source);ns=1000000000+round(index/fps*1e9)
            if ns>=next_analysis:
                next_analysis=ns+interval
                start=time.perf_counter()
                found=detector.detect(thumbnail(frame) if args.detector=='nanodet' else frame,args.threshold)
                timings.append((time.perf_counter()-start)*1000);raw_count+=len(found)
                confirmed=tracker.update(found,ns);confirmed_count+=len(confirmed);tracks.update(v.track for v in confirmed)
                events.append((ns+round(args.delay_ms*1e6),ns,confirmed))
                records.write(json.dumps({'time_s':index/fps,'inference_ms':timings[-1],
                    'raw':[{'box':[v.x,v.y,v.w,v.h],'score':v.confidence,'class':v.category} for v in found],
                    'confirmed':[{'id':v.track,'box':[v.x,v.y,v.w,v.h],'score':v.confidence,'vx':v.vx,'vy':v.vy} for v in confirmed]})+'\n')
            while events and events[0][0]<=ns:markers.accept(*events.popleft())
            shown=0
            for captured,v in markers.visible(ns):
                x,y=tracker.anchor(v,ns-captured)
                cx=min(WIDTH-13,max(12,int(x*WIDTH)));tip=min(HEIGHT-1,max(22,int(y*HEIGHT)-4))
                points=np.array([[cx,tip],[cx-10,tip-20],[cx+10,tip-20]],np.int32)
                cv2.fillConvexPoly(frame,points,(255,255,255));cv2.polylines(frame,[points],True,(0,0,0),2);shown+=1
            if shown:marked_frames+=1
            cv2.rectangle(frame,(0,0),(WIDTH,15),(0,0,0),-1)
            cv2.putText(frame,f'{label} | '+('cada fotograma' if args.hz>=fps else f'{args.hz:g} Hz')+f' | OFFLINE | {index/fps:.1f} s',
                        (5,11),cv2.FONT_HERSHEY_SIMPLEX,.34,(255,255,255),1,cv2.LINE_AA)
            if index in snapshot_indices:
                name=f'frame_{index/fps:06.1f}s.jpg';cv2.imwrite(str(args.output/name),frame);snapshots.append(frame.copy())
            encoder.stdin.write(frame.tobytes());frames+=1
            if index and index%450==0:print(f'{index/fps:.0f}/{limit/fps:.1f} s, {marked_frames} frames marked',flush=True)
        encoder.stdin.close();encoder.stdin=None
        if encoder.wait(timeout=60):raise RuntimeError('Encoder failed; see encoder.log')
        succeeded=True
    finally:
        records.close();cap.release();tracker.close()
        if encoder.poll() is None:
            encoder.terminate()
            try:encoder.wait(timeout=5)
            except subprocess.TimeoutExpired:encoder.kill();encoder.wait()
        memory=monitor.stop();log.close()
        if not succeeded:out.unlink(missing_ok=True)
    elapsed=time.monotonic()-started;after=resource.getrusage(resource.RUSAGE_SELF);child=resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu_self=after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime
    cpu_encoder=child.ru_utime+child.ru_stime-child_before.ru_utime-child_before.ru_stime
    if snapshots:
        while len(snapshots)%2:snapshots.append(np.zeros_like(snapshots[0]))
        sheet=np.vstack([np.hstack(snapshots[i:i+2]) for i in range(0,len(snapshots),2)]);cv2.imwrite(str(args.output/'overview.jpg'),sheet)
    result={'source':str(args.video),'source_size':source_size,'source_fps':fps,'source_frames':total,'processed_frames':frames,'video_seconds':frames/fps,
            'output_size':[WIDTH,HEIGHT],'detector':args.detector,'input_size':args.size,'analysis_hz':args.hz,'threshold':args.threshold,
            'start_threshold':args.start_threshold,'group_vehicle_classes':args.group_vehicles,'hold_ms':args.hold_ms,
            'simulated_delivery_delay_ms':args.delay_ms,
            'simulated_display_age_ms':args.display_age_ms,'opencv_threads':args.threads,'panini_applied':False,'wall_seconds':elapsed,'processing_fps':frames/elapsed,
            'analysis_calls':len(timings),'analysis_mean_ms':float(np.mean(timings)),'analysis_p95_ms':float(np.percentile(timings,95)),
            'raw_detection_observations':raw_count,'confirmed_observations':confirmed_count,'track_ids':len(tracks),'frames_with_marker':marked_frames,
            'analysis_cpu_mean_pct_one_core':100*cpu_self/elapsed,'encoder_cpu_mean_pct_one_core':100*cpu_encoder/elapsed,
            'job_cpu_mean_pct_all_cores':100*(cpu_self+cpu_encoder)/elapsed/__import__('os').cpu_count(),'logical_cpus':__import__('os').cpu_count(),
            **memory,'model_sha256':hashlib.sha256((ROOT/'models'/('nanodet_plus_m.bin' if args.detector=='nanodet' else args.detector+'.onnx')).read_bytes()).hexdigest(),
            'versions':{'ncnn':ncnn.__version__,'opencv':cv2.__version__,'numpy':np.__version__},
            'limits':['Offline PC throughput, not Raspberry Pi performance.','Delivery delay is simulated; it is not measured camera latency.',
                      'Counts are detection observations and temporary track IDs, not unique cars or accuracy.','RSS sums can count shared pages twice.']}
    (args.output/'metrics.json').write_text(json.dumps(result,indent=2)+'\n')
    if (args.output/'encoder.log').stat().st_size==0:(args.output/'encoder.log').unlink()
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':
    main()

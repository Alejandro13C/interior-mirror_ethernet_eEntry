#!/usr/bin/env python3
"""Exploratory box evaluation on a small, approximate, visually labelled sample.

Tune thresholds on 'tune' frames; report 'check' frames separately.
This is not COCO mAP or an independent racing validation set.
"""
import argparse
from collections import deque
import importlib.util
import json
from pathlib import Path
import time
import cv2
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('offline_video',ROOT/'tools/analyze-video.py')
video=importlib.util.module_from_spec(spec);spec.loader.exec_module(video)


def match(boxes,found):
    pairs=[]
    for i,box in enumerate(boxes):
        x,y,w,h=box
        for j,v in enumerate(found):
            area=max(0,min(x+w,v.x+v.w)-max(x,v.x))*max(0,min(y+h,v.y+v.h)-max(y,v.y))
            overlap=area/(w*h+v.w*v.h-area)
            if overlap>=.5:pairs.append((overlap,i,j))
    used_truth=set();used_found=set()
    for _,i,j in sorted(pairs,reverse=True):
        if i not in used_truth and j not in used_found:used_truth.add(i);used_found.add(j)
    return len(used_truth),len(found)-len(used_found),len(boxes)-len(used_truth)


def stats(counts):
    tp,fp,fn=map(int,np.sum(counts,axis=0))
    return {'tp':tp,'fp':fp,'fn':fn,'precision':tp/(tp+fp) if tp+fp else 0,
            'recall':tp/(tp+fn) if tp+fn else 0,'f1':2*tp/(2*tp+fp+fn) if tp+fp+fn else 0}


def marker_stats(folder,annotation):
    profile=json.loads((folder/'metrics.json').read_text());fps=profile['source_fps']
    if profile['output_size']!=annotation['canvas']:raise RuntimeError('Annotation/output dimensions differ')
    queue=deque()
    for line in (folder/'detections.jsonl').read_text().splitlines():
        item=json.loads(line);capture=1000000000+round(item['time_s']*1e9);vehicles=[]
        for v in item['confirmed']:
            x,y,w,h=v['box'];vehicles.append(video.Vehicle(x,y,w,h,v['score'],2,v['id'],2,v['vx'],v['vy']))
        queue.append((capture+round(profile['simulated_delivery_delay_ms']*1e6),capture,vehicles))
    markers=video.MarkerBuffer(profile.get('hold_ms',0),profile['simulated_display_age_ms'])
    tracker=video.Tracker();frames={round(item['time_s']*fps):item for item in annotation['frames']}
    counts=[];after_initial=[];per_frame=[]
    try:
        for index in range(max(frames)+1):
            now=1000000000+round(index/fps*1e9)
            while queue and queue[0][0]<=now:markers.accept(*queue.popleft())
            visible=markers.visible(now)
            if index not in frames:continue
            found=[]
            for captured,v in visible:
                projected=video.Vehicle.from_buffer_copy(v)
                x,y=tracker.anchor(v,now-captured);projected.x=x-v.w/2;projected.y=y;found.append(projected)
            boxes=[(x/800,y/480,w/800,h/480) for x,y,w,h in frames[index]['boxes']]
            score=match(boxes,found);counts.append(score)
            if index:after_initial.append(score)
            per_frame.append({'time_s':index/fps,'tp':score[0],'fp':score[1],'fn':score[2]})
    finally:tracker.close()
    return {'all_frames':stats(counts),'after_initial_confirmation':stats(after_initial),'frames':per_frame,
            'limits':'Approximate correlated samples; source t=0 is intentionally unconfirmed. Does not measure whole-video or general accuracy.'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--annotations',type=Path,default=ROOT/'tests/fixtures/texana_boxes.json')
    p.add_argument('--markers',type=Path,help='Evaluate rendered marker positions from an existing run instead of rerunning models')
    a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True);annotation=json.loads(a.annotations.read_text());samples=[]
    if a.markers:
        result=marker_stats(a.markers,annotation)
        (a.output/'marker_evaluation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
        return
    cap=cv2.VideoCapture(str(a.source));cv2.setNumThreads(1)
    for item in annotation['frames']:
        cap.set(cv2.CAP_PROP_POS_MSEC,item['time_s']*1000);ok,frame=cap.read()
        if not ok:raise RuntimeError('Cannot read labelled frame')
        samples.append((item,video.canvas(frame)))
    cap.release();results=[];predictions={}
    for name in ('nanodet256','nanodet416','yolov5n','yolov5s','yolo11n'):
        model=video.Detector(int(name[-3:])) if name.startswith('nanodet') else video.YoloDetector(name)
        observations=[];times=[]
        for item,frame in samples:
            start=time.perf_counter();found=model.detect(video.thumbnail(frame) if name.startswith('nanodet') else frame,.1)
            times.append(1000*(time.perf_counter()-start));observations.append(found)
        predictions[name]=[[{'box':[v.x,v.y,v.w,v.h],'score':v.confidence,'class':v.category} for v in found] for found in observations]
        for threshold in (.15,.2,.25,.3,.4,.5):
            counts={'tune':[],'check':[]}
            for (item,_),found in zip(samples,observations):
                boxes=[(x/800,y/480,w/800,h/480) for x,y,w,h in item['boxes']]
                counts[item['split']].append(match(boxes,[v for v in found if v.confidence>=threshold]))
            results.append({'detector':name,'threshold':threshold,'mean_analysis_ms':float(np.mean(times)),
                            **{k:stats(v) for k,v in counts.items()}})
        best=max([r for r in results if r['detector']==name],key=lambda r:(r['tune']['f1'],r['tune']['recall'],r['threshold']))
        print(json.dumps(best),flush=True)
    result={'source':str(a.source),'annotation':annotation,'iou_threshold':.5,'method':'One-to-one greedy matching in descending IoU, vehicle classes grouped.',
            'limits':'Approximate annotations, one clip, few correlated frames; not general accuracy or exhaustive ground truth.',
            'results':results,'predictions':predictions}
    (a.output/'evaluation.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':main()

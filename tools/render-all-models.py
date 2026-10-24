#!/usr/bin/env python3
"""Render synchronized reference pictures with confirmed triangles only. No MD."""
import argparse,json
from pathlib import Path
import cv2,numpy as np
MODELS=('nanodet256','nanodet416','yolov5n','yolov5s','yolo11n')
NAMES=('01_winchester','02_unity','03_hornet','04_dwarf','05_texana','06_x5m')
p=argparse.ArgumentParser(description=__doc__);p.add_argument('folder',type=Path);a=p.parse_args();root=a.folder
MODELS=tuple(json.loads((root/'manifest.json').read_text())['models'])
reference=json.loads((root/'manual-reference/reference.json').read_text());out=root/'visual-comparisons';out.mkdir(exist_ok=True)
for n,name in enumerate(NAMES):
 if not all((root/name/model/'metrics.json').exists() for model in MODELS):continue
 samples={row['frame_index']:row for row in reference['samples'] if row['clip']==n};predictions={index:{} for index in samples}
 for model in MODELS:
  for index,line in enumerate((root/name/model/'detections.jsonl').open()):
   if index in samples:predictions[index][model]=json.loads(line)['confirmed']
 for index,row in samples.items():
  original=cv2.imread(str(root/'manual-reference'/row['image']));panels=[]
  for model in MODELS+('original',):
   frame=original.copy()
   for b in predictions[index].get(model,[]):
    x,y,w,h=b['box'];cx=min(787,max(12,round((x+w/2)*800)));tip=min(479,max(22,round(y*480)-4));points=np.array([[cx,tip],[cx-10,tip-20],[cx+10,tip-20]],np.int32)
    cv2.fillConvexPoly(frame,points,(255,255,255));cv2.polylines(frame,[points],True,(0,0,0),2)
   cv2.rectangle(frame,(0,0),(800,32),(0,0,0),-1);cv2.putText(frame,f'{model} | {name[3:]} | {row["time_s"]:.2f}s',(8,22),0,.6,(255,255,255),1)
   panels.append(cv2.resize(frame,(640,384)))
  cv2.imwrite(str(out/f'{name}_frame{index:05}_t{row["time_s"]:07.2f}s.jpg'),np.vstack((np.hstack(panels[:3 if len(MODELS)==5 else 2]),np.hstack(panels[3 if len(MODELS)==5 else 2:]))),[cv2.IMWRITE_JPEG_QUALITY,93])
 print(name,len(samples),'comparison pictures',flush=True)

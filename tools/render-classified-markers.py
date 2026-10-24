#!/usr/bin/env python3
"""Render sampled audit decisions using triangles only; colors indicate audit class."""
import argparse,collections,json
from pathlib import Path
import cv2,numpy as np

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('folder',type=Path);a=p.parse_args();root=a.folder;study=root.parent
 rows=json.load(open(root/'classified-markers.json'));models=list(dict.fromkeys(m['model'] for m in rows));ref=json.load(open(study/'manual-reference/reference.json'))
 samples=[s for s in ref['samples'] if not s.get('exclude_from_scoring')];out=root/'classified-samples';out.mkdir(exist_ok=True)
 lookup=collections.defaultdict(list)
 for row in rows:lookup[(row['clip'],row['frame_index'],row['model'])].append(row)
 colors={'correct':(30,220,30),'correct_new':(30,220,30),'duplicate':(255,220,0),'duplicate_new':(255,220,0),'position_error':(0,160,255),'false_positive':(30,30,255),'non_target':(30,30,255),'uncertain':(200,60,200)}
 for sample in samples:
  source=cv2.imread(str(study/'manual-reference'/sample['image']));panels=[]
  for model in models+['original']:
   frame=source.copy()
   for row in lookup[(sample['clip'],sample['frame_index'],model)]:
    x,y,w,h=row['box'];cx=min(787,max(12,round((x+w/2)*800)));tip=min(479,max(22,round(y*480)-4));pts=np.array([[cx,tip],[cx-10,tip-20],[cx+10,tip-20]],np.int32)
    cv2.fillConvexPoly(frame,pts,colors[row['category']]);cv2.polylines(frame,[pts],True,(0,0,0),2)
   cv2.rectangle(frame,(0,0),(800,32),(0,0,0),-1)
   label=f'{model} | {sample["time_s"]:.2f}s | SAMPLE AUDIT'
   cv2.putText(frame,label,(8,22),0,.6,(255,255,255),1);panels.append(cv2.resize(frame,(640,384)))
  mosaic=np.vstack([np.hstack(panels[i:i+3]) for i in (0,3,6)])
  footer=np.zeros((38,mosaic.shape[1],3),np.uint8)
  for text,color,x in [('GREEN: correct',colors['correct'],10),('CYAN: duplicate',colors['duplicate'],340),('ORANGE: position',colors['position_error'],700),('RED: false alarm / non-target',colors['false_positive'],1060),('PURPLE: uncertain',colors['uncertain'],1600)]:cv2.putText(footer,text,(x,26),0,.55,color,1)
  cv2.imwrite(str(out/f'clip{sample["clip"]}_frame{sample["frame_index"]:05}.jpg'),np.vstack([mosaic,footer]),[cv2.IMWRITE_JPEG_QUALITY,93])
 print('Rendered',len(samples),'classified sample mosaics; colors indicate audit categories, not distance or risk.')
if __name__=='__main__':main()

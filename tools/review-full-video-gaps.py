#!/usr/bin/env python3
"""Show representative moments with zero raw detections for visual auditing.
Selection is biased towards gaps: never use these sheets to estimate accuracy.
"""
import argparse,json
from pathlib import Path
import cv2,numpy as np
p=argparse.ArgumentParser(description=__doc__);p.add_argument('folder',type=Path);a=p.parse_args()
for folder in sorted(a.folder.iterdir()):
 if not (folder/'coverage.json').exists():continue
 d=json.loads((folder/'coverage.json').read_text());gaps=d['zero_detection_intervals_s']
 # Review longest absence periods, plus dispersed zero-detection moments.
 longest=sorted(gaps,key=lambda pair:pair[1]-pair[0],reverse=True)[:4]
 scattered=[gaps[round(i*(len(gaps)-1)/3)] for i in range(4)] if gaps else []
 times=sorted(set(round((start+end)/2,3) for start,end in longest+scattered));panels=[];records=[]
 cap=cv2.VideoCapture(d['source'])
 for t in times:
  cap.set(cv2.CAP_PROP_POS_MSEC,t*1000);ok,image=cap.read()
  if not ok:raise RuntimeError('Cannot decode audit frame')
  h,w=image.shape[:2];scale=min(640/w,360/h);new=cv2.resize(image,(round(w*scale),round(h*scale)))
  panel=np.zeros((390,640,3),np.uint8);y=(360-new.shape[0])//2;x=(640-new.shape[1])//2;panel[y:y+new.shape[0],x:x+new.shape[1]]=new
  cv2.putText(panel,f'{folder.name} | {t:.3f}s | zero detections',(5,380),0,.5,(255,255,255),1);panels.append(panel);records.append({'time_s':t,'manual_expected_vehicles':None,'note':'Unreviewed; absence may be correct.'})
 cap.release()
 if panels:
  if len(panels)%2:panels.append(np.zeros_like(panels[0]))
  cv2.imwrite(str(folder/'zero-detection-audit.jpg'),np.vstack([np.hstack(panels[i:i+2]) for i in range(0,len(panels),2)]))
  target=folder/'audit.json'
  if not target.exists():target.write_text(json.dumps({'selection':'Biased zero-detection audit, not accuracy evaluation.','frames':records},indent=2)+'\n')
 print(folder.name,len(times),'audit frames')

import json,cv2,numpy as np
from pathlib import Path
root=Path(__file__).resolve().parents[1]/'comparisons/new-models_all-frames_6-videos';out=root/'marker-audit';sheets=out/'review-sheets';sheets.mkdir(exist_ok=True);groups=json.load(open(out/'groups.json'));rows=json.load(open(out/'markers.json'));look={r['id']:r for r in rows}
for clip in range(6):
 items=sorted([g for g in groups if g['clip']==clip],key=lambda g:(g['frame_index'],g['id']))
 for offset in range(0,len(items),12):
  panels=[]
  for g in items[offset:offset+12]:
   original=cv2.imread(str(root/'manual-reference'/g['image']));im=original.copy();x,y,w,h=g['box'];x,y,w,h=x*800,y*480,w*800,h*480
   cx=round(x+w/2);cy=round(y)
   cv2.rectangle(im,(round(x),round(y)),(round(x+w),round(y+h)),(0,0,255),2)
   cv2.drawMarker(im,(cx,cy),(0,255,255),cv2.MARKER_CROSS,16,2)
   im=cv2.resize(im,(560,336));card=np.full((370,560,3),20,np.uint8);card[34:]=im
   # Crop with context, expanded at least 18px for tiny detections.
   pad=max(18,min(w,h)*.3);x1=max(0,int(x-pad));y1=max(0,int(y-pad));x2=min(800,int(x+w+pad));y2=min(480,int(y+h+pad));crop=original[y1:y2,x1:x2]
   if crop.size:
    scale=min(190/crop.shape[1],130/crop.shape[0]);crop=cv2.resize(crop,(max(1,round(crop.shape[1]*scale)),max(1,round(crop.shape[0]*scale))),interpolation=cv2.INTER_NEAREST)
    ch,cw=crop.shape[:2];card[370-ch:370,560-cw:560]=crop
   models=','.join(sorted({look[i]['model'] for i in g['members']}))
   cv2.putText(card,f'G{g["id"]} C{clip} f{g["frame_index"]} {g["time_s"]:.1f}s',(6,16),0,.48,(255,255,255),1)
   cv2.putText(card,models,(6,30),0,.38,(200,200,200),1);panels.append(card)
  while len(panels)%3:panels.append(np.zeros((370,560,3),np.uint8))
  sheet=np.vstack([np.hstack(panels[i:i+3]) for i in range(0,len(panels),3)])
  cv2.imwrite(str(sheets/f'clip{clip}_page{offset//12:02}.jpg'),sheet,[cv2.IMWRITE_JPEG_QUALITY,94])
print('Sheets',len(list(sheets.glob('*.jpg'))))

import json,math
from pathlib import Path
root=Path(__file__).resolve().parents[1];out=root/'comparisons/new-models_all-frames_6-videos/marker-audit';refroot=root/'comparisons/new-models_all-frames_6-videos/manual-reference'
ref=json.load(open(refroot/'reference.json'));refs={(s['clip'],s['frame_index']):s for s in ref['samples']};names=('01_winchester','02_unity','03_hornet','04_dwarf','05_texana','06_x5m');rows=[];groups=[]
def iou(a,b):
 x,y,w,h=a;xx,yy,ww,hh=b;i=max(0,min(x+w,xx+ww)-max(x,xx))*max(0,min(y+h,yy+hh)-max(y,yy));return i/(w*h+ww*hh-i)
for folder in ['comparisons/old/all-models_all-frames_6-videos','comparisons/new-models_all-frames_6-videos']:
 for model in json.load(open(root/folder/'evaluation.json'))['results']:
  for sample in model['samples']:
   clip=names.index(sample['clip']);ref=refs[(clip,sample['frame_index'])];found=sample['predictions']['confirmed'];pairs=[]
   for t,(x,y,w,h) in enumerate(ref['boxes']):
    for j,b in enumerate(found):
     xx,yy,ww,hh=b['box'];ax=(xx+ww/2)*800;ay=yy*480
     if x-2<=ax<=x+w+2 and abs(ay-y)<=max(4,h*.5):pairs.append((abs(ax-(x+w/2))/w+abs(ay-y)/h,t,j))
   used_t=set();used_p=set();assigned={}
   for _,t,j in sorted(pairs):
    if t not in used_t and j not in used_p:used_t.add(t);used_p.add(j);assigned[j]=('correct',t)
   for _,t,j in sorted(pairs):
    if j not in assigned:assigned[j]=('duplicate',t)
   for j,b in enumerate(found):
    row=dict(id=len(rows),model=model['model'],clip=clip,sample=sample['sample'],frame_index=sample['frame_index'],time_s=sample['time_s'],image=ref['image'],prediction_index=j,box=b['box'],score=b['score'],category=assigned.get(j,('needs_review',None))[0],target=assigned.get(j,(None,None))[1])
    rows.append(row)
    if row['category']!='needs_review':continue
    chosen=None
    for g in groups:
     if g['clip']==clip and g['frame_index']==row['frame_index'] and iou(g['box'],b['box'])>.72:
      chosen=g;break
    if chosen is None:
     chosen=dict(id=len(groups),clip=clip,sample=sample['sample'],frame_index=sample['frame_index'],time_s=sample['time_s'],image=ref['image'],box=b['box'],members=[]);groups.append(chosen)
    chosen['members'].append(row['id']);row['review_group']=chosen['id']
(out/'markers.json').write_text(json.dumps(rows,indent=2)+'\n');(out/'groups.json').write_text(json.dumps(groups,indent=2)+'\n')
print('All markers',len(rows),'unassociated',sum(x['category']=='needs_review' for x in rows),'review clusters',len(groups));print('Clusters per clip',[sum(g['clip']==i for g in groups) for i in range(6)])

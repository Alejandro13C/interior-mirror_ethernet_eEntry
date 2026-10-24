#!/usr/bin/env python3
"""Aggregate all-frame outputs and score a separate exploratory visual reference.
No Markdown is generated. Global detection presence must not be called accuracy.
"""
import argparse,collections,json,math
from pathlib import Path
MODELS=('nanodet256','nanodet416','yolov5n','yolov5s','yolo11n')
NAMES=('01_winchester','02_unity','03_hornet','04_dwarf','05_texana','06_x5m')
def score(truth,found):
 pairs=[]
 for i,(x,y,w,h) in enumerate(truth):
  for j,b in enumerate(found):
   xx,yy,ww,hh=b['box'];ax=(xx+ww/2)*800;ay=yy*480
   if x-2<=ax<=x+w+2 and abs(ay-y)<=max(4,h*.5):pairs.append((abs(ax-(x+w/2))/w+abs(ay-y)/h,i,j))
 used_t=set();used_p=set()
 for _,i,j in sorted(pairs):
  if i not in used_t and j not in used_p:used_t.add(i);used_p.add(j)
 duplicates=len({j for _,i,j in pairs if j not in used_p})
 return {'matched':len(used_t),'missed':len(truth)-len(used_t),'duplicate_anchors':duplicates,'unassociated_predictions_not_verified_false_positives':len(found)-len(used_p)-duplicates}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('folder',type=Path);p.add_argument('--completed-only',action='store_true');a=p.parse_args();root=a.folder
 global MODELS
 MODELS=tuple(json.loads((root/'manifest.json').read_text())['models'])
 available=[n for n,name in enumerate(NAMES) if all((root/name/model/'metrics.json').exists() for model in MODELS)]
 if len(available)!=6 and not a.completed_only:raise RuntimeError('Study not complete')
 reference=json.loads((root/'manual-reference/reference.json').read_text());thresholds=(.15,.25,.4,.5);results=[]
 for model in MODELS:
  sample_results=[];total_frames=raw_present=confirmed_present=0;tracks=set();sources=[]
  for n,name in enumerate(NAMES):
   if n not in available:continue
   folder=root/name/model
   if not (folder/'metrics.json').exists():raise RuntimeError('Incomplete: '+str(folder))
   metrics=json.loads((folder/'metrics.json').read_text());frames={row['frame_index']:row for row in reference['samples'] if row['clip']==n and not row.get('exclude_from_scoring')};count=0
   previous=-1
   for index,line in enumerate((folder/'detections.jsonl').open()):
    row=json.loads(line);count+=1
    if row.get('frame_index',index)!=index or row['time_s']<=previous:raise RuntimeError('Incomplete or unordered records')
    previous=row['time_s'];raw_present+=bool(row['raw']);confirmed_present+=bool(row['confirmed']);tracks.update((name,b['id']) for b in row['confirmed'])
    if index not in frames:continue
    ref=frames.pop(index)
    sample_results.append({'clip':name,'sample':ref['sample'],'frame_index':index,'time_s':row['time_s'],'targets':len(ref['boxes']),'raw':{str(t):score(ref['boxes'],[b for b in row['raw'] if b['score']>=t]) for t in thresholds},'confirmed':score(ref['boxes'],row['confirmed']),'predictions':{'raw':row['raw'],'confirmed':row['confirmed']}})
   if count!=metrics['frames'] or frames:raise RuntimeError('Missing frames/reference samples')
   total_frames+=count;sources.append({'clip':name,'frames':count,'record_file':str(folder/'detections.jsonl')})
  totals={str(t):dict(collections.Counter({k:sum(row['raw'][str(t)][k] for row in sample_results) for k in ('matched','missed','duplicate_anchors','unassociated_predictions_not_verified_false_positives')})) for t in thresholds}
  confirmed={k:sum(row['confirmed'][k] for row in sample_results) for k in ('matched','missed','duplicate_anchors','unassociated_predictions_not_verified_false_positives')}
  result={'model':model,'all_frames':total_frames,'frames_with_raw_detection_not_accuracy':raw_present,'frames_with_confirmed_triangles_not_accuracy':confirmed_present,'temporary_ids_not_unique_cars':len(tracks),'scored_reference_frames':len(sample_results),'reference_targets':sum(row['targets'] for row in sample_results),'raw_reference_scores':totals,'confirmed_reference_scores':confirmed,'samples':sample_results,'sources':sources}
  results.append(result);print(json.dumps({k:v for k,v in result.items() if k not in ('samples','sources')}),flush=True)
 (root/('evaluation.json' if len(available)==6 else 'evaluation.partial.json')).write_text(json.dumps({'results':results,'reference_limits':reference['limits'],'anchor_matching':'one-to-one; horizontal anchor inside visible box ±2px; vertical difference from top <= max(4px, half height). Duplicates are additional anchors fitting labelled targets.','limits':f'{len(available)}/6 clips completed for all models. Only the sampled reference images have exploratory labels; scores are not global accuracy or exhaustive false-positive estimates.'},indent=2)+'\n')
if __name__=='__main__':main()

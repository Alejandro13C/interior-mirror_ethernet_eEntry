#!/usr/bin/env python3
"""Summarize measured study outputs without treating unlabelled boxes as false positives."""
import argparse,importlib.util,json
from pathlib import Path
import cv2,numpy as np
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('evaluation',ROOT/'tools/evaluate-video.py');ev=importlib.util.module_from_spec(spec);spec.loader.exec_module(ev)
p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('--annotations',type=Path,default=ROOT/'tests/fixtures/racing_holdout_boxes.json');a=p.parse_args();folder=a.folder
manifest=json.loads((folder/'samples.json').read_text());labels=json.loads(a.annotations.read_text())
for clip_id,token in labels.get('source_checks',{}).items():
 if token not in Path(manifest['clips'][int(clip_id)]['path']).name:raise RuntimeError('Annotations belong to different source videos; supply matching --annotations')
lookup={s['image']:i for i,s in enumerate(manifest['samples'])};results=[]
for path in sorted(folder.glob('*_*_*t.json')):
 d=json.loads(path.read_text());tp=fn=unmatched=0
 for frame in labels['frames']:
  found=[]
  # Vehicle constructor has box + confidence + category + track + hits + velocity.
  for b in d['predictions'][lookup[frame['image']]]:
   if b['box'][3]*480>=20:found.append(ev.video.Vehicle(*b['box'],b['score'],b['class'],0,0,0,0))
  counts=ev.match([[x/800,y/480,w/800,h/480] for x,y,w,h in frame['boxes']],found)
  tp+=counts[0];unmatched+=counts[1];fn+=counts[2]
 anchor_matches=0
 for frame in labels['frames']:
  truth=frame['boxes'];used=set()
  for b in sorted(d['predictions'][lookup[frame['image']]],key=lambda b:b['score'],reverse=True):
   x,y,w,h=b['box'];ax=(x+w/2)*800;ay=y*480
   candidates=[(abs(ax-(bx+bw/2))/bw+abs(ay-by)/bh,i) for i,(bx,by,bw,bh) in enumerate(truth) if i not in used and bx<=ax<=bx+bw and abs(ay-by)<=bh*.5]
   if candidates:used.add(min(candidates)[1])
  anchor_matches+=len(used)
 d['triangle_target_matches']=anchor_matches
 d['target_matches']=tp;d['target_misses']=fn;d['unmatched_predictions_not_verified_false_positives']=unmatched
 results.append({k:v for k,v in d.items() if k!='predictions'})
(folder/'summary.json').write_text(json.dumps(results,indent=2)+'\n')
lines=['| Engine | Model | Threads | Mean / p95 ms | Serial FPS | CPU total % | Peak RSS MiB | Triangle targets | Box IoU targets |','|---|---|---:|---:|---:|---:|---:|---:|---:|']
for d in results:lines.append(f"| {d['engine']} | {d['model']} | {d['threads']} | {d['mean_ms']:.1f} / {d['p95_ms']:.1f} | {d['serial_fps']:.1f} | {d['cpu_machine_percent']:.1f} | {d['process_peak_rss_mib']:.0f} | {d['triangle_target_matches']}/{d['target_matches']+d['target_misses']} | {d['target_matches']}/{d['target_matches']+d['target_misses']} |")
(folder/'table.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))

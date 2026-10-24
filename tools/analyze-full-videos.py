#!/usr/bin/env python3
"""Process every frame with NanoDet416. Coverage is NOT detection accuracy.
Two parallel jobs reduce runtime; their resource numbers are not isolated benchmarks.
"""
import argparse,concurrent.futures,hashlib,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
NAMES=('01_winchester','02_unity','03_hornet','04_dwarf','05_texana','06_x5m')
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);p.add_argument('--manifest',type=Path,default=ROOT/'comparisons/old/engine-study_6-real-videos/samples.json');a=p.parse_args()
 manifest=json.loads(a.manifest.read_text());clips=manifest['clips'];a.output.mkdir(parents=True,exist_ok=True)
 (a.output/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 if len(clips)!=len(NAMES):raise RuntimeError('Expected the six original study clips')
 environment=dict(os.environ,OMP_WAIT_POLICY='PASSIVE',PYTHONDONTWRITEBYTECODE='1')
 def run(item):
  n,clip=item;path=Path(clip['path']);folder=a.output/NAMES[n];folder.mkdir(exist_ok=True)
  if hashlib.sha256(path.read_bytes()).hexdigest()!=clip['sha256']:raise RuntimeError('Source changed: '+str(path))
  if not (folder/'metrics.json').exists():
   command=[sys.executable,str(ROOT/'tools/analyze-video.py'),str(path),str(folder),'--detector','nanodet','--size','416','--hz','60','--threshold','.15','--start-threshold','.25','--group-vehicles','--hold-ms','0','--threads','1','--delay-ms','0','--display-age-ms','0']
   with (folder/'progress.log').open('w') as log:subprocess.run(command,env=environment,stdout=log,stderr=subprocess.STDOUT,check=True)
  metrics=json.loads((folder/'metrics.json').read_text())
  if metrics['analysis_calls']!=metrics['source_frames'] or metrics['processed_frames']!=metrics['source_frames']:raise RuntimeError('Not all frames were analyzed')
  rows=empty=raw_present=confirmed_present=0;raw_hist={};marker_hist={};empty_runs=[];run_start=None;tracks={}
  for line in (folder/'detections.jsonl').open():
   row=json.loads(line);rows+=1;nraw=len(row['raw']);nc=len(row['confirmed']);raw_hist[nraw]=raw_hist.get(nraw,0)+1;marker_hist[nc]=marker_hist.get(nc,0)+1
   raw_present+=bool(nraw);confirmed_present+=bool(nc)
   if not nraw and run_start is None:run_start=row['time_s']
   if nraw and run_start is not None:empty_runs.append([run_start,row['time_s']]);run_start=None
   for detection in row['confirmed']:
    key=str(detection['id']);track=tracks.setdefault(key,{'first_s':row['time_s'],'last_s':row['time_s'],'observations':0});track['last_s']=row['time_s'];track['observations']+=1
  if run_start is not None:empty_runs.append([run_start,metrics['video_seconds']])
  if rows!=metrics['processed_frames']:raise RuntimeError('Incomplete records')
  result={'name':NAMES[n],'source':str(path),'source_sha256':clip['sha256'],'frames':rows,'frames_with_raw_detection':raw_present,'frames_without_raw_detection':rows-raw_present,'raw_coverage_percent':100*raw_present/rows,'frames_with_confirmed_marker':confirmed_present,'marker_coverage_percent':100*confirmed_present/rows,'raw_count_histogram':raw_hist,'marker_count_histogram':marker_hist,'zero_detection_intervals_s':empty_runs,'temporary_track_count_not_unique_cars':len(tracks),'temporary_tracks':tracks,'limits':['Presence/absence of a detection is not correctness. Empty frames may genuinely contain no cars.','Tracks are not manually labelled identities; no ID accuracy or recall is claimed.','Same exploratory thresholds for all clips; no parameter tuning on outputs.','Concurrent runs: use isolated study for CPU/RAM comparisons.']}
  (folder/'coverage.json').write_text(json.dumps(result,indent=2)+'\n');print('Completed '+NAMES[n]+': '+str(rows)+' frames',flush=True);return result
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,enumerate(clips)))
 summary={'detector':'NanoDet-Plus416','threshold':.15,'start_threshold':.25,'all_frames':sum(r['frames'] for r in results),'clips':[{k:v for k,v in r.items() if k not in ('temporary_tracks','zero_detection_intervals_s')} for r in results]}
 (a.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
 print(json.dumps({'total_frames':summary['all_frames'],'clips':len(results)}),flush=True)
if __name__=='__main__':main()

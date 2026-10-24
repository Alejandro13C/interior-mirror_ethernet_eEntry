#!/usr/bin/env python3
"""Apply explicit visual decisions to sampled markers; export disjoint categories."""
import argparse,collections,csv,hashlib,json
from pathlib import Path

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('folder',type=Path);a=p.parse_args();root=a.folder
 markers=json.load(open(root/'markers.json'));groups=json.load(open(root/'groups.json'));decisions=json.load(open(root/'visual-decisions.json'))
 if {str(g['id']) for g in groups}!=set(decisions):raise RuntimeError('Incomplete or unexpected visual decisions')
 allowed={'correct_new','false_positive','non_target','position_error','uncertain'}
 for g in groups:
  decision=decisions[str(g['id'])]
  if decision['category'] not in allowed or not decision['reason']:raise RuntimeError('Invalid decision')
  for mid in g['members']:
   m=markers[mid]
   if m['id']!=mid or m['category']!='needs_review' or m['review_group']!=g['id']:raise RuntimeError('Invalid group membership')
   m.update(decision);m['classification_basis']='visual review of original scene and candidate crop'
 for m in markers:
  if m['category'] in ('correct','duplicate'):m['classification_basis']='existing sampled reference, original anchor rule; not newly re-annotated'
  if m['category']=='needs_review':raise RuntimeError('Unclassified marker')
 # Additional vehicle observations may be found by more than one proposal in a model.
 objects=collections.defaultdict(list)
 for m in markers:
  if m['category']=='correct_new':objects[(m['model'],m['clip'],m['frame_index'],m['target'])].append(m)
 for members in objects.values():
  for m in sorted(members,key=lambda m:m['score'],reverse=True)[1:]:m['category']='duplicate_new'
 models=list(dict.fromkeys(m['model'] for m in markers));summary=[]
 for model in models:
  rows=[m for m in markers if m['model']==model];c=collections.Counter(m['category'] for m in rows)
  s={'model':model,'sample_triangles':len(rows),'correct_reference':c['correct'],'additional_real_vehicle_triangles':c['correct_new'],'correct_total':c['correct']+c['correct_new'],'duplicate_total':c['duplicate']+c['duplicate_new'],'false_positive_no_following_target':c['false_positive'],'non_target_host_or_service_vehicle':c['non_target'],'false_alarms_total':c['false_positive']+c['non_target'],'position_errors':c['position_error'],'uncertain':c['uncertain']}
  if s['correct_total']+s['duplicate_total']+s['false_alarms_total']+s['position_errors']+s['uncertain']!=len(rows):raise RuntimeError('Category totals disagree')
  summary.append(s)
 def csvwrite(name,rows,fields):
  with (root/name).open('w',newline='') as f:
   w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
 fields=['id','model','clip','sample','frame_index','time_s','image','prediction_index','category','reason','target','score','box','classification_basis']
 csvwrite('all-markers.csv',markers,fields);csvwrite('summary.csv',summary,list(summary[0]))
 for name,cats in [('false-positives',{'false_positive','non_target'}),('position-errors',{'position_error'}),('correct',{'correct','correct_new'}),('duplicates',{'duplicate','duplicate_new'}),('uncertain',{'uncertain'})]:csvwrite(name+'.csv',[m for m in markers if m['category'] in cats],fields)
 data={'sample_frames':69,'models':8,'total_triangles':len(markers),'unassociated_markers_visually_reviewed':sum(len(g['members']) for g in groups),'reviewed_groups':len(groups),'results':summary,'scope':'Only the same 69 scored sample frames, not all 38850 source frames. Original matched/duplicate labels retained; all 555 previously unassociated markers reviewed in 341 pooled visual groups.','definitions':{'correct':'Existing anchor match or visually identified real following vehicle previously absent/mislocated in sparse labels.','duplicate':'Extra anchor fitting an already identified vehicle in same frame/model.','false_positive':'No following target at anchor: signs, photos of cars, fences, sky, background or scene-sized incoherent detection.','non_target':'Visible host-car body or parked/service vehicle off the racing surface; real vehicle but irrelevant alert.','position_error':'Coherent real target or merged targets, triangle displaced/overlarge/wrong body part.','uncertain':'Insufficient visual evidence; excluded from confirmed false-alarm count.'},'limits':'Exploratory assistant visual annotations, not independent certified ground truth. Counts are marker appearances, not distinct cars or false alarms per minute. False-alarm total includes non-target vehicles; pure object-class false positives are not interchangeable with this operational definition. No all-frame precision/recall estimate. Earlier correct/duplicate reference labels were not fully re-annotated by this audit.','input_sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ['markers.json','groups.json','visual-decisions.json']}}
 (root/'classified-markers.json').write_text(json.dumps(markers,indent=2)+'\n');(root/'summary.json').write_text(json.dumps(data,indent=2)+'\n')
 for row in summary:print(json.dumps(row),flush=True)
if __name__=='__main__':main()

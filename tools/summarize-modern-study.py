#!/usr/bin/env python3
"""Join sampled quality and isolated CPU benchmarks into JSON/CSV, never Markdown."""
import argparse,csv,json
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('folder',type=Path);a=p.parse_args();root=a.folder
    old=root.parent/'old';groups=[('previous',old/'all-models_all-frames_6-videos/evaluation.json',old/'engine-study_6-real-videos/summary.json'),('new',root/'evaluation.json',root/'cpu-benchmarks/summary.json')];rows=[]
    for group,quality_path,bench_path in groups:
        quality=json.loads(quality_path.read_text())['results'];bench=json.loads(bench_path.read_text())
        if isinstance(bench,dict):bench=bench['results']
        for q in quality:
            configs=[b for b in bench if b['model']==q['model'] and b.get('available',True)]
            b=min(configs,key=lambda x:x['mean_ms']);s=q['confirmed_reference_scores']
            rows.append({'group':group,'model':q['model'],'correct_triangles_sample':s['matched'],'reference_target_appearances':q['reference_targets'],'duplicate_triangles_sample':s['duplicate_anchors'],'unassociated_sample_not_verified_false_positives':s['unassociated_predictions_not_verified_false_positives'],'cpu_engine':b['engine'],'threads':b['threads'],'mean_detector_ms_pc':round(b['mean_ms'],2),'p95_detector_ms_pc':round(b['p95_ms'],2),'cpu_percent_total_pc':round(b['cpu_machine_percent'],2),'process_peak_rss_mib_pc':round(b['process_peak_rss_mib'],2)})
    audit_path=root/'marker-audit/summary.json'
    if audit_path.exists():
        audit={row['model']:row for row in json.loads(audit_path.read_text())['results']}
        for row in rows:
            reviewed=audit[row['model']]
            row.update({'audited_correct_triangles_sample':reviewed['correct_total'],'audited_duplicates_sample':reviewed['duplicate_total'],'audited_false_alarms_sample':reviewed['false_alarms_total'],'audited_non_target_vehicles_subset':reviewed['non_target_host_or_service_vehicle'],'audited_position_errors_sample':reviewed['position_errors'],'audited_uncertain_sample':reviewed['uncertain']})
    with (root/'comparison.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (root/'comparison.json').write_text(json.dumps({'results':rows,'limits':'Sampled quality only, not 38850-frame accuracy. Original unassociated counts are NOT false positives. Audited columns, when present, use the separate visual audit of those markers; false alarms include irrelevant host/service vehicles, and uncertain cases are separate. Additional real vehicles absent/mislocated in sparse labels mean 105 is not a denominator for audited recall. Benchmarks use fastest measured CPU configuration per model on this PC; previous/new studies run at different times. RAM is full process; time is detector-only, not camera latency or Raspberry Pi.'},indent=2)+'\n')
    for row in rows:print(json.dumps(row),flush=True)

if __name__=='__main__':main()

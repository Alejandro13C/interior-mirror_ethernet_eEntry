#!/usr/bin/env python3
"""Verify complete saved inference streams and synchronized offline videos."""
import argparse,hashlib,json
from pathlib import Path
import cv2

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('folder',type=Path);a=p.parse_args();root=a.folder
    manifest=json.loads((root/'manifest.json').read_text());results=[];total=0
    names=('01_winchester','02_unity','03_hornet','04_dwarf','05_texana','06_x5m')
    for name,source in zip(names,manifest['sources']):
        cap=cv2.VideoCapture(source['path']);expected=int(cap.get(cv2.CAP_PROP_FRAME_COUNT));fps=cap.get(cv2.CAP_PROP_FPS);cap.release()
        if hashlib.sha256(Path(source['path']).read_bytes()).hexdigest()!=source['sha256']:raise RuntimeError('Source changed')
        for model in manifest['models']:
            count=0
            for index,line in enumerate((root/name/model/'detections.jsonl').open()):
                row=json.loads(line);count+=1
                if row['frame_index']!=index or abs(row['time_s']-index/fps)>1e-4:raise RuntimeError('Frame mismatch')
                for b in row['raw']+row['confirmed']:
                    x,y,w,h=b['box']
                    if min(x,y,w,h)<0 or w<=0 or h<=0 or x+w>1.00001 or y+h>1.00001 or not 0<=b['score']<=1:raise RuntimeError('Invalid detection')
            if count!=expected:raise RuntimeError('Incomplete records')
            total+=count
        video=root/'synchronized-videos'/f'{name}.mp4';cap=cv2.VideoCapture(str(video));encoded=int(cap.get(cv2.CAP_PROP_FRAME_COUNT));encoded_fps=cap.get(cv2.CAP_PROP_FPS)
        if encoded!=expected or abs(encoded_fps-fps)>.001:raise RuntimeError('Encoded duration/fps mismatch')
        for index in (0,expected//2,expected-1):
            cap.set(cv2.CAP_PROP_POS_FRAMES,index)
            if not cap.read()[0]:raise RuntimeError('Video decode failed')
        cap.release();results.append({'clip':name,'source_frames':expected,'encoded_frames':encoded,'fps':encoded_fps,'first_middle_last_decode':True})
    if list(root.rglob('*.md')):raise RuntimeError('Unexpected Markdown in new study')
    data={'model_frames_verified':total,'videos':results,'no_markdown_generated':True,'limits':'Checks stream completeness and video integrity, not detection correctness.'}
    (root/'verification.json').write_text(json.dumps(data,indent=2)+'\n');print(json.dumps(data),flush=True)

if __name__=='__main__':main()

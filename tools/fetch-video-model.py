#!/usr/bin/env python3
"""Fetch optional PC comparison models with pinned URLs and SHA-256."""
import argparse
import hashlib
from pathlib import Path
import urllib.request

MODELS={
 'yolov5n':('https://github.com/ultralytics/yolov5/releases/download/v7.0/yolov5n.onnx',
             '04f0e55c26f58d17145b36045780fe1250d5bd2187543e11568e5141d05b3262',3981910),
 'yolov5s':('https://github.com/ultralytics/yolov5/releases/download/v7.0/yolov5s.onnx',
             '8bc25a47abdbe2fd23e30eb948746bf856b031ea3cc815166687f52db9f8c659',14698981),
 'yolo11n':('https://huggingface.co/webnn/yolo11n/resolve/9c5acfdd74aaff2d0f47c51b878506361039a51f/onnx/yolo11n.onnx',
             '7d8fd1717d9d5bbab6986cd134afb620649c7a394303d55b1e09fc00804cc5c1',10720228),
}
p=argparse.ArgumentParser(description=__doc__);p.add_argument('names',nargs='*',choices=list(MODELS));a=p.parse_args()
root=Path(__file__).resolve().parents[1]/'models';root.mkdir(exist_ok=True)
for name in a.names or ['yolov5n']:
    url,sha,size=MODELS[name];path=root/(name+'.onnx')
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==sha:
        print('Verified:',name);continue
    with urllib.request.urlopen(url,timeout=60) as response:data=response.read(size+1)
    if len(data)!=size or hashlib.sha256(data).hexdigest()!=sha:raise SystemExit('Model hash or size mismatch: '+name)
    temporary=path.with_suffix('.tmp');temporary.write_bytes(data);temporary.replace(path)
    print('Downloaded and verified:',name)

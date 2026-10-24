#!/usr/bin/env python3
"""Download pinned PC-study models; validate size and SHA-256 before publishing."""
import argparse,hashlib,urllib.request
from pathlib import Path
MODELS = {'yolo26n': ('https://huggingface.co/prithivMLmods/YOLO26-ONNX/resolve/c526dae7a5a5b4f1d79d704903b4406011da2716/yolo26n/yolo26n.onnx', '00e2d1062178fca312fee35cf5fc9b9c783b3e5c9675d48b84929f69ee391807', 9942005), 'picodet_xs320': ('https://paddledet.bj.bcebos.com/deploy/third_engine/picodet_xs_320_coco_lcnet.onnx', 'e83deca19863b127e702ae340ed7f7455334471886f0251413be459142a08aec', 2859300), 'picodet_s320': ('https://paddledet.bj.bcebos.com/deploy/third_engine/picodet_s_320_coco_lcnet.onnx', 'f9c671308fe7c20e618c7e2b5f88cf7460443cfcb1ef4b628d00d95f0f79758e', 4781217)}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('models',nargs='*');a=p.parse_args()
    root=Path(__file__).resolve().parents[1]/'models';root.mkdir(exist_ok=True)
    for name in a.models or MODELS:
        if name not in MODELS:raise SystemExit('Unknown model: '+name)
        url,sha,size=MODELS[name];target=root/(name+'.onnx')
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()==sha:
            print('Verified',name);continue
        with urllib.request.urlopen(url,timeout=300) as response:data=response.read(size+1)
        if len(data)!=size or hashlib.sha256(data).hexdigest()!=sha:raise RuntimeError('Invalid model: '+name)
        temporary=target.with_suffix('.download');temporary.write_bytes(data);temporary.replace(target)
        print('Downloaded and verified',name)
if __name__=='__main__':main()

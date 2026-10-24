#!/usr/bin/env python3
"""Integration test with a known positive traffic image encoded as 416x250 I420."""
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import tempfile
import time
import argparse

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('image',type=Path)
parser.add_argument('--binary',type=Path)
parser.add_argument('--model',choices=['nanodet','yolov5n'],default='nanodet')
parser.add_argument('--flags',type=int,default=2)
args=parser.parse_args()
pixels=args.image.read_bytes()
assert len(pixels)==156000
root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='rear-camera-detector-check-') as folder:
    env=dict(os.environ,CAMERA_DETECTOR_DIR=folder,CAMERA_DETECTOR_MODEL=args.model)
    minimum=float(env.get('CAMERA_DETECTOR_THRESHOLD','.15' if args.model=='yolov5n' else '.4'))
    worker=subprocess.Popen([str(args.binary or root/'build/vehicle-detector')],
        env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    try:
        assert select.select([worker.stdout],[],[],15)[0], 'Worker did not initialize'
        ready=worker.stdout.readline().strip()
        assert 'ready' in ready,ready
        client=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM)
        client.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,256*1024)
        client.bind(folder+'/video.sock');client.settimeout(5)
        ids=[]
        for sequence in [1,2,3]:
            captured=time.monotonic_ns()-50000000
            header=struct.pack('<IIIIQQII',0x56454831,3,416,250,sequence,captured,args.flags,0)
            client.sendto(header+pixels,folder+'/worker.sock')
            result=client.recv(4096)
            assert len(result)==680
            magic,version,count,reserved,seq,cap,done=struct.unpack_from('<IIIIQQQ',result)
            assert magic==0x56454831 and version==3 and seq==sequence and cap==captured
            if sequence==1:
                assert count==0, 'Unconfirmed single detection was published'
            else:
                assert 1<=count<=16, 'Positive reference image produced no confirmed vehicles'
            assert 0<=done-cap<=750000000, 'Detection exceeded freshness budget'
            tracks=[]
            for i in range(count):
                x,y,w,h,confidence,category,track,hits,vx,vy=struct.unpack_from('<fffffIIIff',result,40+i*40)
                assert category in (2,3,5,7) and track>0 and minimum<=confidence<=1
                assert hits>=2 and abs(vx)<.001 and abs(vy)<.001
                assert x>=0 and y>=0 and w>0 and h>0 and x+w<=1.00001 and y+h<=1.00001
                tracks.append(track)
            if sequence>1: ids.append(tracks)
            print(f'seq={sequence} vehicles={count} capture_to_result_ms={(done-cap)/1e6:.1f} tracks={tracks}')
            time.sleep(.05)
        assert ids[0]==ids[1], 'Identical frames lost track identity'
        client.settimeout(.5)
        stale=struct.pack('<IIIIQQII',0x56454831,3,416,250,4,time.monotonic_ns()-2000000000,args.flags,0)
        client.sendto(stale+pixels,folder+'/worker.sock')
        try:
            client.recv(4096)
            raise AssertionError('Stale input was processed')
        except socket.timeout:
            pass
        client.close()
        print('Real detector IPC: vehicles, coordinates, freshness, tracking and stale rejection passed')
    finally:
        worker.terminate()
        try:worker.wait(timeout=3)
        except subprocess.TimeoutExpired:worker.kill();worker.wait()

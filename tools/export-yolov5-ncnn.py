#!/usr/bin/env python3
"""Export the exact studied YOLOv5n v7.0 weights to three raw NCNN heads."""
import hashlib,json,subprocess,shutil
from pathlib import Path
import onnx,numpy as np
from onnx import numpy_helper as nh,TensorProto as T
root=Path(__file__).resolve().parents[1];source=root/'models/yolov5n.onnx';work=root/'build/yolov5-export';work.mkdir(parents=True,exist_ok=True)
sha=hashlib.sha256(source.read_bytes()).hexdigest()
if sha!='04f0e55c26f58d17145b36045780fe1250d5bd2187543e11568e5141d05b3262':raise RuntimeError('Unexpected YOLOv5n source weights')
m=onnx.load(source)
for idx,t in enumerate(m.graph.initializer):
 if t.data_type==T.FLOAT16:m.graph.initializer[idx].CopyFrom(nh.from_array(nh.to_array(t).astype(np.float32),t.name))
for n in m.graph.node:
 for a in n.attribute:
  if a.type==onnx.AttributeProto.TENSOR and a.t.data_type==T.FLOAT16:a.t.CopyFrom(nh.from_array(nh.to_array(a.t).astype(np.float32),a.t.name))
for v in list(m.graph.input)+list(m.graph.output)+list(m.graph.value_info):
 if v.type.tensor_type.elem_type==T.FLOAT16:v.type.tensor_type.elem_type=T.FLOAT
onnx.save(m,work/'yolov5n-fp32.onnx')
heads=['/model.24/m.%d/Conv_output_0'%i for i in range(3)]
onnx.utils.extract_model(str(work/'yolov5n-fp32.onnx'),str(work/'yolov5n-heads.onnx'),['images'],heads)
pnnx=root/'build/model-tools-python/pnnx/pnnx'
with (work/'conversion.log').open('w') as log:subprocess.run([str(pnnx),str(work/'yolov5n-heads.onnx'),'inputshape=[1,3,640,640]','fp16=0'],stdout=log,stderr=log,check=True)
for suffix in ['param','bin']:shutil.copyfile(work/f'yolov5n_heads.ncnn.{suffix}',root/'models'/f'yolov5n.{suffix}')
param=root/'models/yolov5n.param';lines=param.read_text().splitlines();lowered=[]
for index,line in enumerate(lines[2:]):
 fields=line.split()
 if fields[0]!='Swish':lowered.append(line);continue
 if fields[2:4]!=['1','1']:raise RuntimeError('Unexpected Swish signature')
 before,after=fields[4:6];a,b,s=[f'yolo_silu_{index}_{suffix}' for suffix in ('a','b','s')]
 lowered.extend([f'Split yolo_silu_{index}_split 1 2 {before} {a} {b}',f'Sigmoid yolo_silu_{index}_sigmoid 1 1 {a} {s}',f'BinaryOp yolo_silu_{index}_mul 2 1 {b} {s} {after} 0=2'])
blobs=set()
for line in lowered:
 fields=line.split();bottoms,tops=int(fields[2]),int(fields[3]);blobs.update(fields[4+bottoms:4+bottoms+tops])
param.write_text('7767517\n'+f'{len(lowered)} {len(blobs)}\n'+'\n'.join(lowered)+'\n')
metadata={'source':'https://github.com/ultralytics/yolov5/releases/download/v7.0/yolov5n.onnx','source_sha256':sha,'license':'GPL-3.0','onnx_version':onnx.__version__,'pnnx_wheel':'20261020','pnnx_executable_sha256':hashlib.sha256(pnnx.read_bytes()).hexdigest(),'transformation':'Original FP16 constants promoted losslessly to FP32; extract three pre-sigmoid Conv heads; original decode/NMS implemented in src/yolov5.hpp. No training or quantization.','input':'in0, RGB/255 letterboxed square','outputs':['out0','out1','out2'],'strides':[8,16,32],'anchors':[[10,13,16,30,33,23],[30,61,62,45,59,119],[116,90,156,198,373,326]],'artifacts':{s:hashlib.sha256((root/'models'/f'yolov5n.{s}').read_bytes()).hexdigest() for s in ['param','bin']}}
metadata['silu_lowering']='Swish(x) = x * sigmoid(x), using existing NCNN Split/Sigmoid/BinaryOp layers; weights unchanged.'
(root/'models/yolov5n-ncnn.json').write_text(json.dumps(metadata,indent=2)+'\n');print('Exported exact studied YOLOv5n weights to NCNN.')

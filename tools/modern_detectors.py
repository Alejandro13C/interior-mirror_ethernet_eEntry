"""PC adapters for YOLO26 end-to-end and official PP-PicoDet raw ONNX exports."""
from pathlib import Path
import cv2
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
MODELS=('yolo26n','picodet_xs320','picodet_s320')

class Detector:
    def __init__(self,name,engine='cpu',threads=1,vehicle_type=None):
        self.name=name
        if vehicle_type is None:
            from modern_video_types import Vehicle
            vehicle_type=Vehicle
        self.vehicle_type=vehicle_type
        self.engine=engine
        self.mean=np.array([.485,.456,.406],np.float32);self.std=np.array([.229,.224,.225],np.float32)
        if engine=='openvino':
            import openvino as ov
            self.compiled=ov.Core().compile_model(str(ROOT/'models'/f'{name}.onnx'),'CPU',{'INFERENCE_NUM_THREADS':threads,'NUM_STREAMS':1,'INFERENCE_PRECISION_HINT':'f32','ENABLE_CPU_PINNING':False})
            self.size=int(self.compiled.input(0).shape[-1]);return
        if engine=='opencv':
            cv2.setNumThreads(threads)
            self.net=cv2.dnn.readNetFromONNX(str(ROOT/'models'/f'{name}.onnx'))
            self.size=640 if name=='yolo26n' else 320;return
        import onnxruntime as ort
        opts=ort.SessionOptions();opts.intra_op_num_threads=threads;opts.inter_op_num_threads=1
        opts.log_severity_level=3
        opts.add_session_config_entry('session.intra_op.allow_spinning','0')
        providers=['CPUExecutionProvider']
        if engine=='cuda':
            ort.preload_dlls(directory='')
            providers=[('CUDAExecutionProvider',{'cudnn_conv_algo_search':'HEURISTIC','gpu_mem_limit':1073741824,'use_tf32':0}),'CPUExecutionProvider']
        self.session=ort.InferenceSession(str(ROOT/'models'/f'{name}.onnx'),opts,providers=providers)
        if engine=='cuda':
            if self.session.get_providers()[0]!='CUDAExecutionProvider':raise RuntimeError('CUDA unavailable')
            self.session.disable_fallback()
        inp=self.session.get_inputs()[0];self.input_name=inp.name;self.size=inp.shape[-1]
        if len(self.session.get_inputs())!=1:raise RuntimeError('Unexpected input signature')

    def blob(self,image):
        h,w=image.shape[:2]
        if self.name=='yolo26n':
            scale=self.size/max(h,w);nw,nh=round(w*scale),round(h*scale)
            self.left,self.top=(self.size-nw)//2,(self.size-nh)//2;self.scale=scale
            im=cv2.copyMakeBorder(cv2.resize(image,(nw,nh)),self.top,self.size-nh-self.top,self.left,self.size-nw-self.left,cv2.BORDER_CONSTANT,value=(114,114,114))
            return cv2.dnn.blobFromImage(im,1/255.,swapRB=True)
        # Official TestReader: RGB, cubic resize without aspect preservation,
        # ImageNet normalization; output coordinates are restored independently.
        im=cv2.resize(image,(self.size,self.size),interpolation=cv2.INTER_CUBIC)[:,:,::-1].astype(np.float32)/255.
        return np.ascontiguousarray(((im-self.mean)/self.std).transpose(2,0,1)[None])

    def detect(self,image,threshold=.15):
        Vehicle=self.vehicle_type
        h,w=image.shape[:2];blob=self.blob(image)
        if self.engine=='openvino':
            result=self.compiled([blob]);outputs=[result[output] for output in self.compiled.outputs]
        elif self.engine=='opencv':
            self.net.setInput(blob);outputs=self.net.forward(self.net.getUnconnectedOutLayersNames())
        else:outputs=self.session.run(None,{self.input_name:blob})
        if self.name=='yolo26n':
            rows=outputs[0][0]
            if rows.shape!=(300,6):raise RuntimeError('Unexpected YOLO26 output')
            boxes=rows[:,:4].astype(np.float32).copy();scores=rows[:,4];labels=rows[:,5].astype(int)
            boxes[:,[0,2]]=(boxes[:,[0,2]]-self.left)/self.scale/w
            boxes[:,[1,3]]=(boxes[:,[1,3]]-self.top)/self.scale/h
        else:
            if len(outputs)!=8:raise RuntimeError('Unexpected PicoDet output')
            decoded=[];score_parts=[]
            for level,stride in enumerate((8,16,32,64)):
                score=outputs[level][0];reg=outputs[level+4][0].reshape(-1,4,8)
                side=self.size//stride
                if score.shape!=(side*side,80):raise RuntimeError('Unexpected feature shape')
                exp=np.exp(reg-reg.max(axis=2,keepdims=True));dist=(exp/exp.sum(axis=2,keepdims=True)*np.arange(8)).sum(axis=2)*stride
                yy,xx=np.mgrid[:side,:side];centers=np.column_stack((xx.ravel()+.5,yy.ravel()+.5))*stride
                decoded.append(np.column_stack((centers[:,0]-dist[:,0],centers[:,1]-dist[:,1],centers[:,0]+dist[:,2],centers[:,1]+dist[:,3]))/self.size)
                score_parts.append(score)
            boxes=np.concatenate(decoded);all_scores=np.concatenate(score_parts)
            labels=all_scores.argmax(axis=1);scores=all_scores[np.arange(len(labels)),labels]
        selected=np.flatnonzero(np.isin(labels,[2,3,5,7]) & (scores>=threshold))
        boxes=np.clip(boxes,0.,1.);selected=[i for i in selected if boxes[i,2]>boxes[i,0] and boxes[i,3]>boxes[i,1]]
        selected=sorted(selected,key=lambda i:float(scores[i]),reverse=True)
        if self.name!='yolo26n' and selected:
            # Class-agnostic vehicle NMS, same policy as previous comparison.
            xywh=[[float(boxes[i,0]),float(boxes[i,1]),float(boxes[i,2]-boxes[i,0]),float(boxes[i,3]-boxes[i,1])] for i in selected]
            kept=np.asarray(cv2.dnn.NMSBoxes(xywh,[float(scores[i]) for i in selected],threshold,.45)).reshape(-1)
            selected=[selected[i] for i in kept]
        result=[]
        for i in selected[:16]:
            x,y,x2,y2=boxes[i];result.append(Vehicle(x,y,x2-x,y2-y,float(scores[i]),int(labels[i]),0,0,0,0))
        return result

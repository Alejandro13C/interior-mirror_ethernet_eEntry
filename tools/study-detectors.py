#!/usr/bin/env python3
"""CPU study on real clips. Each configuration runs in an isolated process.
No decoding/encoding in timed region; timings include preprocessing and NMS.
"""
import argparse, hashlib, importlib.util, json, os, platform, resource, subprocess, sys, time
os.environ.setdefault("OMP_WAIT_POLICY","PASSIVE")
from pathlib import Path
import cv2
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('video',ROOT/'tools/analyze-video.py')
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)

class Backend:
    def __init__(self,engine,path,threads,pinning=True):
        self.engine=engine
        if engine=='ort':
            import onnxruntime as ort
            opts=ort.SessionOptions();opts.intra_op_num_threads=threads;opts.inter_op_num_threads=1
            opts.add_session_config_entry('session.intra_op.allow_spinning','0')
            self.session=ort.InferenceSession(str(path),opts,providers=['CPUExecutionProvider'])
            self.name=self.session.get_inputs()[0].name
            self.dtype=np.float16 if self.session.get_inputs()[0].type=='tensor(float16)' else np.float32
        else:
            import openvino as ov
            self.session=ov.Core().compile_model(str(path),'CPU',{'INFERENCE_NUM_THREADS':threads,'NUM_STREAMS':1,'INFERENCE_PRECISION_HINT':'f32','ENABLE_CPU_PINNING':pinning})
            self.dtype=np.float16 if self.session.input(0).element_type.to_string()=='f16' else np.float32
    def setInput(self,blob):self.blob=blob.astype(self.dtype,copy=False)
    def forward(self):
        if self.engine=='ort':return self.session.run(None,{self.name:self.blob})[0]
        return self.session([self.blob])[self.session.output(0)]

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path)
    p.add_argument('--videos-dir',type=Path,default=Path('/home/alejandro/Downloads'));p.add_argument('--worker',nargs=3,metavar=('ENGINE','MODEL','THREADS'));a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True)
    if not a.worker:
        if (a.output/'samples.json').exists():raise RuntimeError('Choose a new output folder to preserve the previous study')
        samples=[];manifest=[]
        for n,path in enumerate(sorted(a.videos_dir.glob('*.mp4'))):
            cap=cv2.VideoCapture(str(path));fps=cap.get(cv2.CAP_PROP_FPS);duration=cap.get(cv2.CAP_PROP_FRAME_COUNT)/fps
            manifest.append({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'duration_s':duration,'fps':fps})
            for i,fraction in enumerate((.05,.2,.4,.6,.8)):
                cap.set(cv2.CAP_PROP_POS_MSEC,duration*fraction*1000);ok,frame=cap.read()
                if not ok:raise RuntimeError(str(path))
                name=f'clip{n}_{i}.png';cv2.imwrite(str(a.output/name),v.canvas(frame))
                samples.append({'image':name,'source':str(path),'time_s':duration*fraction})
            cap.release()
        (a.output/'samples.json').write_text(json.dumps({'clips':manifest,'samples':samples,'machine':{'platform':platform.platform(),'cpu':Path('/proc/cpuinfo').read_text().split('model name')[1].split('\n')[0].strip(' :\t'),'logical_cpus':os.cpu_count(),'memory':Path('/proc/meminfo').read_text().splitlines()[0]}},indent=2))
        configs=[('ncnn','nanodet256','1'),('ncnn','nanodet416','1')]+[(e,m,str(t)) for e in ('opencv','ort','openvino') for m in ('yolov5n','yolov5s','yolo11n') for t in (1,4)]
        for config in configs:
            subprocess.run([sys.executable,str(Path(__file__).resolve()),str(a.output),'--worker',*config],check=True)
        return
    engine,name,threads=a.worker;threads=int(threads);cv2.setNumThreads(threads)
    images=[cv2.imread(str(a.output/s['image'])) for s in json.loads((a.output/'samples.json').read_text())['samples']]
    if engine=='ncnn':model=v.Detector(int(name[-3:]));images=[v.thumbnail(im) for im in images]
    elif engine=='opencv':model=v.YoloDetector(name)
    else:
        model=v.YoloDetector.__new__(v.YoloDetector);model.name=name;model.net=Backend(engine,ROOT/'models'/f'{name}.onnx',threads)
    for _ in range(5):model.detect(images[0],.15)
    timings=[];predictions=[];start=time.perf_counter();cpu=time.process_time()
    for image in images:
        for repeat in range(3):
            before=time.perf_counter();found=model.detect(image,.15);timings.append((time.perf_counter()-before)*1000)
        predictions.append([{'box':[b.x,b.y,b.w,b.h],'score':b.confidence,'class':b.category} for b in found])
    elapsed=time.perf_counter()-start;cpu=time.process_time()-cpu
    key=f'{engine}_{name}_{threads}t'
    result={'engine':engine,'model':name,'threads':threads,'samples':len(images),'warmup':5,'repeats':3,'mean_ms':float(np.mean(timings)),'p95_ms':float(np.percentile(timings,95)),'serial_fps':1000/float(np.mean(timings)),'cpu_one_core_percent':100*cpu/elapsed,'cpu_machine_percent':100*cpu/elapsed/os.cpu_count(),'process_peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,'predictions':predictions,'python':platform.python_version(),'opencv':cv2.__version__}
    if engine=='ort':
        import onnxruntime;result['engine_version']=onnxruntime.__version__
    elif engine=='openvino':
        import openvino;result['engine_version']=openvino.__version__
    elif engine=='ncnn':
        import ncnn;result['engine_version']=ncnn.__version__
    else:result['engine_version']=cv2.__version__
    (a.output/f'{key}.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:val for k,val in result.items() if k!='predictions'}),flush=True)
if __name__=='__main__':main()

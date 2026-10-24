#!/usr/bin/env python3
"""Isolated PC CPU benchmarks, one subprocess per model/thread configuration."""
import argparse,hashlib,json,os,platform,resource,subprocess,sys,time
from pathlib import Path
os.environ.setdefault('OMP_WAIT_POLICY','PASSIVE')
import cv2,numpy as np
from modern_detectors import Detector,MODELS,ROOT

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('folder',type=Path)
    p.add_argument('--worker',nargs=3);a=p.parse_args();out=a.folder/'cpu-benchmarks';out.mkdir(exist_ok=True)
    source=ROOT/'comparisons/old/engine-study_6-real-videos'
    samples=json.loads((source/'samples.json').read_text())
    if not a.worker:
        for model in MODELS:
            for engine in ('cpu','openvino','opencv'):
                for threads in (1,4):
                    subprocess.run([sys.executable,__file__,str(a.folder),'--worker',model,engine,str(threads)],check=True)
        results=[json.loads((out/f'{model}_{engine}_{threads}t.json').read_text()) for model in MODELS for engine in ('cpu','openvino','opencv') for threads in (1,4)]
        (out/'summary.json').write_text(json.dumps({'results':results,'sample_source':str(source/'samples.json'),
            'machine':{'cpu':Path('/proc/cpuinfo').read_text().split('model name')[1].split('\n')[0].strip(' :\t'),'logical_cpus':os.cpu_count(),'platform':platform.platform()},
            'limits':'PC CPU only, isolated jobs; no encoding/decoding in timed region. Includes preprocessing, inference and postprocessing. RSS includes Python and runtime. Not Raspberry Pi or end-to-end camera latency.'},indent=2)+'\n')
        return
    name,engine,threads=a.worker;threads=int(threads);cv2.setNumThreads(1)
    target=out/f'{name}_{engine}_{threads}t.json'
    images=[cv2.imread(str(source/row['image'])) for row in samples['samples']]
    if any(im is None for im in images):raise RuntimeError('Missing benchmark input')
    try:
        model=Detector(name,engine,threads)
        for _ in range(5):model.detect(images[0],.15)
    except Exception as error:
        target.write_text(json.dumps({'model':name,'engine':engine,'threads':threads,'available':False,'error':str(error)},indent=2)+'\n')
        print(name,engine,threads,'unavailable:',str(error)[:160],flush=True);return
    timings=[];start=time.perf_counter();cpu=time.process_time()
    for image in images:
        for _ in range(3):
            before=time.perf_counter();model.detect(image,.15);timings.append((time.perf_counter()-before)*1000)
    elapsed=time.perf_counter()-start;cpu=time.process_time()-cpu
    result={'model':name,'engine':'onnxruntime-cpu' if engine=='cpu' else engine,'available':True,'threads':threads,'samples':len(images),'repeats':3,'warmup':5,
        'mean_ms':float(np.mean(timings)),'p95_ms':float(np.percentile(timings,95)),'serial_fps':1000/float(np.mean(timings)),
        'cpu_one_core_percent':100*cpu/elapsed,'cpu_machine_percent':100*cpu/elapsed/os.cpu_count(),
        'process_peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
        'model_sha256':hashlib.sha256((ROOT/'models'/f'{name}.onnx').read_bytes()).hexdigest()}
    if engine=='openvino':
        import openvino;result['engine_version']=openvino.__version__
    elif engine=='opencv':result['engine_version']=cv2.__version__
    else:
        import onnxruntime;result['engine_version']=onnxruntime.__version__
    target.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)

if __name__=='__main__':main()

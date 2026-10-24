"""Optional PC-only ONNX Runtime CUDA adapter. No Pi or system-driver changes."""
import numpy as np
class CudaBackend:
    def __init__(self,path):
        import onnxruntime as ort
        ort.preload_dlls(directory='')
        options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(path),options,providers=[('CUDAExecutionProvider',{'cudnn_conv_algo_search':'HEURISTIC','gpu_mem_limit':1073741824,'use_tf32':0}),'CPUExecutionProvider'])
        if self.session.get_providers()[0]!='CUDAExecutionProvider':raise RuntimeError('CUDA unavailable; refusing silent CPU fallback')
        self.session.disable_fallback();inp=self.session.get_inputs()[0];self.name=inp.name
        self.dtype=np.float16 if inp.type=='tensor(float16)' else np.float32
    def setInput(self,blob):self.blob=blob.astype(self.dtype,copy=False)
    def forward(self):return self.session.run(None,{self.name:self.blob})[0]

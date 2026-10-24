#!/usr/bin/env python3
"""Build pinned NCNN with layers required by NanoDet and YOLOv5n."""
from pathlib import Path
import hashlib
import platform
import re
import subprocess
import tarfile
import urllib.request

REV = 'c4193aadbbb56582aa87b1850dd3d98fb8fd936d'  # NCNN 20250916
SHA = '6005bbdefb83ad6da63b2fa200d04c59882d09c9c62e977aa6325a0a6a4822ab'
root = Path(__file__).resolve().parents[1]
build = root / 'build'
build.mkdir(exist_ok=True)
archive = build / 'ncnn-source.tar.gz'
if not archive.exists():
    with urllib.request.urlopen(f'https://codeload.github.com/Tencent/ncnn/tar.gz/{REV}', timeout=60) as response:
        archive.write_bytes(response.read(20000000))
if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA:
    raise SystemExit('NCNN archive SHA-256 mismatch')
source = build / 'ncnn-source' / f'ncnn-{REV}'
if not source.exists():
    source.parent.mkdir(exist_ok=True)
    with tarfile.open(archive) as tar:
        tar.extractall(source.parent, filter='data')
needed = {'binaryop','concat','convolution','convolutiondepthwise','input','interp',
          'permute','pooling','reshape','shufflechannel','sigmoid','slice','split',
          'relu','clip','innerproduct','flatten','packing','cast','padding','softmax','scale','bias','crop'}
classes = re.findall(r'ncnn_add_layer\((\w+)', (source/'src/CMakeLists.txt').read_text())
cache = build / 'ncnn-layers.cmake'
cache.write_text('\n'.join(f'set(WITH_LAYER_{c.lower()} {"ON" if c.lower() in needed else "OFF"} CACHE BOOL "" FORCE)' for c in classes)+'\n')
command = ['cmake','-S',str(source),'-B',str(build/'ncnn'),'-C',str(cache),
           '-DCMAKE_BUILD_TYPE=Release',f'-DCMAKE_INSTALL_PREFIX={build/"ncnn-install"}',
           '-DNCNN_VULKAN=OFF','-DNCNN_BUILD_TOOLS=OFF','-DNCNN_BUILD_EXAMPLES=OFF',
           '-DNCNN_BUILD_BENCHMARK=OFF','-DNCNN_BUILD_TESTS=OFF','-DNCNN_RUNTIME_CPU=OFF',
           '-DNCNN_INT8=OFF','-DNCNN_BF16=OFF','-DNCNN_C_API=OFF']
if platform.machine().startswith('armv7'):
    command += ['-DCMAKE_CXX_FLAGS=-O3 -march=armv7-a -mfpu=neon-vfpv4 -mfloat-abi=hard']
subprocess.run(command,check=True)
# Keep memory use bounded on the 512 MB Pi; normal video keeps running.
subprocess.run(['cmake','--build',str(build/'ncnn'),'--parallel','1'],check=True)
subprocess.run(['cmake','--install',str(build/'ncnn')],check=True)
print('NCNN installed in',build/'ncnn-install')

#!/usr/bin/env python3
"""Build pinned NCNN with layers required by NanoDet and YOLOv5n.

Usage: build-ncnn.py [name]  (default ncnn: build/<name> and build/<name>-install)
"""
from pathlib import Path
import sys
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
needed = {'swish','binaryop','concat','convolution','convolutiondepthwise','input','interp',
          'permute','pooling','reshape','shufflechannel','sigmoid','slice','split',
          'relu','clip','innerproduct','flatten','packing','cast','padding','softmax','scale','bias','crop'}
classes = re.findall(r'ncnn_add_layer\((\w+)', (source/'src/CMakeLists.txt').read_text())
name = sys.argv[1] if len(sys.argv) > 1 else 'ncnn'
cache = build / f'{name}-layers.cmake'
cache.write_text('\n'.join(f'set(WITH_LAYER_{c.lower()} {"ON" if c.lower() in needed else "OFF"} CACHE BOOL "" FORCE)' for c in classes)+'\n')
command = ['cmake','-S',str(source),'-B',str(build/name),'-C',str(cache),
           '-DCMAKE_BUILD_TYPE=Release',f'-DCMAKE_INSTALL_PREFIX={build/(name+"-install")}',
           '-DNCNN_VULKAN=OFF','-DNCNN_BUILD_TOOLS=OFF','-DNCNN_BUILD_EXAMPLES=OFF',
           '-DNCNN_BUILD_BENCHMARK=OFF','-DNCNN_BUILD_TESTS=OFF','-DNCNN_RUNTIME_CPU=OFF',
           '-DNCNN_INT8=OFF','-DNCNN_BF16=OFF','-DNCNN_C_API=OFF']
# The compiler target decides: a 64-bit kernel reports aarch64 under 32-bit Raspbian.
target = subprocess.run(['cc','-dumpmachine'],capture_output=True,text=True).stdout
if target.startswith('arm-') or platform.machine().startswith('armv7'):
    # Pi 3: Cortex-A53 running 32-bit code (ARMv8 NEON with FMA).
    command += ['-DCMAKE_CXX_FLAGS=-O3 -mcpu=cortex-a53 -mfpu=neon-fp-armv8 -mfloat-abi=hard']
subprocess.run(command,check=True)
# Keep memory use bounded on the 512 MB Pi; normal video keeps running.
subprocess.run(['cmake','--build',str(build/name),'--parallel','1'],check=True)
subprocess.run(['cmake','--install',str(build/name)],check=True)
print('NCNN installed in',build/(name+'-install'))

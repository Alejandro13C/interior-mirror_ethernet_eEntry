#!/usr/bin/env python3
"""Fetch official NanoDet-Plus NCNN weights and verify each file's SHA-256."""
from pathlib import Path
import hashlib
import urllib.request
import io
import zipfile
URL = 'https://github.com/RangiLyu/nanodet/releases/download/v1.0.0-alpha-1/nanodet-plus-m_416_ncnn.zip'
FILES = {
 'param': ('d79e18ecd8595081bb29fdf0c790a47fcea96fbf58598350569a92e71e915b72',23123),
 'bin': ('f7555ca6cb9650ee41e2f0a53fdf46b6e187a2a09792208d8c3eec033b6ac615',2397184),
}
root=Path(__file__).resolve().parents[1]/'models'
root.mkdir(exist_ok=True)
archive=None
for extension,(sha,size) in FILES.items():
    name='nanodet_plus_m.'+extension
    path=root/name
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==sha:
        print('Verified:',name);continue
    if archive is None:
        with urllib.request.urlopen(URL,timeout=60) as response:packed=response.read(8000001)
        if len(packed)>8000000:raise SystemExit('Model archive too large')
        archive=zipfile.ZipFile(io.BytesIO(packed))
    entry=archive.getinfo('nanodet-plus-m_416.'+extension)
    if entry.file_size!=size:raise SystemExit('Unexpected model size')
    data=archive.read(entry)
    if len(data)!=size or hashlib.sha256(data).hexdigest()!=sha:raise SystemExit('Model hash mismatch: '+name)
    temporary=path.with_suffix('.tmp');temporary.write_bytes(data);temporary.replace(path)
    print('Downloaded and verified:',name)

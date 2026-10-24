#!/usr/bin/env python3
"""Verify complete offline outputs and report coverage without claiming accuracy."""
import argparse,json
from pathlib import Path
import cv2
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('folder',type=Path);p.add_argument('--report',type=Path,default=ROOT/'full-video-results.md');a=p.parse_args()
summary=json.loads((a.folder/'summary.json').read_text());rows=[]
for clip in summary['clips']:
 folder=a.folder/clip['name'];m=json.loads((folder/'metrics.json').read_text());cap=cv2.VideoCapture(str(folder/'annotated.mp4'));n=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
 if not n==m['analysis_calls']==m['processed_frames']==m['source_frames']==clip['frames']:raise RuntimeError('Incomplete analysis or encoded output')
 for index in (0,n//2,n-1):
  cap.set(cv2.CAP_PROP_POS_FRAMES,index)
  if not cap.read()[0]:raise RuntimeError('Cannot decode output frame')
 cap.release();thresholds={str(t):{'frames_with_detection':0,'observations':0} for t in (.15,.25,.4,.5)};count=0;previous=-1
 for line in (folder/'detections.jsonl').open():
  record=json.loads(line);count+=1
  if record['time_s']<=previous:raise RuntimeError('Nonmonotonic records')
  previous=record['time_s']
  for threshold,row in thresholds.items():
   detections=sum(box['score']>=float(threshold) for box in record['raw']);row['frames_with_detection']+=bool(detections);row['observations']+=detections
 if count!=n:raise RuntimeError('Missing per-frame detections')
 (folder/'threshold-coverage.json').write_text(json.dumps({'thresholds':thresholds,'limits':'Post-filtering existing raw detections, not a new tracker run or an accuracy measure.'},indent=2)+'\n')
 try:link=(folder/'annotated.mp4').resolve().relative_to(a.report.resolve().parent)
 except ValueError:link=(folder/'annotated.mp4').resolve()
 rows.append(f"| {clip['name'][3:]} | {n:,} | {clip['frames_without_raw_detection']:,} | {clip['frames_with_confirmed_marker']:,} | [Abrir vídeo]({link}) |")
 total=sum(c['frames'] for c in summary['clips'])
 if total!=summary['all_frames']:raise RuntimeError('Summary mismatch')
header=f'''# NanoDet416: vídeos completos

**{total:,} fotogramas analizados de seis vídeos**, sin saltos. Salida 800×480 con triángulos blancos, sin cajas visibles; originales conservados. Se verifican registros por fotograma, número de imágenes codificadas y decodificación al inicio, mitad y final.

Perfil exploratorio: umbral 0,15, alta de pista 0,25, confirmación en dos observaciones, mismo asociador C y sin retardo artificial. Es el perfil de la comparación de modelos; la Pi conserva entrada 256 y umbral 0,40. El límite solicitado de 60 Hz que aparece en el rótulo permite analizar cada imagen del original, de ~30 fps: no es una medición de 60 fps ni de rendimiento en la Raspberry.

| Vídeo | Fotogramas analizados | Sin detección bruta | Con triángulo confirmado | Resultado |
|---|---:|---:|---:|---|
'''
footer='''
**Estas columnas no miden la tasa de acierto.** Un fotograma sin detección puede estar vacío o contener un coche omitido; uno con triángulo puede tener aciertos, duplicados o falsos avisos. Tampoco indican si se han encontrado todos los coches presentes. Para precisión/recall y continuidad por vehículo hace falta anotar los vehículos reales y sus identidades.

La revisión visual ya confirma:

- Unity, ~30,4 s: coche visible a contraluz sin detección. ~90 s: varios triángulos sobre un mismo coche azul.
- Hornet, ~126,9 s: varios coches visibles sin detecciones.
- Dwarf, ~67,7 s: coche muy cercano sin detección.
- X5M, ~5 s: marcadores sobre zonas ajenas al coche que sigue.
- Hay también ausencias correctas: escenas vacías y cartelas del vídeo original.

El 8/8 anterior solo describía ocho coches de la pequeña muestra. **NanoDet416 no alcanza un 100 % de detección en estos vídeos y presenta fallos relevantes**, incluso con coches cercanos. No se calcula un porcentaje global sin referencia anotada ni se elige como detector definitivo.

En cada carpeta: `detections.jsonl` (resultado de cada imagen), `metrics.json`, `coverage.json`, captura resumen y auditoría de intervalos sin detecciones. `threshold-coverage.json` compara presencia de detecciones al filtrar scores 0,15/0,25/0,40/0,50, sin repetir inferencia ni presentarla como precisión. Auditorías seleccionadas para buscar fallos: no son una muestra aleatoria para estimar exactitud.

Los dos trabajos en paralelo incluyen codificación; sus consumos no sustituyen al benchmark aislado del [estudio de motores](detector-study.md). Resultados completos en `comparisons/nanodet416_all-frames_6-videos/`; hallazgos en `review-findings.json`.
'''
a.report.write_text(header+'\n'.join(rows)+'\n'+footer);print(a.report);print('Verified',total,'frames across',len(rows),'videos')

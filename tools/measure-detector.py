#!/usr/bin/env python3
"""Measure live camera/detector process CPU and RAM on the Raspberry Pi."""
import argparse,json,os,subprocess,time
from pathlib import Path

def pid(service):
 return int(subprocess.check_output(['systemctl','show',service,'--property=MainPID','--value'],text=True).strip())
def proc(n):
 ticks=Path(f'/proc/{n}/stat').read_text().rsplit(')',1)[1].split();status=dict(x.split(':',1) for x in Path(f'/proc/{n}/status').read_text().splitlines() if ':' in x)
 return {'ticks':int(ticks[11])+int(ticks[12]),'rss_mib':int(status['VmRSS'].split()[0])/1024,'peak_rss_mib':int(status['VmHWM'].split()[0])/1024}
def system():
 numbers=list(map(int,Path('/proc/stat').read_text().splitlines()[0].split()[1:9]));return sum(numbers),numbers[3]+numbers[4]
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seconds',type=int,default=30);p.add_argument('--output',type=Path,required=True);p.add_argument('--services',nargs='+',choices=['rear-camera','rear-camera-detector'],default=['rear-camera','rear-camera-detector']);a=p.parse_args()
 if not 1<=a.seconds<=300:raise RuntimeError('seconds must be 1..300')
 services=list(dict.fromkeys(a.services));pids={s:pid(s) for s in services};first={s:proc(n) for s,n in pids.items()};total,idle=system();start=time.monotonic();samples=[]
 while time.monotonic()-start<a.seconds:
  time.sleep(max(0,min(2,a.seconds-(time.monotonic()-start))))
  current={s:proc(n) for s,n in pids.items()};memory=dict(x.split(':',1) for x in Path('/proc/meminfo').read_text().splitlines());samples.append({'elapsed_s':time.monotonic()-start,'processes':current,'available_mib':int(memory['MemAvailable'].split()[0])/1024,'temperature_c':int(Path('/sys/class/thermal/thermal_zone0/temp').read_text())/1000})
 elapsed=time.monotonic()-start;end_total,end_idle=system();hz=os.sysconf('SC_CLK_TCK');results={}
 for s in services:
  if pid(s)!=pids[s]:raise RuntimeError('Service restarted during measurement: '+s)
  results[s]={'pid':pids[s],'cpu_one_core_percent':100*(samples[-1]['processes'][s]['ticks']-first[s]['ticks'])/hz/elapsed,'cpu_total_machine_percent':100*(samples[-1]['processes'][s]['ticks']-first[s]['ticks'])/hz/elapsed/os.cpu_count(),'rss_peak_mib':max(x['processes'][s]['rss_mib'] for x in samples)}
 data={'seconds':elapsed,'logical_cpus':os.cpu_count(),'services':results,'system_cpu_percent':100*(1-(end_idle-idle)/(end_total-total)),'minimum_available_mib':min(x['available_mib'] for x in samples),'maximum_temperature_c':max(x['temperature_c'] for x in samples),'samples':samples}
 try:data['throttled']=subprocess.check_output(['vcgencmd','get_throttled'],text=True).strip()
 except (OSError,subprocess.CalledProcessError):pass
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(data,indent=2)+'\n');print(json.dumps({k:v for k,v in data.items() if k!='samples'}),flush=True)
if __name__=='__main__':main()

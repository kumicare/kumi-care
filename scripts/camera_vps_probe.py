#!/usr/bin/env python3
"""Decode a Mac-local RTSP stream on the VPS through a temporary SSH tunnel.

No Xiaomi credentials are sent to the VPS. Reports contain decoder metadata,
frame hashes and luma statistics, not pictures. With --snapshot one JPEG is
returned to the Mac only (never stored on the VPS) for human inspection.
A passing probe is not a camera reliability claim.
"""
import argparse
import base64
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REMOTE = r'''
import base64, json, os, pathlib, subprocess, sys, time, uuid
os.umask(0o077)
stream, port, seconds, warmup, snapshot = (sys.argv[1], int(sys.argv[2]),
    int(sys.argv[3]), int(sys.argv[4]), sys.argv[5] == '1')
FLAT_RANGE = 24  # luma max-min below this (8-bit) is treated as a flat/gray frame
root = pathlib.Path.home()/'.local/state/kumi-camera-probes'/str(uuid.uuid4())
root.mkdir(parents=True, mode=0o700)
url = 'rtsp://127.0.0.1:%d/%s' % (port, stream)
started = time.time()
report = {'stream':stream, 'requested_seconds':seconds, 'warmup_seconds':warmup,
          'status':'FAIL', 'evidence_directory':str(root), 'started_at_unix':started,
          'visual_validation':'NOT_CHECKED',
          'scope':'VPS decode over SSH; no identity or safety inference'}

def progress(msg):
    sys.stderr.write('[probe %ds] %s\n' % (time.time()-started, msg)); sys.stderr.flush()

def luma_rows(path):
    frames, cur = [], {}
    for line in path.read_text().splitlines():
        if line.startswith('frame:'):
            if cur: frames.append(cur)
            cur = {}
        elif line.startswith('lavfi.signalstats.'):
            key, _, value = line[len('lavfi.signalstats.'):].partition('=')
            cur[key] = float(value)
    if cur: frames.append(cur)
    return [f for f in frames if 'YMIN' in f and 'YMAX' in f and 'YAVG' in f]

try:
    probe = subprocess.run(['ffprobe','-v','error','-rtsp_transport','tcp',
        '-show_streams','-of','json',url], capture_output=True, timeout=20)
    if probe.returncode: raise RuntimeError('ffprobe failed')
    streams = json.loads(probe.stdout).get('streams',[])
    report['video'] = [{k:s.get(k) for k in
        ('codec_name','width','height','r_frame_rate')} for s in streams
        if s.get('codec_type')=='video']
    if not report['video']: raise RuntimeError('no video stream')
    hashes, luma = root/'frames.sha256', root/'luma.txt'
    vf = 'fps=1,scale=320:-2,signalstats,metadata=mode=print:file=%s' % luma
    with hashes.open('wb') as output, (root/'decoder.log').open('wb') as error:
        proc = subprocess.Popen(['ffmpeg','-nostdin','-hide_banner','-loglevel','error',
            '-xerror','-rtsp_transport','tcp','-i',url,'-ss',str(warmup),'-map','0:v:0',
            '-an','-t',str(seconds),'-vf',vf,'-f','framehash','-'],
            stdout=output, stderr=error)
        deadline = time.time()+seconds+warmup+30
        while proc.poll() is None:
            if time.time() > deadline:
                proc.kill(); proc.wait(); raise subprocess.TimeoutExpired('ffmpeg', seconds)
            time.sleep(1)
            if int(time.time()-started) % 30 == 0:
                n = sum(1 for l in hashes.read_text().splitlines() if l and not l.startswith('#'))
                progress('decoded %d/%d sampled frames' % (n, seconds))
    rows=[l for l in hashes.read_text().splitlines() if l.strip() and not l.startswith('#')]
    stats = luma_rows(luma) if luma.exists() else []
    # Output -ss drops warmup frames after filtering; keep only the sampled tail.
    stats = stats[len(stats)-len(rows):] if len(stats) >= len(rows) else stats
    nonflat = [f for f in stats if f['YMAX']-f['YMIN'] >= FLAT_RANGE]
    report['decoded_frames']=len(rows)
    report['distinct_frame_hashes']=len({r.split(',')[-1].strip() for r in rows})
    report['decoder_exit_code']=proc.returncode
    report['luma'] = {'frames':len(stats), 'nonflat_frames':len(nonflat),
        'flat_range_threshold':FLAT_RANGE,
        'yavg_min':min((f['YAVG'] for f in stats), default=None),
        'yavg_max':max((f['YAVG'] for f in stats), default=None)}
    report['status']='PASS' if (proc.returncode==0 and len(rows)>=seconds-1
                                and len(nonflat)>=seconds-1) else 'FAIL'
    if snapshot:
        progress('capturing snapshot')
        shot = subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error',
            '-rtsp_transport','tcp','-i',url,'-ss',str(warmup),'-map','0:v:0',
            '-frames:v','1','-f','image2pipe','-c:v','mjpeg','-q:v','3','-'],
            capture_output=True, timeout=warmup+30)
        if shot.returncode == 0 and shot.stdout:
            report['snapshot_captured_at_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            report['snapshot_jpeg_base64'] = base64.b64encode(shot.stdout).decode()
        else:
            report['snapshot_error'] = 'snapshot decode failed'
except subprocess.TimeoutExpired:
    report['error']='bounded decode/probe timeout'
except Exception as e:
    report['error']=type(e).__name__
report['elapsed_seconds']=round(time.time()-started,3)
persisted = {k:v for k,v in report.items() if k != 'snapshot_jpeg_base64'}
(root/'report.json').write_text(json.dumps(persisted,indent=2)+'\n')
print(json.dumps(report))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='kumi')
    parser.add_argument('--stream', required=True, help='go2rtc stream name; never a credential URL')
    parser.add_argument('--local-port', type=int, default=18554)
    parser.add_argument('--remote-port', type=int, default=18554)
    parser.add_argument('--seconds', type=int, default=15)
    parser.add_argument('--warmup', type=int, default=5, help='seconds skipped before sampling')
    parser.add_argument('--snapshot', action='store_true',
                        help='return one post-warmup JPEG to the Mac (private, 0600)')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.stream):
        parser.error('stream must contain only letters, digits, underscore or hyphen')
    if not re.fullmatch(r'[A-Za-z0-9_.@-]+', args.host) or args.host.startswith('-'):
        parser.error('invalid SSH host')
    if not 2 <= args.seconds <= 1800:
        parser.error('seconds must be 2..1800')
    if not 0 <= args.warmup <= 30:
        parser.error('warmup must be 0..30')
    if not all(1024 <= p <= 65535 for p in (args.local_port,args.remote_port)):
        parser.error('ports must be 1024..65535')
    folder = Path(__file__).resolve().parents[1]/'.local/camera/probes'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder.mkdir(parents=True, mode=0o700)
    # OpenSSH fails rather than silently using an occupied remote port.
    cmd=['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=10',
         '-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=10',
         '-o','ServerAliveCountMax=3','-R',
         f'127.0.0.1:{args.remote_port}:127.0.0.1:{args.local_port}',
         args.host,(f'python3 - {args.stream} {args.remote_port} {args.seconds} '
                    f'{args.warmup} {int(args.snapshot)}')]
    try:
        # Remote progress lines go straight to this terminal via inherited stderr.
        proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        stdout,_=proc.communicate(REMOTE,timeout=args.seconds+args.warmup+120)
        if proc.returncode:
            report={'status':'FAIL','error':'SSH or remote execution failed',
                    'exit_code':proc.returncode}
        else:
            report=json.loads(stdout)
    except subprocess.TimeoutExpired:
        proc.kill()
        report={'status':'FAIL','error':'SSH probe timeout'}
    except (OSError,ValueError):
        report={'status':'FAIL','error':'probe launch or response invalid'}
    jpeg=report.pop('snapshot_jpeg_base64',None)
    if jpeg:
        shot=folder/'snapshot.jpg'
        shot.touch(mode=0o600); shot.write_bytes(base64.b64decode(jpeg))
        report['local_snapshot']=str(shot)
    path=folder/'report.json'
    path.write_text(json.dumps(report,indent=2)+'\n'); path.chmod(0o600)
    print(json.dumps(report,indent=2)); print('Local report:',path)
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':
    sys.exit(main())

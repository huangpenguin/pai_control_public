#!/usr/bin/env python3
"""Offline clip of actual frames covering an existing Action; preserves source hashes."""
import json,hashlib,shutil,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1];run=sys.argv[1];src=root/'logs'/(run+'-video');out=root/'logs'/(run+'-action-video')
capture_path=src/'capture.json';raw_path=root/'logs'/(run+'.json')
c=json.loads(capture_path.read_text());a=json.loads(raw_path.read_text())
start=next(e['wall_seconds'] for e in a['events'] if e['event']=='raw_instruction_submitted')
end=next(e['wall_seconds'] for e in a['events'] if e['event']=='instruction_terminal')
frames=c['frames'];before=max(i for i,f in enumerate(frames) if f['wall_s']<=start);after=min(i for i,f in enumerate(frames) if f['wall_s']>=end)
selected=frames[before:after+1];assert selected[0]['wall_s']<=start and selected[-1]['wall_s']>=end
out.mkdir()
manifest={}
for f in selected:
 name=f['file'];assert Path(name).name==name
 shutil.copyfile(src/name,out/name);manifest[name]=hashlib.sha256((src/name).read_bytes()).hexdigest()
provenance={'source_capture':str(capture_path),'source_capture_sha256':hashlib.sha256(capture_path.read_bytes()).hexdigest(),'source_action_raw':str(raw_path),'source_action_raw_sha256':hashlib.sha256(raw_path.read_bytes()).hexdigest(),'action_window_wall_s':[start,end],'actual_clip_window_wall_s':[selected[0]['wall_s'],selected[-1]['wall_s']],'full_action_covered':True,'source_frame_count':len(frames),'selected_frame_count':len(selected),'original_actual_jpeg_sha256':manifest,'model_calls':0,'added_camera_frames':0,'clip_limit':'Only pre-action readiness and post-cleanup waiting omitted; actual temporal gaps preserved.'}
c['frames']=selected;c['collection_started_wall_s']=c['first_camera_wall_s']=selected[0]['wall_s'];c['collection_ended_wall_s']=c['last_camera_wall_s']=selected[-1]['wall_s'];c['derived_clip_provenance']=provenance
(out/'capture.json').write_text(json.dumps(c,indent=2)+'\n');(out/'clip-provenance.json').write_text(json.dumps(provenance,indent=2)+'\n');print(out)

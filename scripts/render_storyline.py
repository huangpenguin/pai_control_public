#!/usr/bin/env python3
"""Editorial replay of retained real camera/TF/audits. No simulation or API.
Run with /usr/bin/python3 (system Pillow); no project dependencies change.
"""
import argparse
import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
BOLD = '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'
INK, MUTED, BG, MINT, GREEN, RED = '#172d47', '#687b8b', '#eff3ef', '#dff3eb', '#12a487', '#d85a4e'
W, H = 1280, 900


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=24)
def font(size, bold=False):
    return ImageFont.truetype(BOLD if bold else FONT, size)


def text(draw, xy, value, size=18, fill=INK, bold=False):
    draw.text(xy, value, font=font(size, bold), fill=fill)


def card(draw, box, fill='white', outline='#dce5e0', width=1, radius=14):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def photo(canvas, source, box):
    # Camera area is the retained real JPEG, with no content inpainting or overlay.
    image = Image.open(source).convert('RGB')
    if image.size != (box[2]-box[0], box[3]-box[1]):
        image = image.resize((box[2]-box[0], box[3]-box[1]), Image.Resampling.LANCZOS)
    canvas.paste(image, box[:2])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--preview-only', action='store_true')
    parser.add_argument('--run-id', choices=['p4-formal1-red-green','p4-formal2-green-red'], default='p4-formal1-red-green')
    parser.add_argument('--speed', type=float, choices=[2,3], default=3)
    parser.add_argument('--closing-seconds', type=float, default=8)
    parser.add_argument('--max-seconds', type=float, default=180)
    args = parser.parse_args()
    # Offline means offline: refuse while this project's simulation is active.
    running = subprocess.check_output(['docker', 'compose', 'ps', '--status', 'running', '-q', 'simulation'], cwd=ROOT, text=True)
    if running.strip():
        raise SystemExit('Stop the simulation before editorial rendering.')
    if not 0<=args.closing_seconds<=8:
        parser.error('Closing card must be within [0,8] seconds')
    run_number=1 if args.run_id=='p4-formal1-red-green' else 2
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    raw_path = ROOT/'logs'/(args.run_id+'.json')
    capture_path = ROOT/'logs'/(args.run_id+'-action-video/capture.json')
    raw, capture = load(raw_path), load(capture_path)
    frames, events = capture['frames'], raw['events']
    steps = raw['planning']['validated_plan']['steps']
    first,second=('station_b','station_a') if run_number==1 else ('station_a','station_b')
    expected = [('navigate_to',first),('inspect_indicator',first),('navigate_to',second),('inspect_indicator',second),('return_to_base',None)]
    assert [(s['op'],s['place_id']) for s in steps] == expected
    start = next(e['wall_seconds'] for e in events if e['event']=='raw_instruction_submitted')
    terminal = next(e for e in events if e['event']=='instruction_terminal')
    assert raw['pass_'] and raw['action_requests_sent']==1 and raw['api_attempt_delta']==1
    assert frames[0]['wall_s']<=start and frames[-1]['wall_s']>=terminal['wall_seconds']
    planning_done = next(e['wall_seconds'] for e in events if e.get('audit',{}).get('kind')=='planning_completed')
    completion, inspections = {}, {}
    for e in events:
        a = e.get('audit',{})
        if a.get('kind')=='arrival_verified':
            assert a['action_uuid']==raw['action_uuid']
            index={first:0,second:2,'base':4}[a['place_id']]
            completion[index]=e['wall_seconds']
        if a.get('kind')=='inspection_completed':
            i=a['inspection'];assert i['action_uuid']==raw['action_uuid']
            index={first:1,second:3}[i['place_id']]
            completion[index]=e['wall_seconds'];inspections[i['place_id']]=(e['wall_seconds'],i)
    assert len(completion)==5 and all(completion[i]<completion[i+1] for i in range(4))
    source_dir=capture_path.parent
    labels=[first[-1].upper()+' へ移動',first[-1].upper()+' の灯を検査',second[-1].upper()+' へ移動',second[-1].upper()+' の灯を検査','起点へ帰還']
    japanese_instruction=f'設備{first[-1].upper()}のランプを確認し、次に設備{second[-1].upper()}のランプを確認。最後に起点へ戻る。'
    stations={'base':(-3,-2),'station_b':(3,2),'station_a':(3,-2)}
    source_hashes={str(p.relative_to(ROOT)):sha(p) for p in [raw_path,capture_path,Path(__file__)]}
    used_camera={}
    xy=lambda x,y:(746+(x+5.2)/10.4*450,298+(4.2-y)/8.4*310)

    def render(frame,index):
        wall=frame['wall_s'];done=[i for i in range(5) if completion[i]<=wall]
        active=next((i for i in range(5) if i not in done),None) if wall>=planning_done else None
        canvas=Image.new('RGB',(W,H),BG);d=ImageDraw.Draw(canvas)
        text(d,(30,17),'ことば → 計画 → 移動 → 画像確認',13,GREEN,True)
        text(d,(28,42),'ことばで巡回。自分の目で確認。',31,INK,True)
        text(d,(886,28),'2026-10-11 の実行記録',15,MUTED)
        card(d,(1063,55,1248,83),INK,INK);text(d,(1080,57),f'記録の再生・{args.speed:g}倍速',14,'white',True)
        card(d,(28,96,1252,143),INK,INK)
        text(d,(43,98),'入力原文（中国語）',14,'#ffcf74',True)
        text(d,(205,98),raw['original_instruction'],16,'white')
        text(d,(43,122),'日本語訳',12,'#ffcf74',True)
        text(d,(205,120),japanese_instruction,14,'white')
        for i,label in enumerate(labels):
            x=28+i*247;fill=MINT if i in done else 'white'
            card(d,(x,158,x+236,220),fill,GREEN if active==i else '#dce5e0',3 if active==i else 1)
            state='✓' if i in done else str(i+1)
            text(d,(x+11,168),state+'  '+label,18,INK,True)
            detail='確認済み' if i in done else ('実行中' if active==i else ('待検査' if steps[i]['op']=='inspect_indicator' else '未実行'))
            if wall<planning_done:detail='AI計画の検証待ち'
            text(d,(x+12,195),detail,12,GREEN if i in done else MUTED)
        card(d,(28,237,688,786));text(d,(42,247),'ロボットの目（実際のカメラ）',17,INK,True)
        text(d,(438,250),'保存画像・描き直しなし',12,MUTED)
        source=source_dir/frame['file'];used_camera[frame['file']]=sha(source)
        photo(canvas,source,(38,280,678,760))
        text(d,(42,763),f'撮影時の仮想時刻 {frame["image_sim_s"]:.2f}秒・640×480・次の画像まで保持表示',12,MUTED)
        card(d,(708,237,1252,786));text(d,(723,247),'実際に通った道',17,INK,True)
        text(d,(1080,251),'位置の記録から作図',12,MUTED)
        d.rounded_rectangle((737,290,1205,617),radius=12,fill='#f6f9fa',outline=INK,width=5)
        for x in range(765,1200,43):d.line((x,294,x,613),fill='#e0e8e9',width=1)
        for y in range(315,612,37):d.line((741,y,1201,y),fill='#e0e8e9',width=1)
        bx,by=xy(-.7,.7);ex,ey=xy(.7,-.7);card(d,(bx,by,ex,ey),INK,INK,radius=3)
        text(d,(bx+4,by+13),'障害物',11,'white')
        points=[xy(f['map_pose']['x'],f['map_pose']['y']) for f in frames[:index+1] if f.get('map_pose')]
        if len(points)>1:d.line(points,fill=GREEN,width=5)
        for name,(sx,sy) in stations.items():
            px,py=xy(sx,sy);result=inspections.get(name);color='#cbd0d8'
            if name=='base':color='#ffcf74'
            if result and result[0]<=wall:color=GREEN if result[1]['color']=='green' else RED
            d.ellipse((px-11,py-11,px+11,py+11),fill=color,outline=INK,width=3)
            text(d,(px-15,py-33),'起点' if name=='base' else name[-1].upper(),13,INK,True)
        if frame.get('map_pose'):
            p=frame['map_pose'];px,py=xy(p['x'],p['y']);yaw=2*math.atan2(p['qz'],p['qw'])
            d.ellipse((px-15,py-15,px+15,py+15),fill='white',outline=INK,width=3)
            d.line((px,py,px+12*math.cos(yaw),py-12*math.sin(yaw)),fill=GREEN,width=4)
        for j,name in enumerate([first,second]):
            x=724+j*257;card(d,(x,632,x+245,713),'#f0f5f3','#f0f5f3')
            text(d,(x+10,639),'設備 '+name[-1].upper(),14)
            record=inspections[name]
            if record[0]<=wall:
                ins=record[1];green=ins['color']=='green';text(d,(x+10,660),'緑 / 正常' if green else '赤 / 異常',23,GREEN if green else RED,True)
                path=ROOT/'logs/inspections'/Path(ins['png_path']).name;assert sha(path)==ins['png_sha256']
                source_hashes[str(path.relative_to(ROOT))]=sha(path);photo(canvas,path,(x+175,640,x+235,685))
                text(d,(x+10,692),f'確認時の画像 · +{record[0]-start:.2f}s',10,MUTED)
            else:text(d,(x+10,665),'待検査 / 結果を先読みしない',16,MUTED)
        text(d,(725,724),f'経過時間 +{max(0,wall-start):.1f}秒',18,INK,True)
        text(d,(1018,724),f'完了 {len(done)}/5 手順',18,GREEN,True)
        d.rounded_rectangle((725,759,1235,766),radius=3,fill='#dae6e0')
        if done:d.rounded_rectangle((725,759,725+510*len(done)/5,766),radius=3,fill=GREEN)
        text(d,(29,802),'実行記録に基づく再生です。新たに走行させたり、AI に問い合わせたりしていません。',15,INK,True)
        text(d,(29,832),'右図は上から撮った映像ではなく、位置の記録を描いた図です。カメラとは完全同期せず、位置の補間もしていません。',13,MUTED)
        text(d,(29,855),f'本編は正式試験の第{run_number}回です。過去のオンライン巡回は成功1/3回。失敗2件も報告と元の記録に残しています。',13,MUTED)
        return canvas

    def final_card():
        canvas=Image.new('RGB',(W,H),BG);d=ImageDraw.Draw(canvas)
        text(d,(40,28),'本編の外側も、正直に。',34,INK,True)
        text(d,(42,85),'ここからは別の実行記録です（本編の続きではありません）',20,MUTED)
        other=[]
        for number,name in [(1,'p4-formal1-red-green'),(2,'p4-formal2-green-red'),(3,'p4-formal3-gray')]:
            if number==run_number:
                continue
            path=ROOT/'logs'/(name+'.json');r=load(path);assert r['pass_'];source_hashes[str(path.relative_to(ROOT))]=sha(path)
            for e in r['events']:
                a=e.get('audit',{})
                if a.get('kind')=='inspection_completed':other.append((number,a['inspection']))
        for k,(number,ins) in enumerate(other):
            x=40+k*413;card(d,(x,145,x+392,588));path=ROOT/'logs/inspections'/Path(ins['png_path']).name
            assert sha(path)==ins['png_sha256'];source_hashes[str(path.relative_to(ROOT))]=sha(path)
            photo(canvas,path,(x+12,211,x+380,487))
            text(d,(x+16,162),f'別の正式試験 第{number}回・設備 '+ins['place_id'][-1].upper(),19,INK,True)
            label={'red':'赤 / 異常','green':'緑 / 正常','unknown':'不明 / 判定できず'}[ins['color']]
            text(d,(x+16,505),label,25,GREEN if ins['color']=='green' else RED if ins['color']=='red' else MUTED,True)
            text(d,(x+16,549),'同じ場所でランプの色を交換' if number in (1,2) else '灰色のランプは画像から判定できず',15,MUTED)
        card(d,(40,618,1240,777),INK,INK)
        text(d,(65,641),'正式な画像確認試験：3/3回成功・5/5枚を正しく判定',28,'#9ee7c9',True)
        text(d,(65,693),'過去のオンライン巡回は成功1/3回。失敗2件も保存。',24,'white',True)
        text(d,(44,806),'接触は記録できた範囲で集計しています。衝突が一切なかったという保証ではありません。',16,MUTED)
        text(d,(44,842),'保存データだけの編集です。元の実行記録・画像は別途保管。新たな AI 呼出し・走行はありません。',15,MUTED)
        return canvas

    previews=[0,next(i for i,f in enumerate(frames) if f['wall_s']>=completion[1]),len(frames)-1]
    for name,i in zip(['start','inspection-'+first[-1],'complete'],previews):render(frames[i],i).save(output/(name+'.png'))
    final_card().save(output/'other-trials.png')
    if args.preview_only:
        names=[]
    else:
        names=[]
        for i,frame in enumerate(frames):
            name=f'frame-{i:05}.png';render(frame,i).save(output/name)
            next_wall=frames[i+1]['wall_s'] if i+1<len(frames) else capture['collection_ended_wall_s']
            names += [f"file '{name}'",f'duration {max(next_wall-frame["wall_s"],.001)/args.speed:.9f}']
        if args.closing_seconds:
            names += ["file 'other-trials.png'",f'duration {args.closing_seconds:g}',"file 'other-trials.png'"]
        else:
            names.append(f"file 'frame-{len(frames)-1:05}.png'")
        (output/'concat.txt').write_text('\n'.join(names)+'\n')
        subprocess.run(['ffmpeg','-nostdin','-n','-hide_banner','-loglevel','warning','-threads','1','-f','concat','-safe','0','-i',str(output/'concat.txt'),'-vf','fps=10','-c:v','libx264','-preset','veryfast','-crf','22','-pix_fmt','yuv420p','-threads','1',str(output/'storyline.mp4')],check=True)
        probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration,size:stream=codec_name,width,height,pix_fmt,r_frame_rate','-of','json',str(output/'storyline.mp4')],text=True))
        assert 60<=float(probe['format']['duration'])<=args.max_seconds
    manifest={'kind':'offline_editorial_storyline_not_new_execution','sources':source_hashes,'camera_jpeg_sha256':used_camera,'original_baseline_unchanged':'logs/p4-formal1-red-green-action-video/render-x3/video.mp4','original_baseline_sha256':sha(ROOT/'logs/p4-formal1-red-green-action-video/render-x3/video.mp4'),'main_run_id':args.run_id,'action_uuid':raw['action_uuid'],'application_request_id':raw['planning']['request_id'],'step_completion_source':'same-Action arrival_verified or inspection_completed, never Nav2 success alone','completion_wall_s':completion,'action_window_wall_s':[start,terminal['wall_seconds']],'actual_camera_window_wall_s':[frames[0]['wall_s'],frames[-1]['wall_s']],'camera_frame_count':len(frames),'main_speedup':args.speed,'closing_other_trials_seconds':args.closing_seconds,'heading_source':'recorded TF quaternion; no interpolated positions','color_reveal':'only after actual inspection audit; unknown only in labeled other-trials closing card','original_failures_preserved':'historical online P2 1/3; formal tasks 3/3 and RGB5/5 are separate','camera_tf_exact_sync':False,'new_api_calls':0,'new_simulation_runs':0,'preview_only':args.preview_only,'display_language':'ja','original_chinese_instruction_preserved':True,'japanese_instruction_translation':japanese_instruction}
    if not args.preview_only:manifest.update({'output_sha256':sha(output/'storyline.mp4'),'ffprobe':probe})
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(output)


if __name__=='__main__':
    main()

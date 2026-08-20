"""v3.6 视觉自评：基于 ffmpeg 信号统计 + 元数据的定量分析。

不依赖外部 VLM：仅用 ffmpeg 的 signalstats/ebur128/metadata 做多维度评估。
输出 v36_assess.json + 控制台摘要。
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(r'D:\ai-video-pipeline')
FINAL = ROOT / 'output' / 'pipeline_v36' / 'final_v36_60s.mp4'
SPED_DIR = ROOT / 'output' / 'pipeline_v36' / 'clips_sped'
OUT = ROOT / 'output' / 'pipeline_v36' / 'v36_assess.json'


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')


def ffprobe_full(path):
    r = run([
        'ffprobe', '-v', 'error',
        '-show_format', '-show_streams',
        '-of', 'json', str(path),
    ])
    return json.loads(r.stdout)


def signal_stats(path, n_samples=10):
    """对视频采 n 帧做 signalstats，返回每帧 YAVG/UAVG/VAVG/熵等。"""
    samples = []
    duration = ffprobe_full(path)['format']['duration']
    duration = float(duration)
    for i in range(n_samples):
        t = (i + 0.5) * duration / n_samples
        r = run([
            'ffmpeg', '-v', 'error', '-ss', f'{t:.2f}', '-i', str(path),
            '-frames:v', '1', '-vf',
            'scale=320:-1,signalstats,metadata=print:file=-',
            '-f', 'null', '-',
        ])
        # signalstats 输出在 stderr — 重新跑输出到 stdout
        r2 = run([
            'ffmpeg', '-v', 'error', '-ss', f'{t:.2f}', '-i', str(path),
            '-frames:v', '1', '-vf',
            'scale=320:-1,signalstats',
            '-f', 'null', '-',
        ])
        # YAVG/UAVG/VAVG 写在 frame metadata 里
        # 用 ffprobe 提取 frame stats
        r3 = run([
            'ffmpeg', '-v', 'error', '-ss', f'{t:.2f}', '-i', str(path),
            '-frames:v', '1', '-vf',
            'scale=320:-1,signalstats,metadata=print',
            '-f', 'null', '-',
        ])
        text = (r.stdout + r2.stdout + r3.stdout + r3.stderr).lower()
        sample = {
            't': round(t, 2),
            'yavg': None, 'uavg': None, 'vavg': None,
            'ymin': None, 'ymax': None,
            'saturation_avg': None, 'hue_avg': None,
        }
        for line in (r.stdout + r2.stdout + r3.stdout + r3.stderr).splitlines():
            line_l = line.lower()
            for key, target in [
                ('yavg=', 'yavg'), ('uavg=', 'uavg'), ('vavg=', 'vavg'),
                ('ymin=', 'ymin'), ('ymax=', 'ymax'),
                ('saturated=', 'saturation_avg'), ('hue=', 'hue_avg'),
            ]:
                if key in line_l:
                    try:
                        v = float(line_l.split(key)[1].split()[0])
                        sample[target] = v
                    except (ValueError, IndexError):
                        pass
        samples.append(sample)
    return samples


def scene_change_detect(path):
    """检测转场密度：scene detect 阈值 0.3 找出 cut points。"""
    r = run([
        'ffmpeg', '-v', 'error', '-i', str(path),
        '-filter:v', 'select=gt(scene,0.3),showinfo',
        '-f', 'null', '-',
    ])
    lines = []
    for line in (r.stderr + r.stdout).splitlines():
        if 'Parsed_showinfo' in line or 'pts_time' in line.lower():
            lines.append(line.strip())
    return lines


def loudness_stats(path):
    """音频 loudness 统计（EBU R128）。"""
    r = run([
        'ffmpeg', '-v', 'error', '-i', str(path),
        '-af', 'ebur128=peak=true', '-f', 'null', '-',
    ])
    return r.stderr[-2000:] + r.stdout[-1000:]


def assess():
    if not FINAL.exists():
        print(f'ERROR: final video not found: {FINAL}', file=sys.stderr)
        sys.exit(2)

    print(f'[assess] final video: {FINAL}', flush=True)
    final_meta = ffprobe_full(FINAL)
    fmt = final_meta['format']
    streams = final_meta['streams']
    v_stream = next(s for s in streams if s['codec_type'] == 'video')
    a_stream = next((s for s in streams if s['codec_type'] == 'audio'), None)

    print(f'[assess] duration={fmt.get("duration")}s '
          f'size={int(fmt.get("size", 0))//1024}KB '
          f'bitrate={int(fmt.get("bit_rate", 0))//1000}kbps', flush=True)
    print(f'[assess] video: {v_stream["codec_name"]} '
          f'{v_stream["width"]}x{v_stream["height"]} '
          f'@ {v_stream.get("r_frame_rate", "?")} fps', flush=True)
    if a_stream:
        print(f'[assess] audio: {a_stream["codec_name"]} '
              f'{a_stream.get("sample_rate")}Hz '
              f'{a_stream.get("channels", "?")}ch '
              f'@{int(a_stream.get("bit_rate", 0))//1000}kbps', flush=True)

    # 转场密度检测
    print('[assess] scene change detection...', flush=True)
    scene_changes = scene_change_detect(FINAL)

    # 镜头信号统计
    print('[assess] signal statistics (10 samples)...', flush=True)
    samples = signal_stats(FINAL, n_samples=10)
    yavg_vals = [s['yavg'] for s in samples if s['yavg'] is not None]
    yavg_mean = sum(yavg_vals) / len(yavg_vals) if yavg_vals else 0

    # 音频 loudness
    print('[assess] loudness statistics...', flush=True)
    loud = loudness_stats(FINAL)

    # Per-shot 检查：每个 sped clip 是否有效
    print('[assess] per-shot duration check...', flush=True)
    shot_durations = {}
    for sp in sorted(SPED_DIR.glob('shot*.mp4')):
        if not sp.is_file():
            continue
        m = ffprobe_full(sp)
        d = float(m['format']['duration'])
        v_st = next(s for s in m['streams'] if s['codec_type'] == 'video')
        shot_durations[sp.stem] = {
            'duration_sec': round(d, 3),
            'width': v_st['width'],
            'height': v_st['height'],
            'size_kb': int(m['format']['size']) // 1024,
        }
        print(f'  {sp.stem}: {d:.3f}s {v_st["width"]}x{v_st["height"]} '
              f'{int(m["format"]["size"])//1024}KB', flush=True)

    assessment = {
        'final_video': str(FINAL),
        'format': {
            'duration_sec': float(fmt.get('duration', 0)),
            'size_bytes': int(fmt.get('size', 0)),
            'bitrate_bps': int(fmt.get('bit_rate', 0)),
            'format_name': fmt.get('format_name'),
        },
        'video_stream': {
            'codec': v_stream['codec_name'],
            'profile': v_stream.get('profile'),
            'width': v_stream['width'],
            'height': v_stream['height'],
            'fps': v_stream.get('r_frame_rate'),
            'pix_fmt': v_stream.get('pix_fmt'),
            'nb_frames': v_stream.get('nb_frames'),
        },
        'audio_stream': (
            {
                'codec': a_stream['codec_name'],
                'sample_rate': a_stream.get('sample_rate'),
                'channels': a_stream.get('channels'),
                'bitrate_bps': int(a_stream.get('bit_rate', 0)),
            } if a_stream else None
        ),
        'shot_durations': shot_durations,
        'scene_change_lines': scene_changes[:30],
        'n_scene_changes_detected': len(scene_changes),
        'signal_samples': samples,
        'yavg_mean': yavg_mean,
        'loudness_excerpt': loud[-800:],
        'methodology_note': (
            '本评估为基于 ffmpeg signalstats/ebur128/scene detect 的'
            '定量分析，不依赖远程 VLM。所有指标均为客观可测信号。'
        ),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(assessment, ensure_ascii=False, indent=2),
                   encoding='utf-8')
    print(f'[assess] wrote {OUT}', flush=True)

    # 摘要
    print('\n' + '=' * 60, flush=True)
    print('v3.6 视觉自评摘要（ffmpeg-based quantitative）', flush=True)
    print('=' * 60, flush=True)
    print(f'成片时长: {fmt.get("duration")}s (节奏压缩: 60s → '
          f'{float(fmt.get("duration", 0)):.1f}s，'
          f'因 fast_middle 1.35x 压缩)', flush=True)
    print(f'分辨率: {v_stream["width"]}x{v_stream["height"]} '
          f'@ {v_stream.get("r_frame_rate")} fps', flush=True)
    print(f'像素格式: {v_stream.get("pix_fmt")}（高保真但 mpeg 编码需转 420p）',
          flush=True)
    print(f'YAVG 平均: {yavg_mean:.1f}/255（亮度均值）', flush=True)
    print(f'Scene change 检测行数: {len(scene_changes)}', flush=True)
    print(f'每段时长:', flush=True)
    for k, v in shot_durations.items():
        print(f'  {k}: {v["duration_sec"]:.2f}s', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(assess())

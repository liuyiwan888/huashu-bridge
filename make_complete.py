#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_complete.py —— 「文案 → 完整艺术解说短视频」一键桥（接进现有流水线）
========================================================================
在 make_video.py（画面轨）基础上，补齐现有流水线的后半段：
  · 配音：默认走本地 CosyVoice（内置「中文女」），本地推理、不依赖常驻服务；
          找不到 CosyVoice 时自动退回本机 Qwen3-TTS 私有服务（http://127.0.0.1:18080）；
          可用 --tts cosyvoice/qwen3/none 手动指定
  · 字幕：默认对「画面已含文字」的风格（y5 动态大字 / t2 功能卡）不烧录；
           t3 财经图表（画面是图、不含口播句）默认烧无标点旁白字幕；
           可用 --sub 强制开启、--no-sub 强制关闭
  · BGM ：从「抖音爆款」库轮转选曲（pick_bgm.py），配音的 2.5% 音量 + 首尾淡入淡出
  · 响度：loudnorm I=-16（与 baokuan 流水线一致）

配音后端全部不可用时：自动跳过配音，仍输出「画面 + 字幕（+可选BGM）」成片。

依赖：
  - make_video.py 同目录（复用文案解析与 render）
  - ffmpeg（已在）、本机 Qwen3-TTS 服务（配音时）、抖音爆款 BGM 库（混音时）

用法：
  # 完整版（配音+字幕+BGM），自动选语法
  python make_complete.py 文案.md --out 成片/完整.mp4

  # 指定语法 / 竖屏 / 音色
  python make_complete.py 文案.md --mode t3 --theme paper --out 成片/图表.mp4
  python make_complete.py 文案.md --portrait --theme snow --voice Vivian --out 成片/竖屏.mp4

  # 不要配音（只出画面+字幕，适合先预览画面）
  python make_complete.py 文案.md --no-tts --out 成片/预览.mp4

  # 强制指定配音后端（默认 auto：优先本地 CosyVoice）
  python make_complete.py 文案.md --tts cosyvoice --cosyvoice-speaker 中文女 --out 成片/CosyVoice.mp4
  python make_complete.py 文案.md --tts qwen3 --voice Vivian --out 成片/Qwen3.mp4

  # 不要 BGM
  python make_complete.py 文案.md --no-bgm --out 成片/纯配音.mp4
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 复用同目录 make_video.py 的解析与渲染
sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_video import (  # noqa: E402
    parse_y5, parse_t3, parse_t2, auto_mode,
    render, THEMES, DEFAULT_ENGINE,
)

# ---------- 常量 ----------
DEFAULT_TTS_URL = "http://127.0.0.1:18080/v1/audio/speech"
DEFAULT_VOICE = "Vivian"
# 本地 CosyVoice（优先后端）：本地推理，不依赖常驻服务
COSYVOICE_ROOT = Path(r"D:\codex\github\CosyVoice")
COSYVOICE_VENV = COSYVOICE_ROOT / ".venv" / "Scripts" / "python.exe"
COSYVOICE_TTS_SCRIPT = Path(__file__).resolve().parent / "cosyvoice_tts.py"
COSYVOICE_DEFAULT_SPEAKER = "中文女"
BGM_VOL = 0.025          # BGM = 配音的 2.5%
LEAD = 0.7               # 首屏起播前延迟
TAIL = 0.5               # 每页配音后留白
T3_MIN = 6.0             # t3 图表最短时长
PUNCT = "。！？；，、.!?;:,：∶…—–-~·""''（）()[]【】<>《》\t\n "  # 字幕去标点集合


# ---------- 工具 ----------
def ffprobe_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        try:
            return float(r.stdout.strip())
        except ValueError:
            return None
    return None


def strip_punct(text):
    """去掉中英文标点（与现有流水线『无标点烧录字幕』一致）。"""
    return re.sub(rf"[{re.escape(PUNCT)}]+", " ", text).strip()


def tts_synthesize(text, voice, url, out_path, timeout=120):
    """调用本机 Qwen3-TTS（OpenAI 兼容）合成一段音频，返回时长（秒）。"""
    import requests
    resp = requests.post(
        url,
        json={
            "model": "qwen3-tts-1.7b",
            "voice": voice,
            "input": text,
            "response_format": "mp3",
            "language": "Chinese",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    out_path.write_bytes(resp.content)
    return ffprobe_duration(out_path) or 0.0


def choose_tts_backend(mode_arg):
    """选择配音后端。auto：优先本地 CosyVoice（无需常驻服务），否则 Qwen3-TTS。"""
    if mode_arg in ("qwen3", "cosyvoice", "none"):
        return mode_arg
    if COSYVOICE_ROOT.exists() and COSYVOICE_VENV.exists():
        return "cosyvoice"
    return "qwen3"  # 后续若 18080 连不上会自动降级为无声


def cosyvoice_synthesize(segs, speaker, work):
    """调用本地 CosyVoice 为所有口播片段合成 wav，返回 (audio_paths, durations)。"""
    if not (COSYVOICE_ROOT.exists() and COSYVOICE_VENV.exists()):
        raise RuntimeError("CosyVoice 未安装（缺目录或 venv）")
    cues_json = work / "_cv_cues.json"
    cues_json.write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
    out_dir = work / "_cv"
    dur_json = work / "_cv_durations.json"
    cmd = [
        str(COSYVOICE_VENV), str(COSYVOICE_TTS_SCRIPT),
        "--cosyvoice-root", str(COSYVOICE_ROOT),
        "--speaker", speaker,
        "--cues", str(cues_json),
        "--outdir", str(out_dir),
        "--durations", str(dur_json),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("---- CosyVoice stderr ----\n" + (r.stderr or "")[-1500:])
        raise RuntimeError("CosyVoice 合成失败（详见上方 stderr）")
    durations = json.loads(dur_json.read_text(encoding="utf-8"))
    audio = []
    for i in range(len(segs)):
        p = out_dir / f"cue_{i:03d}.wav"
        ok = p.exists() and durations[i] > 0
        audio.append(p if ok else None)
        durations[i] = durations[i] if ok else 0.0
    return audio, durations


def cue_narration_y5(cue):
    """y5 单页的口播文案。"""
    if cue.get("kind") == "number":
        val = cue["data"]["value"]
        suffix = cue["data"].get("suffix", "")
        sub = cue.get("sub") or ""
        txt = f"{val}{suffix}"
        return f"{txt}，{sub}" if sub else txt
    text = cue.get("text", "")
    sub = cue.get("sub")
    return f"{text}，{sub}" if sub else text


def cue_narration_t2(cue):
    text = cue.get("text", "")
    sub = cue.get("sub")
    return f"{text}：{sub}" if sub else text


def t3_narration(data):
    parts = []
    if data.get("title"):
        parts.append(data["title"])
    if data.get("unit"):
        parts.append(data["unit"])
    for s in data.get("series", []):
        parts.append(f"{s['label']} {s['value']}")
    hi = data.get("highlight", {}).get("index")
    if hi is not None and hi < len(data.get("series", [])):
        parts.append(f"最高的是{data['series'][hi]['label']}")
    return "，".join(parts) + "。"


# ---------- 主流程 ----------
def run(input_path, out_path, mode, theme, portrait, engine,
       voice, tts_url, tts_backend, cosyvoice_speaker, use_tts, use_bgm, bgm_mood, bgm_vol, keep_spec, show_sub):
    lines = Path(input_path).read_text(encoding="utf-8").splitlines()
    if mode == "auto":
        mode = auto_mode(lines)
    print(f">> 语法判定：{mode}  |  画幅：{'竖屏' if portrait else '横屏'}  |  色板：{theme}")

    work = Path(out_path).resolve().parent
    work.mkdir(parents=True, exist_ok=True)

    # 1) 解析 cues / data
    if mode == "y5":
        cues, _ = parse_y5(lines, portrait)
        data = None
    elif mode == "t2":
        data, cues, _ = parse_t2(lines)
    elif mode == "t3":
        data, cues, _ = parse_t3(lines)
    else:
        raise SystemExit(f"make_complete 暂不支持 mode={mode}（仅 y5/t2/t3）")

    # 2) 逐 cue 配音（按后端分发）
    voice_available = False
    cue_audio = []   # 每个 cue 的音频路径
    cue_durs = []    # 每个 cue 配音时长
    if use_tts and tts_backend != "none":
        # 准备口播文本：t3 整段一段；y5/t2 逐页
        if mode == "t3":
            segs = [t3_narration(data)]
        else:
            segs = []
            for cue in cues:
                narr = cue_narration_y5(cue) if mode == "y5" else cue_narration_t2(cue)
                segs.append(narr.strip())

        if tts_backend == "qwen3":
            print(">> 配音：逐页调用本机 Qwen3-TTS（18080）...")
            for i, text in enumerate(segs):
                if not text:
                    cue_audio.append(None); cue_durs.append(0.0); continue
                ap = work / f"_cue_{i:02d}.mp3"
                try:
                    d = tts_synthesize(text, voice, tts_url, ap)
                    cue_audio.append(ap); cue_durs.append(d)
                    print(f"   [{i+1}/{len(segs)}] {text[:18]}…  {d:.1f}s")
                except Exception as e:
                    print(f"   [!] 配音失败（{e}），本页跳过配音")
                    cue_audio.append(None); cue_durs.append(0.0)
        else:  # cosyvoice
            print(f">> 配音：本地 CosyVoice（{cosyvoice_speaker}）...")
            try:
                cue_audio, cue_durs = cosyvoice_synthesize(segs, cosyvoice_speaker, work)
            except Exception as e:
                print(f"   [!] CosyVoice 合成失败（{e}），整体跳过配音")
                cue_audio = [None] * len(segs)
                cue_durs = [0.0] * len(segs)
        voice_available = any(cue_audio)
        if not voice_available:
            if tts_backend == "cosyvoice":
                print(">> CosyVoice 合成失败：将输出『画面+字幕』无声成片")
            else:
                print(">> 本机 Qwen3-TTS 未连接/全部失败：将输出『画面+字幕』无声成片（启动 18080 后加 --tts 重跑可补配音）")
    else:
        print(">> 已指定 --no-tts / --tts none：跳过配音")

    # 3) 按配音时长重排 cue 时间轴（画面与配音锁帧）
    total = 0.0
    if mode == "t3":
        t3_dur = cue_durs[0] if (voice_available and cue_audio[0]) else 0.0
        total = max(T3_MIN, t3_dur + 0.6)
        # cues 沿用解析里的相对 at（0.3 / 2.7），保持；末尾自然停在 total
        for c in cues:
            pass
    else:
        at = LEAD
        for i, c in enumerate(cues):
            d = cue_durs[i] if (voice_available and cue_audio[i]) else (
                2.2 if mode == "y5" else 2.0)
            c["at"] = round(at, 2)
            at += d + TAIL
            total = at
        total = round(total + 0.2, 2)

    # 4) 组装 spec 并渲染无声画面轨
    if mode == "y5":
        spec = {"grammar": "y5_kinetic_type", "duration": total, "fps": 30,
                "width": 1080 if portrait else 1920, "height": 1920 if portrait else 1080,
                "theme": theme, "cues": cues}
    elif mode == "t2":
        spec = {"grammar": "t2_keynote_ui", "duration": total, "fps": 30,
                "width": 1080 if portrait else 1920, "height": 1920 if portrait else 1080,
                "theme": theme, "data": data, "cues": cues}
    else:  # t3
        spec = {"grammar": "t3_finance_chart", "duration": total, "fps": 30,
                "width": 1080 if portrait else 1920, "height": 1920 if portrait else 1080,
                "theme": theme, "data": data, "cues": cues}

    spec_path = work / (Path(out_path).stem + ".spec.json")
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    silent_mp4 = work / (Path(out_path).stem + ".silent.mp4")
    render(spec_path, silent_mp4, engine, portrait)

    # 5) 字幕（无标点）
    subs_srt = work / "_subs.srt"
    subs_lines = []
    if mode == "t3":
        narr = strip_punct(t3_narration(data))
        subs_lines.append(srt_block(1, 0.3, total - 0.2, narr))
    else:
        for i, c in enumerate(cues):
            narr = cue_narration_y5(c) if mode == "y5" else cue_narration_t2(c)
            txt = strip_punct(narr)
            if not txt:
                continue
            end = c["at"] + (cue_durs[i] if (voice_available and cue_audio[i]) else (
                2.2 if mode == "y5" else 2.0)) + TAIL
            subs_lines.append(srt_block(i + 1, c["at"], min(end, total - 0.1), txt))
    subs_srt.write_text("\n".join(subs_lines) + "\n", encoding="utf-8")

    # 6) 配音轨：把每页音频按 at 延时对齐到视频时间轴
    voice_wav = None
    if voice_available:
        voice_wav = work / "_voice.wav"
        build_voice_track(cue_audio, cues, cue_durs, total, voice_wav, work, mode)

    # 7) BGM
    bgm_path = None
    if use_bgm:
        bgm_path = pick_bgm_file(bgm_mood)
        if bgm_path:
            print(f">> BGM：{Path(bgm_path).name}（{bgm_vol*100:.1f}%）")
        else:
            print(">> 未找到 BGM 库，跳过混音")

    # 8) 合成：画面 + 字幕 + 配音 + BGM + loudnorm
    finalize(silent_mp4, subs_srt, voice_wav, bgm_path, total, bgm_vol, out_path, portrait, show_sub)

    # 9) 清理中间文件
    for f in [spec_path, silent_mp4, subs_srt]:
        if f.exists() and not keep_spec:
            try: f.unlink()
            except Exception: pass
    for ap in work.glob("_cue_*.mp3"):
        try: ap.unlink()
        except Exception: pass
    for ap in work.glob("_cv_cues.json"):
        try: ap.unlink()
        except Exception: pass
    for ap in work.glob("_cv_durations.json"):
        try: ap.unlink()
        except Exception: pass
    cvdir = work / "_cv"
    if cvdir.exists():
        try:
            import shutil as _sh
            _sh.rmtree(cvdir)
        except Exception:
            pass
    if voice_wav and voice_wav.exists():
        try: voice_wav.unlink()
        except Exception: pass

    print(f"\n✅ 完整成片：{out_path}")
    if not voice_available and use_tts:
        if tts_backend == "cosyvoice":
            print("   （当前为无声版：CosyVoice 合成未成功，检查 D:\\codex\\github\\CosyVoice 与 .venv）")
        else:
            print("   （当前为无声版：启动本机 Qwen3-TTS 18080 服务后，加 --tts 重跑本命令即可补配音）")


def srt_block(idx, start, end, text):
    def ts(t):
        h = int(t // 3600); m = int((t % 3600) // 60); s = int(t % 60); ms = int((t - int(t)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    return f"{idx}\n{ts(start)} --> {ts(end)}\n{text}\n"


def build_voice_track(cue_audio, cues, cue_durs, total, out_wav, work, mode):
    """把每页音频按各自的 at 延时对齐，混成一条与视频等长的配音轨。"""
    if mode == "t3":
        # 单段配音：直接放在开头
        src = [p for p in cue_audio if p][0]
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-af",
             f"apad=whole_dur={total:.2f}", "-c:a", "pcm_s16le", str(out_wav)],
            capture_output=True, text=True, check=True)
        return
    inputs = []
    filters = []
    ai = 0
    for i, p in enumerate(cue_audio):
        if not p:
            continue
        inputs += ["-i", str(p)]
        start_ms = int(cues[i]["at"] * 1000)
        filters.append(f"[{ai}:a]adelay=delays={start_ms}:all=1[a{i}]")
        ai += 1
    if not inputs:
        raise SystemExit("没有可用的配音音频")
    amix = "".join(f"[a{i}]" for i in range(len(filters)))
    filters.append(f"{amix}amix=inputs={len(filters)}:normalize=0[m]")
    filters.append(f"[m]apad=whole_dur={total:.2f}[outa]")
    fc = ";".join(filters)
    # 只处理音频，不接视频
    cmd = ["ffmpeg", "-y"] + inputs + ["-filter_complex", fc, "-map", "[outa]",
            "-c:a", "pcm_s16le", str(out_wav)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("配音轨合成失败：\n" + r.stderr[-800:])


def pick_bgm_file(mood):
    try:
        r = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "pick_bgm.py"),
             "--mood", mood, "--path-only"],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[0]
    except Exception:
        pass
    return None


def finalize(silent_mp4, subs_srt, voice_wav, bgm_path, total, bgm_vol, out_path, portrait, show_sub=True):
    work = subs_srt.parent
    out_abs = Path(out_path).resolve()  # 绝对路径：cd 到 work 后仍能正确落盘
    subs_rel = subs_srt.name  # 相对名：避开 Windows 盘符冒号在 subtitles filter 里的解析问题
    # ffmpeg subtitles filter 默认 ASS PlayRes 是 384x288，字号/边距要按这个坐标系给
    font_size = 8 if portrait else 11      # ≈53px@1920 / 41px@1080
    margin_v = 12 if portrait else 16      # ≈80px@1920 / 60px@1080
    sub_style = (
        f"FontName=Microsoft YaHei,FontSize={font_size},"
        f"PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2,Shadow=1,"
        f"Alignment=2,MarginV={margin_v}"
    )
    vfilter = (f"[0:v]subtitles='{subs_rel}':force_style='{sub_style}'[v]"
               if show_sub else "[0:v]copy[v]")

    cmd = ["ffmpeg", "-y", "-i", str(silent_mp4)]
    af = []

    # 音频输入索引：视频=0，配音=1，BGM=2（仅当各自存在）
    voice_idx = None
    bgm_idx = None
    audio_count = 1  # 视频占 0
    if voice_wav:
        cmd += ["-i", str(voice_wav)]; voice_idx = audio_count; audio_count += 1
    if bgm_path:
        cmd += ["-i", str(bgm_path)]; bgm_idx = audio_count; audio_count += 1

    if voice_wav or bgm_path:
        if bgm_path:
            # BGM 降音量 + 首尾淡入淡出
            af.append(f"[{bgm_idx}:a]volume={bgm_vol},afade=in:st=0:d=1.5,"
                      f"afade=out:st={max(0,total-2):.2f}:d=2[b]")
            parts = []
            if voice_wav:
                parts.append(f"[{voice_idx}:a]")
            parts.append("[b]")
            # duration=first：以第一路（配音轨，已 apad 到 total）为准，防止 BGM 比视频长把音频撑长
            af.append(f"{''.join(parts)}amix=inputs={len(parts)}:normalize=0:duration=first[m]")
        else:
            af.append(f"[{voice_idx}:a]amix=inputs=1:normalize=0[m]")
        af.append("[m]loudnorm=I=-16:TP=-1.5:LRA=11[outa]")
        fc = ";".join(af)
        # -t total：硬性兜底，保证输出音画等长（BGM 过长/仅 BGM 时不会被撑长）
        cmd += ["-filter_complex", f"{vfilter};{fc}", "-map", "[v]", "-map", "[outa]",
                "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                "-t", f"{total:.2f}", str(out_abs)]
    else:
        # 纯画面 + 字幕，无音轨
        vf = (f"subtitles='{subs_rel}':force_style='{sub_style}'" if show_sub else "null")
        cmd += ["-vf", vf,
                "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(out_abs)]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(work))
    if r.returncode != 0:
        raise SystemExit("最终合成失败：\n" + r.stderr[-1200:])


def main():
    ap = argparse.ArgumentParser(description="文案 → 完整艺术解说短视频（画面+配音+字幕+BGM）")
    ap.add_argument("input", help="文案文件（.md/.txt）")
    ap.add_argument("--out", required=True, help="输出 mp4 路径")
    ap.add_argument("--mode", default="auto", choices=["auto", "y5", "t3", "t2"])
    ap.add_argument("--theme", default="navy", help="色板：" + "/".join(THEMES))
    ap.add_argument("--portrait", action="store_true", help="竖屏 1080x1920")
    ap.add_argument("--landscape", action="store_true", help="横屏 1920x1080")
    ap.add_argument("--engine", default=DEFAULT_ENGINE, help="huashu-art-motion/scripts 目录")
    ap.add_argument("--voice", default=DEFAULT_VOICE, help="Qwen3-TTS 音色（默认 Vivian）")
    ap.add_argument("--tts-url", default=DEFAULT_TTS_URL, help="本机 Qwen3-TTS 接口")
    ap.add_argument("--tts", default="auto", choices=["auto", "cosyvoice", "qwen3", "none"],
                   help="配音后端：auto=优先本地CosyVoice再退回Qwen3；cosyvoice=本地；qwen3=18080服务；none=无声")
    ap.add_argument("--cosyvoice-speaker", default=COSYVOICE_DEFAULT_SPEAKER,
                   help="CosyVoice 内置说话人（默认 中文女）")
    ap.add_argument("--no-tts", action="store_true", help="跳过配音（只出画面+字幕）")
    ap.add_argument("--no-bgm", action="store_true", help="不要背景音乐")
    ap.add_argument("--no-sub", action="store_true", help="强制不烧字幕")
    ap.add_argument("--sub", action="store_true", help="强制烧字幕（默认：y5/t2 画面含文字不烧，t3 图表烧旁白）")
    ap.add_argument("--bgm-mood", default="auto", help="BGM 情绪（轻快/扎心/治愈/励志/大气/起伏，默认 auto）")
    ap.add_argument("--bgm-vol", type=float, default=BGM_VOL, help="BGM 音量（相对配音，默认 0.025）")
    ap.add_argument("--keep-spec", action="store_true", help="保留中间 spec.json")
    args = ap.parse_args()

    portrait = args.portrait and not args.landscape
    tts_backend = "none" if args.no_tts else choose_tts_backend(args.tts)
    # 字幕默认策略：y5/t2 画面已含文字 → 不烧；t3 图表 → 烧旁白；--sub/--no-sub 强制覆盖
    if args.no_sub:
        show_sub = False
    elif args.sub:
        show_sub = True
    else:
        mode_for_sub = args.mode if args.mode != "auto" else auto_mode(
            Path(args.input).read_text(encoding="utf-8").splitlines())
        show_sub = mode_for_sub not in ("y5", "t2")
    run(args.input, args.out, args.mode, args.theme, portrait, args.engine,
        args.voice, args.tts_url, tts_backend, args.cosyvoice_speaker, not args.no_tts, not args.no_bgm,
        args.bgm_mood, args.bgm_vol, args.keep_spec, show_sub)


if __name__ == "__main__":
    main()

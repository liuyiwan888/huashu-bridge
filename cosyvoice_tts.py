#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cosyvoice_tts.py —— 本地 CosyVoice 配音子程序（一次加载模型，批量合成）

由 make_complete.py 调用，专为「文案 → 视频」流水线设计：
  · 本地推理，不依赖任何常驻服务（根治 Qwen3-TTS@18080 没起就没人声的短板）
  · 用 CosyVoice 内置说话人（默认「中文女」），走 inference_sft（音质最好、rtf≈2）
  · 一次性加载模型，为整条文案的每一段口播合成 wav 并回报真实时长

用法：
  python cosyvoice_tts.py --cosyvoice-root D:/codex/github/CosyVoice \
      --speaker 中文女 --cues cues.json --outdir <dir> --durations durations.json

  cues.json      : JSON 数组，每个元素是一段口播文本（字符串）
  durations.json : 输出，数组，每个元素是该段配音的秒数（空文本=0.0）

注意（踩坑点，已在 infographic-explainer-video 验证）：
  · 必须用 CosyVoice 自带的 .venv python 跑，绝不用 uv run（会损坏 flash_attn）
  · 绝不用 inference_zero_shot（短中文句会触发快进退化）
  · 输出 22050Hz 单声道 wav
"""
import argparse
import json
import os
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cosyvoice-root", required=True, help="CosyVoice 仓库根目录")
    ap.add_argument("--speaker", default="中文女", help="内置说话人（默认 中文女）")
    ap.add_argument("--cues", required=True, help="JSON 数组文件：每段口播文本")
    ap.add_argument("--outdir", required=True, help="输出 wav 目录")
    ap.add_argument("--durations", required=True, help="输出 durations.json 路径")
    args = ap.parse_args()

    root = Path(args.cosyvoice_root).resolve()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    texts = json.loads(Path(args.cues).read_text(encoding="utf-8"))

    # 与 infographic skill 完全一致的环境装配：把 Matcha-TTS 与仓库根加进 sys.path
    matcha = root / "third_party" / "Matcha-TTS"
    os.environ.setdefault("PYTHONPATH", str(matcha))
    sys.path[:0] = [str(matcha), str(root)]

    import torch
    import torchaudio
    from cosyvoice.cli.cosyvoice import AutoModel

    model_dir = root / "pretrained_models" / "CosyVoice-300M-SFT"
    print(f">> CosyVoice 加载模型：{model_dir}")
    model = AutoModel(model_dir=str(model_dir))
    sr = model.sample_rate

    available = list(getattr(model.frontend, "spk2info", {}) or {})
    if available and args.speaker not in available:
        raise ValueError(f"未知内置音色 {args.speaker!r}；可用：{available}")

    durations = []
    for i, text in enumerate(texts):
        text = (text or "").strip()
        if not text:
            durations.append(0.0)
            continue
        # 内置说话人：模型训练时就带这些声音，无需任何 prompt
        chunks = [
            item["tts_speech"].cpu()
            for item in model.inference_sft(text, args.speaker, stream=False, speed=1.0)
        ]
        if not chunks:
            durations.append(0.0)
            continue
        wav = torch.cat(chunks, dim=-1)
        out = outdir / f"cue_{i:03d}.wav"
        torchaudio.save(str(out), wav, sr)
        dur = wav.shape[-1] / sr
        durations.append(dur)
        print(f"   [cosyvoice {i+1}/{len(texts)}] {text[:20]}…  {dur:.1f}s")

    Path(args.durations).write_text(json.dumps(durations, ensure_ascii=False), encoding="utf-8")
    print(">> CosyVoice 合成完成")


if __name__ == "__main__":
    main()

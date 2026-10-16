#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pick_bgm.py —— huashu-bridge 专用 BGM 选曲器
============================================
复刻 baokuan-to-timepress-video 流水线的选曲硬规则：
  - 只从「D:\\项目资料合集\\背景音乐\\抖音爆款\\」选曲（120+ 首，纯音乐/器乐优先）
  - 情绪池内轮转 + 排除最近 20 条（轮完一整池才会再见同一首）

用法：
  python pick_bgm.py --mood 轻快            # 选 1 首并写台账（推荐）
  python pick_bgm.py --mood 轻快 --count 3   # 主选 + 2 备选
  python pick_bgm.py --path-only             # 只输出路径（供 shell 捕获，Windows D:/ 正斜杠）
  python pick_bgm.py --list                  # 看候选纯音乐池
  python pick_bgm.py --history               # 看最近用过的 20 首
"""
import argparse
import json
import random
from pathlib import Path

BGM_DIR = Path(r"D:/项目资料合集/背景音乐/抖音爆款")
STATE = Path(__file__).resolve().parent / "bgm_state.json"

# 情绪关键词池（按文件名模糊匹配；命中即归入该池）
MOOD_KEYWORDS = {
    "轻快": ["轻快", "happy", "up", "story", "little", "cute", "joy", "bright", "活泼"],
    "扎心": ["扎心", "sad", "tear", "lonely", "cry", "伤", "痛", "melanch", "心碎"],
    "治愈": ["治愈", "heal", "warm", "calm", "quiet", "slow", "温柔", "安", "舒缓"],
    "励志": ["励志", "inspir", "motivat", "rise", "dream", "fight", "逆", "拼搏"],
    "大气": ["大气", "epic", "cinematic", "movie", "grand", "hero", "升", "磅礴"],
    "起伏": ["起伏", "drama", "turn", "emotion", "故事", "转折"],
}


def list_pure():
    """纯音乐优先：文件名含『纯音乐』的全部纳入；没有则退回全库。"""
    files = [p for p in BGM_DIR.glob("*.mp3") if "纯音乐" in p.name]
    if not files:
        files = list(BGM_DIR.glob("*.mp3"))
    return files


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"history": []}


def save_state(s):
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def pick(mood="auto", count=1):
    s = load_state()
    recent = set(s.get("history", [])[-20:])
    pool = list_pure()
    if mood != "auto":
        kw = MOOD_KEYWORDS.get(mood, [])
        if kw:
            matched = [p for p in pool if any(k.lower() in p.name.lower() for k in kw)]
            if matched:
                pool = matched
    # 排除最近 20 条；池子都用过了就重置
    candidates = [p for p in pool if p.name not in recent]
    if not candidates:
        candidates = pool
    chosen = []
    for _ in range(min(count, len(candidates))):
        p = random.choice(candidates)
        candidates.remove(p)
        chosen.append(p)
    # 写台账（保留全量历史，去重只看末 20）
    for p in chosen:
        s.setdefault("history", []).append(p.name)
    save_state(s)
    return chosen


def main():
    ap = argparse.ArgumentParser(description="huashu-bridge BGM 选曲器")
    ap.add_argument("--mood", default="auto", help="情绪：轻快/扎心/治愈/励志/大气/起伏，默认 auto 全库纯音乐轮转")
    ap.add_argument("--count", type=int, default=1, help="选几首（主选+备选）")
    ap.add_argument("--list", action="store_true", help="列出候选纯音乐池")
    ap.add_argument("--history", action="store_true", help="看最近用过的 20 首")
    ap.add_argument("--path-only", action="store_true", help="只输出 Windows 正斜杠路径（供 shell 捕获）")
    args = ap.parse_args()

    if args.list:
        pool = list_pure()
        print(f"候选纯音乐 {len(pool)} 首（库：{BGM_DIR}）：")
        for p in pool[:60]:
            print("  ", p.name)
        return
    if args.history:
        s = load_state()
        for h in s.get("history", [])[-20:]:
            print(h)
        return

    chosen = pick(args.mood, args.count)
    if args.path_only:
        for p in chosen:
            print(p.as_posix())
    else:
        for p in chosen:
            print(p.name)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_video.py —— 「文案 → 艺术动画」一键桥
============================================
把一份人话文案变成 huashu-art-motion 的可渲染 spec，并调用它的 render.py 直接出 MP4。

依赖：
  - 已克隆的 huashu-art-motion 仓库（默认 ../huashu-art-motion，可用 --engine 指定）
  - uv + ffmpeg + Playwright Chromium（首次需 `uv run --with playwright playwright install chromium`）

用法：
  python make_video.py 文案.md --out 成片/清单.mp4
  python make_video.py 文案.md --mode t3 --out 成片/图表.mp4
  python make_video.py 文案.md --mode t2 --out 成片/功能.mp4
  python make_video.py 文案.md --mode auto --portrait --theme navy --out 成片/竖屏.mp4

文案写法（最省事，默认 y5 动态文字）：
  每行 = 一页。
  行首是数字（如 "87.5% 一次跑通的比例"）→ 自动识别成大数字页。
  行内用 "：" 或 ":" 分隔主词与辅句（如 "慢：每次只改一处"）。
  列表项（- / • / * 开头）→ 自动排成带序号的清单页。
  # 开头 → 这一页的标签（左上角小字）。

t3 图表模式文案写法：
  title: 三家研发投入
  unit: 2024 年，亿元
  source: 来源：示意数据
  chart: bar            # bar | line
  甲,120
  乙,86
  丙,43

t2 发布会模式文案写法：
  title: 产品名
  accent: #3d6bff
  语音输入 | 说完就转成文字
  长期记忆 | 记得你上次说过什么
"""
import argparse, json, re, subprocess, sys, shutil
from pathlib import Path

# ---------- 引擎路径 ----------
def _default_engine():
    """定位 huashu-art-motion/scripts：
    - 仓库内 submodule（huashu-bridge/huashu-art-motion，git clone --recurse-submodules）
    - 同级目录（huashu-bridge 与 huashu-art-motion 并列，本地开发布局）
    """
    here = Path(__file__).resolve().parent
    for base in (here, here.parent):
        eng = base / "huashu-art-motion" / "scripts"
        if (eng / "engine" / "render.py").exists():
            return str(eng)
    return str(here.parent / "huashu-art-motion" / "scripts")


DEFAULT_ENGINE = _default_engine()

THEMES = ["paper", "poster", "ink", "navy", "bauhaus", "snow", "wood", "chalk"]

NUM_RE = re.compile(r"^\s*([\d][\d.,]*)\s*([%倍亿万千元$¥％\s]*)")

# ---------- 各语法：文案 -> cues ----------
def parse_y5(lines, portrait):
    """动态文字：一行一页。自动识别数字页 / 清单页。"""
    cues = []
    label = None
    list_idx = 0
    for raw in lines:
        s = raw.rstrip("\n")
        if not s.strip():
            continue
        if s.lstrip().startswith("#"):
            label = s.lstrip("# ").strip()
            continue
        # 列表项
        m = re.match(r"^\s*[-•*]\s+(.*)$", s)
        if m:
            list_idx += 1
            text, sub = split_text(m.group(1))
            cues.append({"at": 0, "kind": "point", "text": text, "sub": sub,
                         "data": {"label": f"{list_idx:02d}"}})
            continue
        # 数字页：行首是数字
        nm = NUM_RE.match(s)
        if nm:
            val_s, unit = nm.group(1), nm.group(2).strip()
            rest = s[nm.end():].strip(" ：:")
            val = float(val_s.replace(",", "")) if "." in val_s else int(val_s.replace(",", ""))
            suffix = unit if unit else ""
            # 把剩余文字当说明
            cues.append({"at": 0, "kind": "number", "sub": rest or None,
                         "data": {"value": val, "suffix": suffix}})
            continue
        # 普通金句页
        text, sub = split_text(s)
        c = {"at": 0, "kind": "point", "text": text}
        if sub:
            c["sub"] = sub
        if label:
            c.setdefault("data", {})["label"] = label
            label = None
        cues.append(c)
    # 自动排时间：第一页 0.7s 起，每页 2.2s
    step = 2.0 if len(cues) > 6 else 2.3
    for i, c in enumerate(cues):
        c["at"] = round(0.7 + i * step, 2)
    duration = round(cues[-1]["at"] + step + 0.4, 2)
    return cues, duration

def parse_t3(lines):
    """财经图表：表头 + 数据行。"""
    data = {"chart": "bar", "series": [], "decimals": 1}
    pending = []
    for raw in lines:
        s = raw.rstrip("\n").strip()
        if not s:
            continue
        if s.startswith("title:"):
            data["title"] = s[6:].strip()
        elif s.startswith("unit:"):
            data["unit"] = s[5:].strip()
        elif s.startswith("source:"):
            data["source"] = s[7:].strip()
        elif s.startswith("chart:"):
            data["chart"] = s[6:].strip()
        else:
            pending.append(s)
    # 数据行：label,value
    for row in pending:
        if "," not in row and "\t" not in row:
            continue
        parts = re.split(r"[,，\t]", row)
        if len(parts) >= 2:
            lbl = parts[0].strip()
            try:
                v = float(parts[1].strip().replace(",", ""))
            except ValueError:
                continue
            data["series"].append({"label": lbl, "value": v})
    if not data["series"]:
        raise SystemExit("t3 模式没解析到数据行，请按 label,value 写几行")
    # 高亮最大值
    max_i = max(range(len(data["series"])), key=lambda i: data["series"][i]["value"])
    data["highlight"] = {"index": max_i, "text": "最高"}
    # cues：坐标系先出，数据长出，最后高亮
    cues = [{"at": 0.3, "kind": data["chart"]}]
    cues.append({"at": round(0.3 + 2.4, 2), "kind": "highlight", "data": {"index": max_i}, "text": "最高"})
    duration = 6.0
    return data, cues, duration

def parse_t2(lines):
    """发布会：表头 + 功能卡。"""
    data = {"accent": "#3d6bff"}
    cards = []
    for raw in lines:
        s = raw.rstrip("\n").strip()
        if not s:
            continue
        if s.startswith("title:"):
            data["title"] = s[6:].strip()
        elif s.startswith("subtitle:"):
            data["subtitle"] = s[10:].strip()
        elif s.startswith("accent:"):
            data["accent"] = s[7:].strip()
        elif s.startswith("eyebrow:"):
            data["eyebrow"] = s[8:].strip()
        else:
            parts = re.split(r"\s*\|\s*", s)
            text = parts[0].strip()
            sub = parts[1].strip() if len(parts) > 1 else ""
            cards.append((text, sub))
    if not cards:
        raise SystemExit("t2 模式没解析到功能卡，请用 '名称 | 说明' 写几行")
    cues = []
    step = 2.0
    for i, (t, sub) in enumerate(cards):
        c = {"at": round(0.9 + i * step, 2), "kind": "card", "text": t}
        if sub:
            c["sub"] = sub
        cues.append(c)
    duration = round(cues[-1]["at"] + step + 0.4, 2)
    return data, cues, duration

def split_text(s):
    """'主词：辅句' -> (主词, 辅句)。"""
    for sep in ["：", ":"]:
        if sep in s:
            a, b = s.split(sep, 1)
            return a.strip(), b.strip()
    return s.strip(), ""

def auto_mode(lines):
    """极简启发式：有 data,value 表格 → t3；否则 y5。"""
    has_table = any(re.match(r"^[^:]+[,，\t]\s*[\d]", l) and not l.startswith(("title:", "unit:", "source:", "chart:")) for l in lines)
    return "t3" if has_table else "y5"

# ---------- spec 组装 ----------
def build_spec(mode, lines, theme, portrait, fps=30):
    if mode == "y5":
        cues, duration = parse_y5(lines, portrait)
        spec = {"grammar": "y5_kinetic_type", "duration": duration, "fps": fps,
                "width": 1080 if portrait else 1920, "height": 1920 if portrait else 1080,
                "theme": theme, "cues": cues}
    elif mode == "t3":
        data, cues, duration = parse_t3(lines)
        spec = {"grammar": "t3_finance_chart", "duration": duration, "fps": fps,
                "width": 1080 if portrait else 1920, "height": 1920 if portrait else 1080,
                "theme": theme, "data": data, "cues": cues}
    elif mode == "t2":
        data, cues, duration = parse_t2(lines)
        spec = {"grammar": "t2_keynote_ui", "duration": duration, "fps": fps,
                "width": 1080 if portrait else 1920, "height": 1920 if portrait else 1080,
                "theme": theme, "data": data, "cues": cues}
    else:
        raise SystemExit(f"不支持的 mode: {mode}")
    # 竖屏给字幕留白（若后续要烧字幕）
    if portrait:
        spec["safe"] = {"top": 0, "bottom": 0}
    return spec

# ---------- 渲染 ----------
def render(spec_path, out_path, engine, portrait):
    render_py = Path(engine) / "engine" / "render.py"
    if not render_py.exists():
        raise SystemExit(f"找不到 render.py：{render_py}\n请用 --engine 指定 huashu-art-motion/scripts 目录")
    # 必须传绝对路径：render.py 在 engine 目录下运行，相对路径会解析错
    spec_abs = spec_path.resolve()
    out_abs = out_path.resolve()
    cmd = ["uv", "run", "--with", "playwright", "python", str(render_py),
           "--spec", str(spec_abs), "--out", str(out_abs)]
    if portrait:
        cmd += ["--width", "1080", "--height", "1920"]
    print(">> 渲染中：", " ".join(cmd))
    r = subprocess.run(cmd, cwd=str(engine))
    if r.returncode != 0:
        raise SystemExit(f"render.py 退出码 {r.returncode}，渲染失败（见上方报错）")
    return out_path

# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(description="文案 → 艺术动画 MP4 一键桥")
    ap.add_argument("input", help="文案文件（.md/.txt）")
    ap.add_argument("--out", required=True, help="输出 mp4 路径")
    ap.add_argument("--mode", default="auto", choices=["auto", "y5", "t3", "t2", "t1"])
    ap.add_argument("--theme", default="navy", help="色板：" + "/".join(THEMES))
    ap.add_argument("--portrait", action="store_true", help="竖屏 1080x1920")
    ap.add_argument("--landscape", action="store_true", help="横屏 1920x1080")
    ap.add_argument("--engine", default=DEFAULT_ENGINE, help="huashu-art-motion/scripts 目录")
    ap.add_argument("--keep-spec", action="store_true", help="保留中间 spec.json")
    args = ap.parse_args()

    portrait = args.portrait and not args.landscape
    lines = Path(args.input).read_text(encoding="utf-8").splitlines()
    mode = auto_mode(lines) if args.mode == "auto" else args.mode
    print(f">> 语法判定：{mode}  |  画幅：{'竖屏' if portrait else '横屏'}  |  色板：{args.theme}")

    spec = build_spec(mode, lines, args.theme, portrait)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path = out_path.parent / (out_path.stem + ".spec.json")
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f">> spec 已生成：{spec_path}")

    render(spec_path, out_path, args.engine, portrait)
    if not args.keep_spec:
        # 保留 spec 便于排查，这里不删；如需清理手动删
        pass
    print(f"\n✅ 成片：{out_path}")

if __name__ == "__main__":
    main()

# huashu-bridge

> 把一份「人话文案」一键变成 **高级感美术风格的解说 / 动态图表短视频**。

`huashu-bridge` 是一个桥接脚本集合：在 [huashu-art-motion](https://github.com/alchaincyf/huashu-art-motion)（35 套美术风格 + 9 种解说语法的渲染引擎）之上，补齐「文案 → 完整成片」的后半段——本地配音、字幕、BGM、响度归一，直接产出可发布的 mp4。

你只需要写一份 `.md` 文案，剩下的交给它。

---

## ✨ 特性

- **文案即脚本**：普通段落 = 一页动态大字；`# 标题` = 左上角标签；`- 项目` = 清单页；行首数字（如 `① 12%`） = 大数字页。
- **自动选语法**：清单/金句自动走 `y5 动态大字`，数字对比走 `t3 财经图表`，功能点走 `t2 发布会卡`。
- **本地配音优先**：默认本地 **CosyVoice**（内置「中文女」音色，本地推理、不依赖常驻服务）；找不到时自动退回 **Qwen3-TTS** 私有服务；可一键关闭。
- **智能字幕**：画面已含文字的风格（y5 / t2）默认不烧字幕，避免与画面大字重复；图表风格（t3）默认烧无标点旁白字幕。
- **BGM 自动轮转**：从「抖音爆款」曲库按情绪池轮转选曲，配音 2.5% 音量 + 首尾淡入淡出，并带选用台账避免连发同曲。
- **响度归一**：`loudnorm I=-16`，与主流短视频流水线一致。

---

## 🧰 环境依赖

| 依赖 | 用途 | 备注 |
| --- | --- | --- |
| Python 3.10+ | 运行脚本 | |
| `uv` | 拉起渲染引擎（自动装 playwright） | `pip install uv` |
| ffmpeg | 音视频合成 / 响度归一 | 需在 PATH |
| [huashu-art-motion](https://github.com/alchaincyf/huashu-art-motion) | 画面渲染引擎（**上游依赖**） | 克隆为同级目录，见下方安装 |
| CosyVoice（可选） | 本地配音 | 默认后端，见下方配置 |
| Qwen3-TTS 服务（可选） | 配音回退 | 默认 `http://127.0.0.1:18080` |
| BGM 曲库（可选） | 背景音乐 | 默认 `抖音爆款` 目录，见下方配置 |

---

## 📦 安装

```bash
# 1. 克隆本桥接仓库
git clone https://github.com/liuyiwan888/huashu-bridge.git
cd huashu-bridge

# 2. 克隆上游渲染引擎为「同级目录」（关键！）
git clone https://github.com/alchaincyf/huashu-art-motion.git ../huashu-art-motion
```

> 引擎默认搜索顺序：先找仓库内的 `huashu-art-motion/`（若你用 submodule），再找**同级目录** `../huashu-art-motion`。
> 也可以用 `--engine /绝对路径/huashu-art-motion/scripts` 手动指定。

---

## 🚀 快速开始

```bash
# 完整版：画面 + 本地 CosyVoice 配音 + 智能字幕 + BGM
python make_complete.py examples/两个大机会.md --out out.mp4

# 竖屏 + 指定主题
python make_complete.py examples/复盘_专注流量.md --portrait --theme snow --out out.mp4

# 只要画面（先预览排版，不出声）
python make_complete.py examples/清单金句.md --no-tts --out preview.mp4
```

跑完会在 `--out` 指定的路径生成成片。

---

## ⚙️ 常用参数（`make_complete.py`）

| 参数 | 说明 |
| --- | --- |
| `input` | 文案 `.md` 路径（位置参数） |
| `--out` | 输出 mp4 路径（默认 `成片/完整.mp4`） |
| `--mode {y5,t3,t2,auto}` | 强制解说语法，默认 `auto` 按内容自动选 |
| `--theme` | 美术主题，如 `paper` / `snow` / `wine` 等（见引擎 THEMES） |
| `--portrait` | 输出竖屏 9:16 |
| `--tts {auto,cosyvoice,qwen3,none}` | 配音后端，默认 `auto`（优先本地 CosyVoice） |
| `--cosyvoice-speaker` | CosyVoice 音色，默认 `中文女` |
| `--voice` | Qwen3-TTS 音色名 |
| `--no-bgm` | 不要背景音乐 |
| `--sub` / `--no-sub` | 强制开 / 关字幕（默认按风格自动判定） |
| `--engine` | 指定 huashu-art-motion/scripts 目录 |

---

## 🔧 本地路径配置（改代码或环境变量）

以下路径是**作者本机默认值**，公开仓库不含这些资源，按需自行配置：

- **CosyVoice 根目录**：`make_complete.py` 中的 `COSYVOICE_ROOT`（默认 `D:\codex\github\CosyVoice`）。
- **BGM 曲库**：`pick_bgm.py` 中的曲库目录（默认「抖音爆款」本地目录）。
- **Qwen3-TTS 服务**：`make_complete.py` 中的 `DEFAULT_TTS_URL`（默认 `http://127.0.0.1:18080/v1/audio/speech`）。

三者任一缺失都不会中断流程：CosyVoice 缺失→退回 Qwen3；Qwen3 也缺失→跳过配音；BGM 缺失→不出 BGM。

---

## 📁 目录结构

```
huashu-bridge/
├── make_video.py        # 文案 .md → spec.json → 调引擎渲染画面轨
├── make_complete.py     # 一键完整成片（画面 + 配音 + 字幕 + BGM）
├── cosyvoice_tts.py     # CosyVoice 批量合成脚本
├── pick_bgm.py          # BGM 曲库轮转选曲
├── bgm_state.json       # BGM 选用台账（运行时生成，已 gitignore）
├── examples/            # 示例文案（.md 源文件）
├── 成片/                # 本地成片（已 gitignore）
└── publish/             # 发布物料（已 gitignore）
```

---

## 📄 许可

- 本仓库（桥接代码）：**MIT** —— 见 [LICENSE](LICENSE)。
- 渲染引擎 `huashu-art-motion` 为其原作者（alchaincyf）所有，请遵循其仓库许可。

---

Made with huashu-bridge · 文案进，成片出。

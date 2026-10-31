"""
評価結果（eval_policy.py の出力）からレポート・動画用の図を作る → outputs/figures/

  python make_figures.py

  fig_data_amount.png     パイロット（4 エピソード）と本番（36 エピソード）の成功率（同じ 50 個の配置）
  fig_generalization.png  学習範囲からのはみ出し × 成功率（95% 信頼区間つき）
  strip_<名前>.png        代表的な試行の GIF から 5 コマを並べた画像（gifs/ に元の GIF もコピー）
"""

import json
import math
import re
import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from PIL import Image  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
EVAL_DIR = BASE_DIR / "outputs" / "eval"
OUT_DIR = BASE_DIR / "outputs" / "figures"
FONT = Path("/mnt/c/Windows/Fonts/NotoSansJP-VF.ttf")  # WSL から Windows の日本語フォントを使う

INK, MUTED, GRID, ACCENT, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#2a78d6", "#fcfcfb"
OLD_LOGS = {"act_v1_030000_n50": "eval_act_v1_n50.log"}  # results.json を出す前に評価したもののログ


def wilson(k, n, z=1.96):
    """成功率の 95% 信頼区間（Wilson）[%]"""
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return 100 * (c - h), 100 * (c + h)


def load(name):
    """(成功数, 試行数, 試行のリスト or None)。results.json がない古い評価はログから数える"""
    path = EVAL_DIR / name / "results.json"
    if path.exists():
        r = json.loads(path.read_text())
        return r["success"], r["episodes"], r["trials"]
    log = (BASE_DIR / "outputs" / OLD_LOGS.get(name, f"eval_{name}.log")).read_text()
    m = re.search(r"成功率: (\d+)/(\d+)", log)
    return int(m.group(1)), int(m.group(2)), None


def setup_style():
    if FONT.exists():
        font_manager.fontManager.addfont(str(FONT))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(FONT)).get_name()
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED,
        "text.color": INK, "font.size": 11, "axes.spines.top": False, "axes.spines.right": False,
    })


def rate_axis(ax):
    ax.set_ylim(0, 105)
    ax.set_yticks(range(0, 101, 20))
    ax.set_ylabel("成功率 [%]")
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def fig_data_amount():
    rows = [("パイロット\n4 エピソード\n4,000 ステップ", *load("act_pilot_n50")[:2]),
            ("本番\n36 エピソード\n30,000 ステップ", *load("act_v1_030000_n50")[:2])]
    fig, ax = plt.subplots(figsize=(5.6, 4.3))
    for i, (label, k, n) in enumerate(rows):
        rate, (lo, hi) = 100 * k / n, wilson(k, n)
        ax.bar(i, rate, width=0.5, color=ACCENT, zorder=2)
        ax.errorbar(i, rate, yerr=[[rate - lo], [hi - rate]], color=INK, capsize=6, linewidth=1.2, zorder=3)
        ax.text(i, hi + 3, f"{k}/{n}（{rate:.0f}%）", ha="center", fontsize=11)
    ax.set_xticks(range(len(rows)), [r[0] for r in rows])
    ax.set_xlim(-0.6, len(rows) - 0.4)
    rate_axis(ax)
    ax.set_title("データを増やすと成功率は 0% → 96%（同じ 50 個の配置）", fontsize=12, loc="left")
    fig.text(0.01, 0.01, "誤差棒は 95% 信頼区間（Wilson）", fontsize=9, color=MUTED)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(OUT_DIR / "fig_data_amount.png", dpi=200)
    plt.close(fig)


def fig_generalization():
    conds = [(0, "act_v1_030000_n50"), (1, "gen_offset4cm"), (2, "gen_offset5cm"), (3, "gen_offset6cm")]
    pts = []
    for over, name in conds:
        k, n, _ = load(name)
        pts.append((over, 100 * k / n, *wilson(k, n), k, n))
    fig, ax = plt.subplots(figsize=(6.0, 4.0))
    ax.axvspan(-0.4, 0, color=GRID, alpha=0.6, zorder=0)
    ax.text(-0.36, 8, "学習範囲\n（±3 cm）", fontsize=9, color=MUTED, va="bottom")
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    ax.plot(xs, ys, color=ACCENT, linewidth=2, zorder=2)
    for over, rate, lo, hi, k, n in pts:
        ax.errorbar(over, rate, yerr=[[rate - lo], [hi - rate]], color=INK, capsize=5, linewidth=1.2, zorder=3)
        ax.plot(over, rate, "o", color=ACCENT, markersize=8, markeredgecolor=SURFACE, markeredgewidth=2, zorder=4)
        ax.text(over + 0.08, rate + 3, f"{k}/{n}", fontsize=10, color=INK)
    ax.set_xticks(xs, ["範囲内\n（±3 cm）", "1 cm 外\n（4 cm）", "2 cm 外\n（5 cm）", "3 cm 外\n（6 cm）"])
    ax.set_xlim(-0.4, 3.5)
    ax.set_xlabel("ブロックの位置：学習範囲からのはみ出し（中心からの距離）")
    rate_axis(ax)
    ax.set_title("1 cm 外までは 85% を保つが、2 cm 外で 15% に落ちる", fontsize=12, loc="left")
    fig.text(0.01, 0.01, "誤差棒は 95% 信頼区間（Wilson）。範囲内は 50 回、ほかは各 20 回", fontsize=9, color=MUTED)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(OUT_DIR / "fig_generalization.png", dpi=200)
    plt.close(fig)


def pick(name, success=None, lifted=None):
    """条件に合う最初の試行の GIF のパス（results.json がなければ GIF のファイル名で選ぶ）"""
    _, _, trials = load(name)
    if trials is None:
        tag = "success" if success else "fail"
        return next(iter(sorted((EVAL_DIR / name).glob(f"eval_*_{tag}.gif"))), None)
    for t in trials:
        if (success is None or t["success"] == success) and (lifted is None or t["lifted"] == lifted):
            tag = "success" if t["success"] else "fail"
            return EVAL_DIR / name / f"eval_{t['episode']:02d}_{tag}.gif"
    return None


def strip(gif_path, out_path, n=5):
    """GIF から等間隔に n コマ取り出して横に並べる"""
    gif = Image.open(gif_path)
    frames = []
    for i in range(gif.n_frames):
        gif.seek(i)
        frames.append(gif.convert("RGB"))
    idx = [round(j * (len(frames) - 1) / (n - 1)) for j in range(n)]
    w, h = frames[0].size
    out = Image.new("RGB", (w * n + 4 * (n - 1), h), SURFACE)
    for j, i in enumerate(idx):
        out.paste(frames[i], (j * (w + 4), 0))
    out.save(out_path)


def examples():
    gif_dir = OUT_DIR / "gifs"
    gif_dir.mkdir(exist_ok=True)
    chosen = {
        "inrange_success": pick("act_v1_030000_n50", success=True),
        "pilot_fail_lifted": pick("act_pilot_n50", success=False, lifted=True),
        "offset4cm_success": pick("gen_offset4cm", success=True),
        "offset5cm_fail_miss": pick("gen_offset5cm", success=False, lifted=False),
        "offset6cm_fail_miss": pick("gen_offset6cm", success=False, lifted=False),
        "yaw30-45_success": pick("gen_yaw30-45", success=True),
    }
    for label, path in chosen.items():
        if path is None:
            print(f"  {label}: 該当なし")
            continue
        shutil.copy(path, gif_dir / f"{label}.gif")
        strip(path, OUT_DIR / f"strip_{label}.png")
        print(f"  {label}: {path.relative_to(BASE_DIR)}")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    setup_style()
    fig_data_amount()
    fig_generalization()
    examples()
    print(f"→ {OUT_DIR}")


if __name__ == "__main__":
    main()

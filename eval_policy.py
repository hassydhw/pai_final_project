"""
学習した方策（lerobot-train の ACT など）を MuJoCo 上で動かし、Pick & Place の成功率を測る（ヘッドレス、EGL）

~/lerobot_env で実行する。記録時と同じ条件（アームはホーム姿勢で静止、ブロックはランダムに配置、
カメラ画像は render_episodes.py と同じ設定、30 fps で行動を data.ctrl に入れる）で試行し、
TaskMonitor の配置成功判定で採点する。後方カメラの動画を GIF で保存する。

  ~/lerobot_env/bin/python eval_policy.py outputs/train/act_pilot/checkpoints/last/pretrained_model
  ~/lerobot_env/bin/python eval_policy.py <チェックポイント> --episodes 10 --max-sec 40 --seed 0

汎化の確認（学習データの配置範囲 ±BLOCK_RANDOM_XY・±BLOCK_RANDOM_YAW の外にブロックを置く）:
  --offset-cm 5      初期位置から縦横どちらかに 5 cm ずれた正方形の周上（学習範囲の ±3 cm の外側 2 cm）
  --yaw-deg 30 45    向きの大きさを 30〜45° に（符号はランダム。位置は学習範囲内）
各試行の配置と結果は <出力先>/results.json に保存する。
"""

import argparse
import json
import time
from pathlib import Path

from render_episodes import CAMERAS, IMAGE_H, IMAGE_W, scene_option  # MUJOCO_GL=egl を先に設定する

import mujoco  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
from lerobot.policies.act.modeling_act import ACTPolicy  # noqa: E402
from lerobot.policies.factory import make_pre_post_processors  # noqa: E402
from PIL import Image  # noqa: E402

from recorder import JOINT_NAMES, RECORD_FPS  # noqa: E402
from teleop_server import HOME_TARGET, TaskMonitor, apply_control, load_model, reset_block  # noqa: E402

SETTLE_SEC = 1.0  # 開始前にアームをホーム姿勢で静止させる時間
GIF_FPS = 10
MAX_BLOCK_R = 0.235  # 汎化の確認で置くブロックの土台からの距離の上限 [m]（スクリプト制御で掴めるのは r=0.237 まで確認）


def place_block(model, data, rng, offset=None, yaw_range=None):
    """
    reset_block（学習データと同じ範囲のランダム配置）のあと、指定があれば範囲外に置き直す。
      offset    : 初期位置から縦横の大きい方が offset [m] になる正方形の周上に一様に置く（届かない位置は引き直す）
      yaw_range : (最小, 最大) [rad]。向きの大きさをこの範囲から選び、符号はランダム
    戻り値は初期位置からのずれ (dx, dy) [m] と向き [rad]
    """
    reset_block(model, data, rng)
    qadr = model.joint("red_block_joint").qposadr[0]
    home = model.key("home").qpos[qadr:qadr + 2]
    if offset:
        while True:
            side, u = divmod(rng.uniform(0, 8 * offset), 2 * offset)
            u -= offset
            dxy = np.array([(offset, u), (-u, offset), (-offset, -u), (u, -offset)][int(side)])
            if np.hypot(*(home + dxy)) <= MAX_BLOCK_R:
                break
        data.qpos[qadr:qadr + 2] = home + dxy
    if yaw_range is not None:
        yaw = rng.uniform(*yaw_range) * rng.choice([-1, 1])
        data.qpos[qadr + 3:qadr + 7] = [np.cos(yaw / 2), 0.0, 0.0, np.sin(yaw / 2)]
    mujoco.mj_forward(model, data)
    w, z = data.qpos[qadr + 3], data.qpos[qadr + 6]
    return data.qpos[qadr:qadr + 2] - home, 2 * np.arctan2(z, w)


def render_observation(renderer, model, data, qadr, option):
    """記録データと同じ形の観測（1 フレーム分、バッチ次元なし）"""
    obs = {"observation.state": torch.tensor(data.qpos[qadr], dtype=torch.float32)}
    frames = {}
    for name, camera in CAMERAS.items():
        renderer.update_scene(data, camera=camera, scene_option=option)
        frames[name] = renderer.render().copy()
        obs[f"observation.images.{name}"] = torch.from_numpy(frames[name]).permute(2, 0, 1).float() / 255.0
    return obs, frames


def run_episode(policy, preprocessor, postprocessor, renderer, rng, max_sec, offset=None, yaw_range=None):
    model, data = load_model()
    for _ in range(int(SETTLE_SEC / model.opt.timestep)):
        apply_control(model, data, HOME_TARGET, 0.0)
        mujoco.mj_step(model, data)
    dxy, yaw = place_block(model, data, rng, offset, yaw_range)
    monitor = TaskMonitor(model)
    qadr = [model.joint(n).qposadr[0] for n in JOINT_NAMES]
    option = scene_option()
    policy.reset()

    gif, success_t, t0 = [], None, data.time
    for k in range(int(max_sec * RECORD_FPS)):
        obs, frames = render_observation(renderer, model, data, qadr, option)
        if k % (RECORD_FPS // GIF_FPS) == 0:
            gif.append(frames["front"])
        with torch.inference_mode():
            action = postprocessor(policy.select_action(preprocessor(obs)))
        data.ctrl[:] = action.squeeze(0).cpu().numpy()
        next_t = t0 + (k + 1) / RECORD_FPS
        while data.time < next_t - 1e-9:
            mujoco.mj_step(model, data)
            if monitor.update(data, verbose=False) and success_t is None:
                success_t = data.time - t0
        if success_t is not None:
            break
    lifted = monitor.lifted or monitor.placed
    return success_t, lifted, gif, dxy, yaw


def main():
    parser = argparse.ArgumentParser(description="学習した方策を MuJoCo 上で評価する")
    parser.add_argument("checkpoint", type=Path, help="pretrained_model ディレクトリ")
    parser.add_argument("--episodes", type=int, default=10)
    parser.add_argument("--max-sec", type=float, default=40.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--out", type=Path, default=None, help="GIF の保存先（既定: outputs/eval/<チェックポイント名>）")
    parser.add_argument("--offset-cm", type=float, default=None, help="ブロックを初期位置からこの距離 [cm] の正方形の周上に置く")
    parser.add_argument("--yaw-deg", type=float, nargs=2, default=None, metavar=("MIN", "MAX"),
                        help="ブロックの向きの大きさ [deg] の範囲")
    args = parser.parse_args()
    offset = args.offset_cm / 100 if args.offset_cm else None
    yaw_range = np.radians(args.yaw_deg) if args.yaw_deg else None

    policy = ACTPolicy.from_pretrained(args.checkpoint)
    policy.config.device = args.device
    policy.to(args.device).eval()
    preprocessor, postprocessor = make_pre_post_processors(
        policy.config, pretrained_path=str(args.checkpoint),
        preprocessor_overrides={"device_processor": {"device": args.device}})

    out_dir = args.out or Path("outputs/eval") / args.checkpoint.resolve().parts[-4]
    out_dir.mkdir(parents=True, exist_ok=True)
    model, _ = load_model()
    renderer = mujoco.Renderer(model, IMAGE_H, IMAGE_W)  # 1 プロセスで 1 個を使い回す
    rng = np.random.default_rng(args.seed)

    results = []
    for ep in range(args.episodes):
        start = time.time()
        success_t, lifted, gif, dxy, yaw = run_episode(policy, preprocessor, postprocessor, renderer, rng,
                                                       args.max_sec, offset, yaw_range)
        results.append({"episode": ep, "dx": float(dxy[0]), "dy": float(dxy[1]), "yaw_deg": float(np.degrees(yaw)),
                        "success": success_t is not None, "lifted": bool(lifted), "time": success_t})
        path = out_dir / f"eval_{ep:02d}_{'success' if success_t is not None else 'fail'}.gif"
        Image.fromarray(gif[0]).save(path, save_all=True, append_images=[Image.fromarray(f) for f in gif[1:]],
                                     duration=1000 // GIF_FPS, loop=0)
        status = f"成功（{success_t:.1f} 秒）" if success_t is not None else ("失敗（持ち上げまで）" if lifted else "失敗")
        print(f"  試行 {ep:2d}: {status}  ずれ ({100 * dxy[0]:+.1f}, {100 * dxy[1]:+.1f}) cm  向き {np.degrees(yaw):+.0f}°"
              f"  [{time.time() - start:.0f} 秒で評価] → {path.name}", flush=True)
    renderer.close()
    n_ok = sum(r["success"] for r in results)
    print(f"\n成功率: {n_ok}/{len(results)}（{100 * n_ok / len(results):.0f}%）  GIF: {out_dir}")
    summary = {"checkpoint": str(args.checkpoint), "seed": args.seed, "offset_cm": args.offset_cm,
               "yaw_deg": args.yaw_deg, "success": n_ok, "episodes": len(results), "trials": results}
    (out_dir / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

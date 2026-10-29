"""
学習した方策（lerobot-train の ACT など）を MuJoCo 上で動かし、Pick & Place の成功率を測る（ヘッドレス、EGL）

~/lerobot_env で実行する。記録時と同じ条件（アームはホーム姿勢で静止、ブロックはランダムに配置、
カメラ画像は render_episodes.py と同じ設定、30 fps で行動を data.ctrl に入れる）で試行し、
TaskMonitor の配置成功判定で採点する。後方カメラの動画を GIF で保存する。

  ~/lerobot_env/bin/python eval_policy.py outputs/train/act_pilot/checkpoints/last/pretrained_model
  ~/lerobot_env/bin/python eval_policy.py <チェックポイント> --episodes 10 --max-sec 40 --seed 0
"""

import argparse
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


def render_observation(renderer, model, data, qadr, option):
    """記録データと同じ形の観測（1 フレーム分、バッチ次元なし）"""
    obs = {"observation.state": torch.tensor(data.qpos[qadr], dtype=torch.float32)}
    frames = {}
    for name, camera in CAMERAS.items():
        renderer.update_scene(data, camera=camera, scene_option=option)
        frames[name] = renderer.render().copy()
        obs[f"observation.images.{name}"] = torch.from_numpy(frames[name]).permute(2, 0, 1).float() / 255.0
    return obs, frames


def run_episode(policy, preprocessor, postprocessor, renderer, rng, max_sec):
    model, data = load_model()
    for _ in range(int(SETTLE_SEC / model.opt.timestep)):
        apply_control(model, data, HOME_TARGET, 0.0)
        mujoco.mj_step(model, data)
    reset_block(model, data, rng)
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
    return success_t, lifted, gif


def main():
    parser = argparse.ArgumentParser(description="学習した方策を MuJoCo 上で評価する")
    parser.add_argument("checkpoint", type=Path, help="pretrained_model ディレクトリ")
    parser.add_argument("--episodes", type=int, default=10)
    parser.add_argument("--max-sec", type=float, default=40.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--out", type=Path, default=None, help="GIF の保存先（既定: outputs/eval/<チェックポイント名>）")
    args = parser.parse_args()

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
        success_t, lifted, gif = run_episode(policy, preprocessor, postprocessor, renderer, rng, args.max_sec)
        results.append(success_t is not None)
        path = out_dir / f"eval_{ep:02d}_{'success' if success_t is not None else 'fail'}.gif"
        Image.fromarray(gif[0]).save(path, save_all=True, append_images=[Image.fromarray(f) for f in gif[1:]],
                                     duration=1000 // GIF_FPS, loop=0)
        status = f"成功（{success_t:.1f} 秒）" if success_t is not None else ("失敗（持ち上げまで）" if lifted else "失敗")
        print(f"  試行 {ep:2d}: {status}  [{time.time() - start:.0f} 秒で評価] → {path.name}")
    renderer.close()
    print(f"\n成功率: {sum(results)}/{len(results)}（{100 * sum(results) / len(results):.0f}%）  GIF: {out_dir}")


if __name__ == "__main__":
    main()

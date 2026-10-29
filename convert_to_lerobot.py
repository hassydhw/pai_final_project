"""
記録したエピソード（recorder.py + render_episodes.py）を LeRobot 形式のデータセットに変換する

~/lerobot_env（LeRobot 0.6.1 を入れた学習用の venv）で実行する。テレオペ用の ~/pai_env とは numpy の版が合わないので分けている。
画像は動画（mp4）ではなく PNG で保存する（FFmpeg 不要。小さなデータセットならこれで十分）。

  ~/lerobot_env/bin/python convert_to_lerobot.py              # data/episodes/ → data/lerobot/so101_pick_place
  ~/lerobot_env/bin/python convert_to_lerobot.py --overwrite  # 既存のデータセットを消して作り直す

特徴量:
  observation.state            (6,)  関節角 joint1〜4 + 指 2 本（HDF5 の observation/qpos）
  observation.images.front     (240, 320, 3)  後方カメラ
  observation.images.wrist     (240, 320, 3)  手先カメラ
  action                       (6,)  アクチュエータ指令（HDF5 の action = data.ctrl）
"""

import argparse
import shutil
from pathlib import Path

import h5py
import numpy as np
from lerobot.datasets.lerobot_dataset import LeRobotDataset

BASE_DIR = Path(__file__).resolve().parent
EPISODE_DIR = BASE_DIR / "data" / "episodes"
DATASET_ROOT = BASE_DIR / "data" / "lerobot" / "so101_pick_place"
REPO_ID = "local/so101_pick_place"
TASK = "Pick up the red block and place it on the green area."
CAMERAS = ["front", "wrist"]


def make_features(joint_names, image_shape):
    image = {"dtype": "image", "shape": image_shape, "names": ["height", "width", "channels"]}
    return {
        "observation.state": {"dtype": "float32", "shape": (len(joint_names),), "names": joint_names},
        **{f"observation.images.{cam}": dict(image) for cam in CAMERAS},
        "action": {"dtype": "float32", "shape": (len(joint_names),), "names": joint_names},
    }


def main():
    parser = argparse.ArgumentParser(description="記録したエピソードを LeRobot 形式に変換する")
    parser.add_argument("--episodes", type=Path, default=EPISODE_DIR)
    parser.add_argument("--root", type=Path, default=DATASET_ROOT)
    parser.add_argument("--overwrite", action="store_true", help="既存のデータセットを消して作り直す")
    args = parser.parse_args()

    paths = sorted(args.episodes.glob("episode_*.hdf5"))
    if not paths:
        raise SystemExit(f"[エラー] エピソードがありません: {args.episodes}")
    if args.root.exists():
        if not args.overwrite:
            raise SystemExit(f"[エラー] {args.root} は既にあります（作り直すなら --overwrite）")
        shutil.rmtree(args.root)

    with h5py.File(paths[0], "r") as h5:
        fps = int(h5.attrs["fps"])
        joint_names = [str(n) for n in h5.attrs["joint_names"]]
        image_shape = h5[f"observation/images/{CAMERAS[0]}"].shape[1:]

    dataset = LeRobotDataset.create(repo_id=REPO_ID, fps=fps, features=make_features(joint_names, image_shape),
                                    root=args.root, robot_type="so101_sim", use_videos=False)
    total = 0
    for path in paths:
        with h5py.File(path, "r") as h5:
            if "observation/images" not in h5:
                raise SystemExit(f"[エラー] {path.name} に画像がありません（先に render_episodes.py を実行）")
            assert int(h5.attrs["fps"]) == fps, f"{path.name} の fps が他と違います"
            state = h5["observation/qpos"][:].astype(np.float32)
            action = h5["action"][:].astype(np.float32)
            images = {cam: h5[f"observation/images/{cam}"] for cam in CAMERAS}
            for t in range(len(state)):
                dataset.add_frame({
                    "observation.state": state[t],
                    **{f"observation.images.{cam}": images[cam][t] for cam in CAMERAS},
                    "action": action[t],
                    "task": TASK,
                })
        dataset.save_episode()
        total += len(state)
        print(f"  {path.name}: {len(state)} フレーム")
    dataset.finalize()
    print(f"→ {args.root}（{len(paths)} エピソード、{total} フレーム、{fps} fps）")


if __name__ == "__main__":
    main()

"""
記録したエピソード（recorder.py）にカメラ画像を追加する（ヘッドレス、EGL で描画）

各フレームの全状態 sim/qpos を MuJoCo に戻して描画し、observation/images/<名前> (T, H, W, 3) uint8 に書き込む。
描画には記録に添付された MJCF（attrs["model_xml"]）を使うので、後からモデルを変えても記録時の見た目で描ける。

  python render_episodes.py              # data/episodes/ で画像がまだないエピソードをすべて処理
  python render_episodes.py --force      # 画像を描き直す
  python render_episodes.py --preview    # 各エピソードの数フレームを並べた PNG も保存（確認用）
"""

import argparse
import os
from pathlib import Path

os.environ.setdefault("MUJOCO_GL", "egl")  # mujoco の import 前に設定する

import h5py  # noqa: E402
import mujoco  # noqa: E402
import numpy as np  # noqa: E402

from recorder import DATA_DIR  # noqa: E402

# 画像の名前 → MJCF のカメラ名
CAMERAS = {"front": "cam_front", "wrist": "cam_wrist"}
IMAGE_H, IMAGE_W = 240, 320


def scene_option():
    """記録用の表示設定: 操作補助の目標球（geom group 2）と site は写さない"""
    option = mujoco.MjvOption()
    option.geomgroup[2] = 0
    option.sitegroup[:] = 0
    return option


def render_episode(path, renderer, model, force=False):
    with h5py.File(path, "a") as h5:
        if "observation/images" in h5:
            if not force:
                print(f"  {path.name}: 画像あり（スキップ）")
                return False
            del h5["observation/images"]
        sim_qpos = h5["sim/qpos"][:]
        data = mujoco.MjData(model)
        option = scene_option()
        datasets = {
            name: h5.create_dataset(f"observation/images/{name}", shape=(len(sim_qpos), IMAGE_H, IMAGE_W, 3),
                                    dtype=np.uint8, chunks=(1, IMAGE_H, IMAGE_W, 3), compression="lzf")
            for name in CAMERAS
        }
        for t, qpos in enumerate(sim_qpos):
            data.qpos[:] = qpos
            mujoco.mj_forward(model, data)
            for name, camera in CAMERAS.items():
                renderer.update_scene(data, camera=camera, scene_option=option)
                datasets[name][t] = renderer.render()
    print(f"  {path.name}: {len(sim_qpos)} フレーム × {len(CAMERAS)} カメラを描画")
    return True


def save_preview(path, n=6):
    """エピソードから等間隔に n フレームを取り出し、カメラごとに 1 行ずつ並べた PNG を保存する"""
    from PIL import Image

    with h5py.File(path, "r") as h5:
        images = h5["observation/images"]
        idx = np.linspace(0, len(h5["time"]) - 1, n).astype(int)
        rows = [np.concatenate([images[name][i] for i in idx], axis=1) for name in CAMERAS]
    out = path.with_suffix(".preview.png")
    Image.fromarray(np.concatenate(rows, axis=0)).save(out)
    print(f"  プレビュー: {out.name}")


def main():
    parser = argparse.ArgumentParser(description="記録したエピソードにカメラ画像を追加する")
    parser.add_argument("--dir", type=Path, default=DATA_DIR)
    parser.add_argument("--force", action="store_true", help="画像があっても描き直す")
    parser.add_argument("--preview", action="store_true", help="確認用の PNG を保存する")
    args = parser.parse_args()

    paths = sorted(args.dir.glob("episode_*.hdf5"))
    print(f"{args.dir}: {len(paths)} エピソード")
    # Renderer を 1 プロセスで同時に 2 個作ると画像が乱れるので、モデルが変わるときだけ作り直す
    renderer = model = model_xml = None
    for path in paths:
        with h5py.File(path, "r") as h5:
            xml = h5.attrs["model_xml"]
        if xml != model_xml:
            if renderer is not None:
                renderer.close()
            model_xml = xml
            model = mujoco.MjModel.from_xml_string(model_xml)
            renderer = mujoco.Renderer(model, IMAGE_H, IMAGE_W)
        rendered = render_episode(path, renderer, model, args.force)
        if args.preview and (rendered or not path.with_suffix(".preview.png").exists()):
            save_preview(path)
    if renderer is not None:
        renderer.close()


if __name__ == "__main__":
    main()

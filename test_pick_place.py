"""
Pick & Place 環境のヘッドレス自動テスト

Quest 3 なしで、teleop_server.py と同じ IK / グリッパー制御を使い
ウェイポイントを順番にたどって「掴む → 持ち上げる → 目標エリアに置く」を検証する。

  python3 test_pick_place.py          # 結果だけ表示
  python3 test_pick_place.py --view   # MuJoCo viewer で動きを確認
"""

import argparse
import time

import mujoco
import mujoco.viewer
import numpy as np

from teleop_server import TaskMonitor, apply_control, load_model

# (目標位置 [x, y, z], グリップ値, 移動にかける秒数)
BLOCK = np.array([0.17, 0.10])
AREA = np.array([0.16, -0.12])
WAYPOINTS = [
    ([*BLOCK, 0.08], 0.0, 1.0),   # ブロック上空へ
    ([*BLOCK, 0.018], 0.0, 1.0),  # 降下
    ([*BLOCK, 0.018], 1.0, 0.8),  # 掴む
    ([*BLOCK, 0.10], 1.0, 1.0),   # 持ち上げ
    ([*AREA, 0.10], 1.0, 1.5),    # 目標エリア上空へ
    ([*AREA, 0.025], 1.0, 1.0),   # 降下
    ([*AREA, 0.025], 0.0, 0.8),   # 離す
    ([*AREA, 0.10], 0.0, 1.0),    # 退避
]


def run(view=False):
    model, data = load_model()
    monitor = TaskMonitor(model)
    block = model.body("red_block").id
    viewer = mujoco.viewer.launch_passive(model, data) if view else None

    prev_target = np.array([0.15, 0.0, 0.15])
    prev_grip = 0.0
    max_z = 0.0
    for target, grip, duration in WAYPOINTS:
        target = np.array(target)
        n = int(duration / model.opt.timestep)
        for i in range(n):
            s = min(1.0, (i + 1) / (0.7 * n))  # 7 割の時間で移動し、残りで静定
            apply_control(model, data, prev_target + (target - prev_target) * s,
                          prev_grip + (grip - prev_grip) * s)
            mujoco.mj_step(model, data)
            max_z = max(max_z, data.xpos[block][2])
            monitor.update(data)
            if viewer is not None and i % 8 == 0:
                viewer.sync()
                time.sleep(0.016)
        ee = data.site("ee_site").xpos
        print(f"  -> target={np.round(target, 3)} grip={grip:.1f}  "
              f"ee={np.round(ee, 3)}  block={np.round(data.xpos[block], 3)}")
        prev_target, prev_grip = target, grip

    # 離した後に静定させてから判定
    for _ in range(500):
        mujoco.mj_step(model, data)
        monitor.update(data)

    if viewer is not None:
        viewer.close()

    final = data.xpos[block]
    print(f"\n最大持ち上げ高さ: {max_z:.3f} m / 最終位置: {np.round(final, 3)}")
    assert max_z > 0.07, "ブロックが十分に持ち上がっていません（滑り落ちた可能性）"
    assert monitor.placed, "ブロックが目標エリアに置かれていません"
    print("✅ Pick & Place テスト成功")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--view", action="store_true")
    run(parser.parse_args().view)

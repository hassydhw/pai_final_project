"""
Pick & Place 環境のヘッドレス自動テスト

Quest 3 なしで、teleop_server.py と同じ IK / グリッパー制御を使い
ウェイポイントを順番にたどって「掴む → 持ち上げる → 目標エリアに置く」を検証する。
ブロックの初期位置に加え、ランダム再配置の範囲の四隅（向きも最大までずらす）でも試す。

  python3 test_pick_place.py          # 結果だけ表示
  python3 test_pick_place.py --view   # MuJoCo viewer で動きを確認
"""

import argparse
import time

import mujoco
import mujoco.viewer
import numpy as np

from teleop_server import BLOCK_RANDOM_XY, BLOCK_RANDOM_YAW, TaskMonitor, apply_control, load_model


def make_waypoints(block, area):
    """(目標位置 [x, y, z], グリップ値, 移動にかける秒数) のリスト"""
    return [
        ([*block, 0.08], 0.0, 1.0),   # ブロック上空へ
        ([*block, 0.018], 0.0, 1.0),  # 降下
        ([*block, 0.018], 1.0, 0.8),  # 掴む
        ([*block, 0.10], 1.0, 1.0),   # 持ち上げ
        ([*area, 0.10], 1.0, 1.5),    # 目標エリア上空へ
        ([*area, 0.025], 1.0, 1.0),   # 降下
        ([*area, 0.025], 0.0, 0.8),   # 離す
        ([*area, 0.10], 0.0, 1.0),    # 退避
    ]


def place_block(model, data, offset, yaw):
    """ブロックを keyframe "home" の位置から offset [m] ずらし、z 軸回りに yaw [rad] 回して置く"""
    qadr = model.joint("red_block_joint").qposadr[0]
    data.qpos[qadr:qadr + 2] += offset
    data.qpos[qadr + 3:qadr + 7] = [np.cos(yaw / 2), 0.0, 0.0, np.sin(yaw / 2)]
    mujoco.mj_forward(model, data)
    return data.qpos[qadr:qadr + 2].copy()


def run_case(offset=(0.0, 0.0), yaw=0.0, view=False):
    model, data = load_model()
    block_xy = place_block(model, data, np.array(offset), yaw)
    area_xy = model.body("place_area").pos[:2].copy()
    monitor = TaskMonitor(model)
    block = model.body("red_block").id
    viewer = mujoco.viewer.launch_passive(model, data) if view else None

    print(f"\n[ブロック ({block_xy[0]:.3f}, {block_xy[1]:.3f}), "
          f"r={np.hypot(*block_xy):.3f}, yaw={yaw:+.2f}]")
    prev_target = np.array([0.15, 0.0, 0.15])
    prev_grip = 0.0
    max_z = 0.0
    for target, grip, duration in make_waypoints(block_xy, area_xy):
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

    print(f"  最大持ち上げ高さ: {max_z:.3f} m / 最終位置: {np.round(data.xpos[block], 3)}")
    assert max_z > 0.07, "ブロックが十分に持ち上がっていません（滑り落ちた可能性）"
    assert monitor.placed, "ブロックが目標エリアに置かれていません"


def run(view=False):
    d, yaw = BLOCK_RANDOM_XY, BLOCK_RANDOM_YAW
    cases = [
        ((0.0, 0.0), 0.0),   # 初期位置
        ((+d, +d), +yaw),    # 奥・左（土台から最も遠い）
        ((+d, -d), -yaw),
        ((-d, +d), -yaw),
        ((-d, -d), +yaw),
    ]
    for offset, case_yaw in cases:
        run_case(offset, case_yaw, view)
    print(f"\n✅ Pick & Place テスト成功（{len(cases)} ケース）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--view", action="store_true")
    run(parser.parse_args().view)

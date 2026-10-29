"""
エピソード記録（recorder.py）と画像の追加（render_episodes.py）のヘッドレス結合テスト

run_simulation を viewer なしで動かし、Quest 3 の代わりに TeleopState へ手の位置を送って
test_pick_place.py と同じ手順で Pick & Place する（実時間で約 15 秒）。
  1. 配置成功 → 1 秒（POST_SUCCESS_SEC）記録を続けてからエピソードが 1 つ保存される
  2. 次のエピソード中に B ボタン → 破棄される
  3. 次のエピソード中に切断 → 破棄される
  4. render_episodes.py で画像を追加できる
  5. 成功後の延長記録中に B ボタンが来ても、成功エピソードとして保存される
一時ディレクトリに保存するので data/episodes/ は汚さない。

  python3 test_recorder.py          # 終わったら一時ディレクトリを消す
  python3 test_recorder.py --keep   # 記録とプレビュー画像を残す（場所を表示する）
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import h5py
import mujoco
import numpy as np

from recorder import POST_SUCCESS_SEC, RECORD_FPS, EpisodeRecorder
from teleop_server import (HOME_TARGET, MODEL_XML, SCALE, TeleopState, load_model, robot_to_xr,
                           run_simulation)
from test_pick_place import make_waypoints

HAND_ORIGIN = np.array([0.1, 1.0, -0.3])  # 接続時の手の位置（WebXR 座標）


class DummyViewer:
    """run_simulation を止めるためだけの viewer の代わり"""

    def __init__(self):
        self.running = True

    def is_running(self):
        return self.running

    def sync(self):
        pass

    def set_texts(self, texts):
        pass


def send(state, target, grip, **flags):
    """ロボット座標の目標位置になるような手の位置を Quest 3 の代わりに送る"""
    hand = HAND_ORIGIN + robot_to_xr(np.asarray(target) - HOME_TARGET) / SCALE
    state.update_from_quest({"x": hand[0], "y": hand[1], "z": hand[2], "gripper": grip, **flags})


def follow(state, waypoints, start):
    """ウェイポイントを 60Hz で補間しながら送る（7 割の時間で移動し、残りで静定）"""
    prev_target, prev_grip = np.array(start), 0.0
    for target, grip, duration in waypoints:
        target = np.array(target)
        n = int(duration * 60)
        for i in range(n):
            s = min(1.0, (i + 1) / (0.7 * n))
            send(state, prev_target + (target - prev_target) * s, prev_grip + (grip - prev_grip) * s)
            time.sleep(1 / 60)
        prev_target, prev_grip = target, grip


def wait_until(condition, timeout, message):
    deadline = time.time() + timeout
    while not condition():
        assert time.time() < deadline, message
        time.sleep(0.02)


def check_interrupt_after_success(out_dir):
    """成功後の延長記録中に B ボタン（discard）が来ても、その時点までが成功エピソードとして保存される"""
    model, data = load_model()
    recorder = EpisodeRecorder(model, "<mujoco/>", out_dir=out_dir, verbose=False)
    recorder.poll(data, connected=True)
    for _ in range(100):  # 0.2 秒
        recorder.record(data, np.zeros(4))
        mujoco.mj_step(model, data)
    recorder.mark_success(data)
    recorder.discard("B ボタン")
    assert recorder.saved_count == 1 and not recorder.active, "成功後の B ボタンで成功エピソードが保存されません"
    print("成功後の延長記録中の B ボタン → 保存（OK）")


def run(keep=False):
    out_dir = Path(tempfile.mkdtemp(prefix="test_recorder_"))
    model, data = load_model()
    state = TeleopState()
    viewer = DummyViewer()
    recorder = EpisodeRecorder(model, MODEL_XML.read_text(encoding="utf-8"), out_dir=out_dir)
    sim = threading.Thread(target=run_simulation, args=(model, data, state, viewer, recorder), daemon=True)
    sim.start()
    time.sleep(0.3)

    try:
        # 1. 接続 → 記録開始 → Pick & Place → 配置成功で保存
        assert not recorder.active, "接続前に記録が始まっています"
        send(state, HOME_TARGET, 0.0)
        wait_until(lambda: recorder.active, 1.0, "接続しても記録が始まりません")
        block_xy = data.xpos[model.body("red_block").id][:2].copy()
        area_xy = model.body("place_area").pos[:2].copy()
        follow(state, make_waypoints(block_xy, area_xy), HOME_TARGET)
        wait_until(lambda: recorder.saved_count == 1, 5.0, "配置成功してもエピソードが保存されません")

        # 2. 成功演出のあとブロックが再配置され、次のエピソードが始まる → B ボタンで破棄
        wait_until(lambda: recorder.active, 4.0, "再配置のあと記録が始まりません")
        time.sleep(0.5)
        send(state, HOME_TARGET, 0.0, reset_block=True)
        time.sleep(0.3)
        # 破棄されて番号は進まず、接続中なのですぐ次のエピソードが始まっている
        assert recorder.active and recorder.saved_count == 1 and recorder.next_index == 1, \
            "B ボタンでの破棄と次のエピソードの開始がうまくいっていません"

        # 3. 切断 → 破棄（再接続するまで記録しない）
        state.reset_origin()
        wait_until(lambda: not recorder.active, 1.0, "切断しても記録が止まりません")
        time.sleep(0.3)
        assert not recorder.active, "切断中に記録が始まっています"
    finally:
        viewer.running = False
        sim.join(timeout=2)

    files = sorted(out_dir.glob("episode_*.hdf5"))
    assert len(files) == 1, f"保存されたエピソードが 1 つではありません: {files}"
    assert recorder.next_index == 1, "破棄したエピソードの番号が進んでいます"
    with h5py.File(files[0], "r") as h5:
        t = h5["time"][:]
        n = len(t)
        shapes = {k: h5[k].shape for k in ("observation/qpos", "observation/qvel", "observation/ee_pos",
                                           "action", "action_ee", "sim/qpos", "sim/qvel")}
        print(f"\n{files[0].name}: {n} フレーム（{t[-1]:.1f} 秒） {shapes}")
        assert h5.attrs["fps"] == RECORD_FPS and h5.attrs["success"]
        assert np.allclose(np.diff(t), 1 / RECORD_FPS, atol=0.0021), "フレーム間隔が一定ではありません"
        assert shapes["observation/qpos"] == (n, 6) and shapes["action"] == (n, 6)
        assert shapes["action_ee"] == (n, 4) and shapes["sim/qpos"] == (n, model.nq)
        # 手順全体は 8.1 秒。配置成功は「離す」の途中で判定され、そこから POST_SUCCESS_SEC だけ続く（実行ごとに揺れる）
        assert 5.0 < t[-1] < 12.0, "エピソードの長さが手順の時間と合いません"
        success_time = h5.attrs["success_time"]
        assert abs(t[-1] - success_time - POST_SUCCESS_SEC) < 2 / RECORD_FPS, "成功後の記録時間が違います"
        # 最後は離して手を引いている（グリップ値 0、把持中心が成功時より上）
        k = int(round(success_time * RECORD_FPS))
        ee_z = h5["observation/ee_pos"][:, 2]
        print(f"成功判定 {success_time:.2f} 秒 → 終了 {t[-1]:.2f} 秒、把持中心の高さ {ee_z[k]:.3f} → {ee_z[-1]:.3f} m")
        assert h5["action_ee"][-1, 3] < 0.01, "最後のフレームでグリッパーが開いていません"
        assert ee_z[-1] > ee_z[k] + 0.01, "成功後に手を引く動きが記録されていません"
        # 掴んでいる間はグリップ値が 1、ブロックは持ち上がっている
        block_z = h5["sim/qpos"][:, 2]
        assert block_z.max() > 0.07, "記録の中でブロックが持ち上がっていません"
        assert h5["action_ee"][:, 3].max() > 0.99

    # 4. 画像の追加（別プロセス。EGL で描画）
    result = subprocess.run([sys.executable, "render_episodes.py", "--dir", str(out_dir), "--preview"],
                            capture_output=True, text=True, cwd=Path(__file__).resolve().parent)
    print(result.stdout.strip())
    with h5py.File(files[0], "r") as h5:
        for name in ("front", "wrist"):
            images = h5[f"observation/images/{name}"]
            assert images.shape == (n, 240, 320, 3) and images.dtype == np.uint8
            assert images[n // 2].std() > 10, f"{name} の画像が真っ黒（または単色）です"

    # 5. 成功後の延長記録中の中断
    check_interrupt_after_success(out_dir / "interrupt")

    if keep:
        print(f"\n記録とプレビュー画像: {out_dir}")
    else:
        shutil.rmtree(out_dir)
    print("✅ 記録テスト成功")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    run(parser.parse_args().keep)

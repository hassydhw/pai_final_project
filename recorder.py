"""
テレオペのエピソード記録（模倣学習 / LeRobot 用の示範データ）

teleop_server.py --record で有効になる。1 エピソード = 1 つの HDF5 ファイル（data/episodes/episode_000000.hdf5）。
  開始 : ブロックが（再）配置されたあと、Quest 3 が接続していれば記録を始める
  保存 : 配置成功のあと POST_SUCCESS_SEC だけ記録を続けてから（ブロックを離して手を引くところまで含める）
         この間に B ボタン・切断などがあっても、成功したエピソードとしてその時点までを保存する
  破棄 : B ボタン・ブロックが届かない場所に出た・Quest 3 の切断・MAX_EPISODE_SEC を超えた（成功前）

カメラ画像はここでは描画しない（テレオペ中の物理ループを重くしないため）。
全状態（sim/qpos）を記録しておき、render_episodes.py で後から描画して observation/images/* に追加する。

HDF5 の中身（T = フレーム数、RECORD_FPS で等間隔）:
  observation/qpos    (T, 6)    関節角 joint1〜4 [rad] + 指 2 本の開き量 [m]
  observation/qvel    (T, 6)    その速度
  observation/ee_pos  (T, 3)    把持中心（ee_site）の位置 [m]
  action              (T, 6)    そのフレームで実行中のアクチュエータ指令（data.ctrl。関節の目標角 + 指の目標開き量）
  action_ee           (T, 4)    手の位置から決めた目標位置 xyz [m] + グリップ値 0〜1
  sim/qpos, sim/qvel  (T, nq), (T, nv)  描画・再生用の全状態（先頭は red_block の freejoint）
  time                (T,)      エピソード開始からの時間 [s]
  attrs: fps, success, success_time（配置成功を判定した時間 [s]）, joint_names,
         model_xml（描画に使う MJCF の全文）, created
"""

import datetime
import re
from pathlib import Path

import h5py
import numpy as np

DATA_DIR = Path(__file__).resolve().parent / "data" / "episodes"
RECORD_FPS = 30
MAX_EPISODE_SEC = 120.0
POST_SUCCESS_SEC = 1.5  # 配置成功のあとも記録を続ける時間（離して手を引く動きを学習データに入れる。
                        # teleop_server.SUCCESS_HOLD_SEC（ブロック再配置までの時間）より短くする）
JOINT_NAMES = ["joint1", "joint2", "joint3", "joint4", "finger_left_joint", "finger_right_joint"]


class EpisodeRecorder:
    def __init__(self, model, model_xml, out_dir=DATA_DIR, fps=RECORD_FPS, verbose=True):
        self.model = model
        self.model_xml = model_xml
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.fps = fps
        self.verbose = verbose
        self.qadr = [model.joint(n).qposadr[0] for n in JOINT_NAMES]
        self.vadr = [model.joint(n).dofadr[0] for n in JOINT_NAMES]
        self.ee_site = model.site("ee_site").id
        self.next_index = self._next_index()
        self.saved_count = 0
        self.armed = True  # 次に接続を確認できたら記録を始める
        self.frames = None
        self.success_time = None  # 配置成功を判定した時間（エピソード開始から [s]）。None なら成功前

    def _next_index(self):
        indices = [int(m.group(1)) for p in self.out_dir.glob("episode_*.hdf5")
                   if (m := re.fullmatch(r"episode_(\d+)\.hdf5", p.name))]
        return max(indices, default=-1) + 1

    def _log(self, message):
        if self.verbose:
            print(f"[Rec] {message}")

    @property
    def active(self):
        return self.frames is not None

    def arm(self):
        """ブロックの再配置後に呼ぶ。記録中なら破棄し、次の接続確認で新しいエピソードを始める"""
        self.armed = True

    def poll(self, data, connected):
        """毎ループ呼ぶ。接続状態に応じて記録を始める / 切断で破棄する。状態が変わったら True"""
        if self.active and not connected:
            self.discard("Quest 3 が切断されたため")
            self.armed = True
            return True
        if not self.active and self.armed and connected:
            self.armed = False
            self.frames = {k: [] for k in ("qpos", "qvel", "ee_pos", "action", "action_ee",
                                           "sim_qpos", "sim_qvel", "time")}
            self.t0 = self.next_t = data.time
            self._log(f"エピソード {self.next_index} の記録を開始")
            return True
        return False

    def record(self, data, action_ee):
        """mj_step の直前に呼ぶ。RECORD_FPS の間隔（シミュレーション時間）で 1 フレームずつ記録する"""
        if not self.active or data.time + 1e-9 < self.next_t:
            return
        f = self.frames
        f["qpos"].append(data.qpos[self.qadr].copy())
        f["qvel"].append(data.qvel[self.vadr].copy())
        f["ee_pos"].append(data.site_xpos[self.ee_site].copy())
        f["action"].append(data.ctrl.copy())
        f["action_ee"].append(np.asarray(action_ee, dtype=float))
        f["sim_qpos"].append(data.qpos.copy())
        f["sim_qvel"].append(data.qvel.copy())
        f["time"].append(data.time - self.t0)
        self.next_t += 1.0 / self.fps
        if self.success_time is not None and data.time - self.t0 - self.success_time >= POST_SUCCESS_SEC:
            self.save()
        elif data.time - self.t0 > MAX_EPISODE_SEC:
            self.discard(f"{MAX_EPISODE_SEC:.0f} 秒を超えたため")
            self.armed = True

    def mark_success(self, data):
        """配置成功時に呼ぶ。POST_SUCCESS_SEC だけ記録を続けたあと自動で保存する"""
        if self.active and self.success_time is None:
            self.success_time = data.time - self.t0

    def discard(self, reason):
        if self.success_time is not None:
            # 成功後の延長記録中なら、その時点までを成功エピソードとして保存する
            self.save()
            return
        if self.active:
            self._log(f"エピソード {self.next_index} を破棄（{reason}、{len(self.frames['time'])} フレーム）")
        self.frames = None

    def save(self):
        """記録中のエピソードを HDF5 に書き出してパスを返す（通常は mark_success のあと自動で呼ばれる）"""
        success_time, self.success_time = self.success_time, None
        if not self.active or not self.frames["time"]:
            self.frames = None
            return None
        path = self.out_dir / f"episode_{self.next_index:06d}.hdf5"
        f = self.frames
        with h5py.File(path, "w") as h5:
            h5.attrs["fps"] = self.fps
            h5.attrs["success"] = True
            h5.attrs["success_time"] = success_time if success_time is not None else f["time"][-1]
            h5.attrs["joint_names"] = JOINT_NAMES
            h5.attrs["model_xml"] = self.model_xml
            h5.attrs["created"] = datetime.datetime.now().isoformat(timespec="seconds")
            for key in ("qpos", "qvel", "ee_pos"):
                h5.create_dataset(f"observation/{key}", data=np.array(f[key]))
            h5.create_dataset("action", data=np.array(f["action"]))
            h5.create_dataset("action_ee", data=np.array(f["action_ee"]))
            h5.create_dataset("sim/qpos", data=np.array(f["sim_qpos"]))
            h5.create_dataset("sim/qvel", data=np.array(f["sim_qvel"]))
            h5.create_dataset("time", data=np.array(f["time"]))
        n = len(f["time"])
        self._log(f"エピソード {self.next_index} を保存 → {path.name}（{n / self.fps:.1f} 秒、{n} フレーム）")
        self.frames = None
        self.next_index += 1
        self.saved_count += 1
        return path

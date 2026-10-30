"""
Quest 3 VR テレオペ統合サーバー（Phase 3）

1 つのプロセス・1 つのポートで以下をすべて処理する:
  - GET /    : index.html の配信（WebXR クライアント）
  - GET /ws  : WebSocket（Quest 3 からのコントローラ位置・グリップ値の受信、VR 表示用のシーン状態の送信）
  - MuJoCo   : SO-101 アームの IK 追従 + グリッパー開閉 + Pick & Place 物理シミュレーション

スレッド構成:
  - メインスレッド : MuJoCo の物理ループと viewer（GLFW はメインスレッド必須）
  - 別スレッド     : aiohttp の非同期イベントループ（HTTP / WebSocket）

使い方:
  python3 teleop_server.py               # viewer あり（WSLg でウィンドウ表示）
  python3 teleop_server.py --no-viewer   # viewer なし（通信テスト用）
  python3 teleop_server.py --record      # 配置成功したエピソードを data/episodes/ に記録（recorder.py）
"""

import argparse
import asyncio
import json
import threading
import time
from pathlib import Path

import mujoco
import mujoco.viewer
import numpy as np
from aiohttp import WSMsgType, web

from recorder import EpisodeRecorder

BASE_DIR = Path(__file__).resolve().parent
INDEX_HTML = BASE_DIR / "index.html"
MODEL_XML = BASE_DIR / "models" / "so101_pick_place.xml"

DEFAULT_PORT = 8000

# --- アーム幾何パラメータ（models/so101_pick_place.xml と一致させる） ---
SHOULDER_Z = 0.08    # 床から joint2（肩）までの高さ
L1 = 0.12            # 上腕（joint2 → joint3）
L2 = 0.10            # 前腕（joint3 → joint4）
L_TOOL = 0.095       # 手首（joint4）→ 把持中心（ee_site）
FINGER_OPEN = 0.03   # 指 1 本あたりの最大開き量 [m]

# --- テレオペのマッピング ---
HOME_TARGET = np.array([0.15, 0.0, 0.12])  # 初回接続時の手の位置に対応するアーム先端位置（届く範囲内にする）
SCALE = 0.5                                # 手の移動量 → アーム移動量の倍率
Z_MIN = 0.018                              # 指先が床にめり込まない最低高さ
R_MIN = 0.06                               # 土台に近づきすぎないための最小水平距離

# MuJoCo viewer の初期視点: アームの真後ろやや上から作業エリアを見る
# （操作者と同じ向き → 手を前に出す = 画面の奥、手を右に出す = 画面の右）
VIEWER_CAMERA = dict(lookat=[0.15, 0.0, 0.04], distance=0.50, azimuth=0.0, elevation=-30.0)

# ブロック再配置時のランダム幅（初期位置 (0.15, 0.08) を中心に）
BLOCK_RANDOM_XY = 0.03     # ±3cm（最も遠い角でも r=0.211m で、先端が届く範囲 r≈0.217m に収まる）
BLOCK_RANDOM_YAW = 0.5     # ±約 30°（大きすぎると角を掴むことになり難しくなる）

# 配置成功時の演出
SUCCESS_HOLD_SEC = 2.0                       # メッセージ表示からブロック再配置までの時間
AREA_SUCCESS_RGBA = [1.0, 0.85, 0.1, 1.0]    # 成功中は目標エリアを黄色に光らせる

JOINT_LIMITS = [(-3.14, 3.14), (-1.57, 1.57), (-2.2, 2.2), (-2.0, 2.0)]

# Quest 3 の VR 空間にアームとブロックを表示するための状態送信
STATE_HZ = 60          # 各 geom の位置・姿勢を送る頻度（一定間隔で送るとカクつきにくい）
VR_FLOOR_HALF = 0.25   # 床（plane）は VR 内では作業エリア周りの机として表示する（半幅、ロボット座標 [m]）

# VR 内の机の高さ: VR 開始時の目の高さ（HMD）にアームの最上部が来るように固定する（Unity の Eye Level 相当）
ARM_TOP_Z = 0.23       # HOME_TARGET の姿勢でのアーム最上部（肘の上端）の高さ [m]。HOME_TARGET やリンク長を変えたら測り直す
EYE_TO_ARM_TOP = 0.0   # 目の高さからアーム最上部までの下げ幅 [m, VR 空間]。正にすると机が下がる


def robot_to_xr(v):
    """ロボット座標（x=前, y=左, z=上）→ WebXR 座標（x=右, y=上, z=手前）"""
    return np.array([-v[1], v[2], -v[0]])


def xr_to_robot(v):
    """WebXR 座標 → ロボット座標（robot_to_xr の逆変換）"""
    return np.array([-v[2], -v[0], v[1]])


class TeleopState:
    """WebSocket スレッドと物理ループで共有する最新入力・シーン状態（スレッドセーフ）"""

    def __init__(self):
        self.lock = threading.Lock()
        self.target_pos = HOME_TARGET.copy()
        self.gripper = 0.0
        self.initial_quest_pos = None
        self._origin_target = HOME_TARGET.copy()
        self._table_y = None  # VR 内でロボットの床（z=0）を置く高さ
        self.block_reset_requested = False
        self._poses = None
        self._success = False
        self._rec = None

    def reset_origin(self):
        """次に受信した手の位置を新しい原点にする（再接続時）"""
        with self.lock:
            self.initial_quest_pos = None

    def update_from_quest(self, payload):
        q = np.array([payload["x"], payload["y"], payload["z"]], dtype=float)
        grip = float(np.clip(payload.get("gripper", 0.0), 0.0, 1.0))

        with self.lock:
            if payload.get("recenter") or self.initial_quest_pos is None:
                # 接続時・VR 開始時: 前後左右は今の手の位置をホーム位置に対応させ、机の高さは目の高さから決める
                self.initial_quest_pos = q
                self._origin_target = HOME_TARGET.copy()
                if payload.get("eye") is not None:
                    self._table_y = float(payload["eye"]) - EYE_TO_ARM_TOP - ARM_TOP_Z / SCALE
                else:  # 目の高さが来ない（旧クライアント・テスト）ときは、今の手の位置にグリッパーを合わせる
                    self._table_y = q[1] - HOME_TARGET[2] / SCALE
            elif payload.get("reset"):
                # A ボタン: 前後左右の原点だけを今の目標位置から取り直す（机の高さは変えない）
                self.initial_quest_pos = q
                self._origin_target = self.target_pos.copy()

            # 前後左右: Quest 3 の座標変化をアームの作業可動域にスケーリング
            # WebXR: x=右, y=上, z=手前（前方は -z） → ロボット: x=前, y=左, z=上
            self.target_pos = self._origin_target + xr_to_robot(q - self.initial_quest_pos) * SCALE
            # 高さ: VR 内の机からの手の高さをそのまま使う（手を机より下げるとグリッパーは机の上で止まる）
            self.target_pos[2] = (q[1] - self._table_y) * SCALE
            self.gripper = grip
            if payload.get("reset_block"):
                self.block_reset_requested = True

    def pop_block_reset(self):
        """ブロックのリセット要求を 1 回だけ取り出す"""
        with self.lock:
            requested, self.block_reset_requested = self.block_reset_requested, False
            return requested

    def snapshot(self):
        with self.lock:
            return self.target_pos.copy(), self.gripper

    def connected(self):
        """Quest 3 から手の位置を受け取っているか（接続直後・切断後は False）"""
        with self.lock:
            return self.initial_quest_pos is not None

    def publish_scene(self, poses, success, rec=None):
        """物理ループから最新の geom の位置・姿勢、配置成功中かどうか、記録の状態（--record のとき）を渡す"""
        with self.lock:
            self._poses, self._success, self._rec = poses, success, rec

    def scene_state(self):
        """
        VR に送る状態メッセージ。
        anchor は VR 内でロボット座標の原点を置く位置。前後左右は手の原点とアームの原点目標が重なるように、
        高さは机の高さに置く（= 届く範囲ではコントローラの位置にグリッパーが来る）。手の原点が未定なら None。
        """
        with self.lock:
            if self._poses is None:
                return None
            anchor = None
            if self.initial_quest_pos is not None:
                anchor = self.initial_quest_pos - robot_to_xr(self._origin_target) / SCALE
                anchor[1] = self._table_y
                anchor = np.round(anchor, 4).tolist()
            return {"type": "state", "poses": self._poses, "success": self._success, "anchor": anchor,
                    "rec": self._rec}


def clamp_target(target):
    """IK に渡す前に、床・土台への干渉を避ける範囲に目標位置を収める"""
    x, y, z = target
    r = np.hypot(x, y)
    if r < R_MIN:
        if r < 1e-6:
            x, y = R_MIN, 0.0
        else:
            x, y = x / r * R_MIN, y / r * R_MIN
    return np.array([x, y, max(z, Z_MIN)])


def solve_ik(target):
    """
    把持中心を target に置き、グリッパーを真下に向ける関節角 (j1, j2, j3, j4) を返す。

    j1 で目標方向へ旋回し、残りは (水平距離 r, 高さ z) 平面の 2 リンク IK として解く。
    pitch 関節（y 軸回り）の角度 φ でリンク方向は (sin φ, cos φ) になる（0 = 真上）。
    """
    tx, ty, tz = clamp_target(target)
    j1 = np.arctan2(ty, tx)
    r = np.hypot(tx, ty)

    # グリッパーが真下を向くので、手首位置は把持中心の L_TOOL 真上
    dr = r
    dz = tz + L_TOOL - SHOULDER_Z
    d = np.hypot(dr, dz)
    d_clamped = np.clip(d, abs(L1 - L2) + 1e-3, L1 + L2 - 1e-3)
    if d > 1e-9:
        dr, dz = dr * d_clamped / d, dz * d_clamped / d
    d = d_clamped

    # 肘上（elbow-up）解
    cos_j3 = (d**2 - L1**2 - L2**2) / (2 * L1 * L2)
    j3 = np.arccos(np.clip(cos_j3, -1.0, 1.0))
    alpha = np.arctan2(dr, dz)
    beta = np.arctan2(L2 * np.sin(j3), L1 + L2 * np.cos(j3))
    j2 = alpha - beta
    # j2 + j3 + j4 = π でグリッパーが真下を向く
    j4 = np.pi - j2 - j3

    joints = np.array([j1, j2, j3, j4])
    lo, hi = np.array(JOINT_LIMITS).T
    return np.clip(joints, lo, hi)


def apply_control(model, data, target, gripper):
    """目標位置とグリップ値をアクチュエータ指令（data.ctrl）に変換する"""
    joints = solve_ik(target)
    for i, name in enumerate(["act_joint1", "act_joint2", "act_joint3", "act_joint4"]):
        data.ctrl[model.actuator(name).id] = joints[i]

    # gripper: 0.0 = 全開, 1.0 = 全閉
    finger = FINGER_OPEN * (1.0 - gripper)
    data.ctrl[model.actuator("act_finger_left").id] = finger
    data.ctrl[model.actuator("act_finger_right").id] = finger

    data.mocap_pos[model.body("target_site").mocapid[0]] = clamp_target(target)


def reset_block(model, data, rng=None):
    """
    赤ブロックを keyframe "home" の初期位置に戻す（速度もゼロにする）。
    rng を渡すと、初期位置を中心に xy 位置と向き（z 軸回り）をランダムにずらす。
    """
    joint = model.joint("red_block_joint")
    qadr, vadr = joint.qposadr[0], joint.dofadr[0]
    qpos = model.key("home").qpos[qadr:qadr + 7].copy()
    if rng is not None:
        qpos[:2] += rng.uniform(-BLOCK_RANDOM_XY, BLOCK_RANDOM_XY, size=2)
        yaw = rng.uniform(-BLOCK_RANDOM_YAW, BLOCK_RANDOM_YAW)
        qpos[3:7] = [np.cos(yaw / 2), 0.0, 0.0, np.sin(yaw / 2)]
    data.qpos[qadr:qadr + 7] = qpos
    data.qvel[vadr:vadr + 6] = 0.0
    mujoco.mj_forward(model, data)


def load_model():
    model = mujoco.MjModel.from_xml_path(str(MODEL_XML))
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, model.key("home").id)
    mujoco.mj_forward(model, data)
    return model, data


def scene_description(model):
    """
    VR 側で描画する geom の一覧（形・大きさ・色）。接続時に 1 回だけ送る。
    透明な geom は除き、床（plane）は作業エリア周りの大きさに切り詰める。
    """
    geoms, ids = [], []
    for i in range(model.ngeom):
        rgba = model.geom_rgba[i]
        if rgba[3] == 0:
            continue
        gtype = mujoco.mjtGeom(model.geom_type[i]).name.removeprefix("mjGEOM_").lower()
        size = model.geom_size[i].copy()
        if gtype == "plane":
            size[:2] = VR_FLOOR_HALF
        ids.append(i)
        geoms.append({"name": model.geom(i).name, "type": gtype,
                      "size": np.round(size, 4).tolist(), "rgba": np.round(rgba, 3).tolist()})
    message = {
        "type": "scene",
        "scale": 1.0 / SCALE,  # VR 内ではアームを手の動きと同じ縮尺（1 / SCALE 倍）で表示する
        "geoms": geoms,
        "success_geom": ids.index(model.geom("place_area_geom").id),
        "success_rgba": AREA_SUCCESS_RGBA,
    }
    return message, ids


def scene_poses(data, geom_ids):
    """各 geom の位置と姿勢 [x, y, z, qw, qx, qy, qz]（ロボット座標）"""
    quat = np.empty(4)
    poses = []
    for i in geom_ids:
        mujoco.mju_mat2Quat(quat, data.geom_xmat[i])
        poses.append(np.round(np.concatenate([data.geom_xpos[i], quat]), 4).tolist())
    return poses


class TaskMonitor:
    """Pick & Place の進行状況（持ち上げ・配置成功）を判定してログ出力する"""

    LIFT_Z = 0.04
    # 土台からこれより遠くで止まったブロックは掴めないので自動リセット
    # （先端が届くのは r≈0.217m までだが、指とブロックの幅の分だけ余裕があり、実測で 0.24m まで掴める）
    REACH_R = 0.24

    def __init__(self, model):
        self.block_body = model.body("red_block").id
        self.block_geom = model.geom("red_block_geom").id
        self.finger_geoms = {model.geom("finger_left_geom").id, model.geom("finger_right_geom").id}
        self.area_pos = model.body("place_area").pos.copy()
        self.area_half = model.geom("place_area_geom").size[:2].copy()
        self.lifted = False
        self.placed = False

    def _held(self, data):
        """指のどちらかがブロックに触れているか"""
        for c in data.contact[:data.ncon]:
            pair = {c.geom1, c.geom2}
            if self.block_geom in pair and pair & self.finger_geoms:
                return True
        return False

    def update(self, data, verbose=True):
        """状態を更新し、今回のステップで配置成功になったら True を返す"""
        pos = data.xpos[self.block_body]
        speed = np.linalg.norm(data.cvel[self.block_body][3:])

        if not self.lifted and pos[2] > self.LIFT_Z:
            self.lifted = True
            if verbose:
                print(f"[Task] ブロックを持ち上げました (z={pos[2]:.3f})")

        in_area = np.all(np.abs(pos[:2] - self.area_pos[:2]) < self.area_half)
        resting = pos[2] < 0.03 and speed < 0.01
        if self.lifted and not self.placed and in_area and resting and not self._held(data):
            self.placed = True
            if verbose:
                print(f"[Task] 🎯 配置成功！ブロックが目標エリアに置かれました ({pos[0]:.3f}, {pos[1]:.3f})")
            return True

        if self.placed and not in_area:
            # 再挑戦できるよう状態を戻す
            self.lifted = self.placed = False
        return False

    def out_of_reach(self, data):
        """ブロックがアームの届かない場所（または床の外）で止まっているか"""
        pos = data.xpos[self.block_body]
        speed = np.linalg.norm(data.cvel[self.block_body][3:])
        return (np.hypot(pos[0], pos[1]) > self.REACH_R and speed < 0.02) or pos[2] < -0.05

    def reset(self):
        self.lifted = self.placed = False


# ---------------------------------------------------------------------------
# HTTP / WebSocket サーバー（aiohttp）
# ---------------------------------------------------------------------------

def create_app(state, scene):
    scene_json = json.dumps(scene)

    async def index(request):
        # トンネル越しに .git や .py が見えないよう、index.html だけを配信する
        return web.FileResponse(INDEX_HTML, headers={"Cache-Control": "no-cache"})

    async def send_states(ws):
        """シミュレーションの最新状態を STATE_HZ で送り続ける（VR 内のアーム・ブロック表示用）"""
        loop = asyncio.get_running_loop()
        next_send = loop.time()
        try:
            while not ws.closed:
                message = state.scene_state()
                if message is not None:
                    await ws.send_str(json.dumps(message))
                # 送信にかかった時間で周期がずれないよう、予定時刻を基準に待つ
                next_send = max(next_send + 1.0 / STATE_HZ, loop.time())
                await asyncio.sleep(next_send - loop.time())
        except ConnectionResetError:
            pass

    async def websocket(request):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        print(f"\n[接続成功] Quest 3 Teleop 接続確立: {request.remote}")
        state.reset_origin()
        await ws.send_str(scene_json)
        sender = asyncio.create_task(send_states(ws))

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        state.update_from_quest(json.loads(msg.data))
                    except (ValueError, KeyError, TypeError) as e:
                        print(f"[警告] 不正なデータを無視しました: {e}")
                elif msg.type == WSMsgType.ERROR:
                    print(f"[エラー] WebSocket: {ws.exception()}")
        finally:
            sender.cancel()

        print("[切断] Quest 3 との接続が切れました")
        return ws

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/index.html", index)
    app.router.add_get("/ws", websocket)
    return app


def start_web_server(state, scene, host, port):
    """aiohttp を別スレッドで起動し、待ち受け開始まで待つ"""
    ready = threading.Event()
    error = []

    def run():
        async def main():
            runner = web.AppRunner(create_app(state, scene), access_log=None)
            await runner.setup()
            await web.TCPSite(runner, host, port).start()
            ready.set()
            await asyncio.Event().wait()

        try:
            asyncio.run(main())
        except Exception as e:  # ポート使用中など
            error.append(e)
            ready.set()

    threading.Thread(target=run, daemon=True).start()
    ready.wait(timeout=10)
    if error:
        raise SystemExit(f"[エラー] サーバー起動に失敗しました (port {port}): {error[0]}")


# ---------------------------------------------------------------------------
# 物理シミュレーションループ
# ---------------------------------------------------------------------------

def show_overlay(viewer, success_count, success, recorder=None):
    """viewer に成功回数と（成功時は）大きなメッセージを重ねて表示する（MuJoCo のフォントは ASCII のみ）"""
    if viewer is None:
        return
    texts = [(mujoco.mjtFontScale.mjFONTSCALE_150, mujoco.mjtGridPos.mjGRID_BOTTOMRIGHT,
              "Success\nB button", f"{success_count}\nreset block")]
    if recorder is not None:
        texts.append((mujoco.mjtFontScale.mjFONTSCALE_150, mujoco.mjtGridPos.mjGRID_BOTTOMLEFT,
                      "REC" if recorder.active else "Standby",
                      f"saved {recorder.saved_count} (total {recorder.next_index})"))
    if success:
        texts.append((mujoco.mjtFontScale.mjFONTSCALE_300, mujoco.mjtGridPos.mjGRID_TOP,
                      "SUCCESS!  Block placed", None))
    viewer.set_texts(texts)


def run_simulation(model, data, state, viewer=None, recorder=None):
    monitor = TaskMonitor(model)
    rng = np.random.default_rng()
    area_geom = model.geom("place_area_geom").id
    area_rgba = model.geom_rgba[area_geom].copy()
    success_count = 0
    success_until = None  # 配置成功後、この時刻（perf_counter）になったらブロックを再配置する
    _, scene_ids = scene_description(model)

    reset_block(model, data, rng)
    show_overlay(viewer, success_count, False, recorder)
    wall_start = time.perf_counter()
    sim_start = data.time

    while viewer is None or viewer.is_running():
        target, grip = state.snapshot()
        apply_control(model, data, target, grip)
        if recorder is not None and recorder.poll(data, state.connected()):
            show_overlay(viewer, success_count, success_until is not None, recorder)
        action_ee = np.append(clamp_target(target), grip)

        # viewer の Backspace でリセットされると data.time が巻き戻るので基準を取り直す
        if data.time < sim_start:
            wall_start, sim_start = time.perf_counter(), data.time

        # 実時間に追いつくまで物理ステップを進める（処理落ち時は 1 回 50 ステップまで）
        wall_elapsed = time.perf_counter() - wall_start
        for _ in range(50):
            if data.time - sim_start >= wall_elapsed:
                break
            if recorder is not None:
                recorder.record(data, action_ee)
            mujoco.mj_step(model, data)
        else:
            wall_start, sim_start = time.perf_counter(), data.time

        if monitor.update(data):
            success_count += 1
            success_until = time.perf_counter() + SUCCESS_HOLD_SEC
            model.geom_rgba[area_geom] = AREA_SUCCESS_RGBA
            if recorder is not None:
                recorder.mark_success(data)
            show_overlay(viewer, success_count, True, recorder)

        reset_reason = None
        if success_until is not None and time.perf_counter() >= success_until:
            reset_reason = "配置成功"
        elif state.pop_block_reset():
            reset_reason = "B ボタン"
        elif success_until is None and monitor.out_of_reach(data):
            reset_reason = "アームの届かない場所に出たため"
        if reset_reason:
            print(f"[Task] ブロックを再配置しました（{reset_reason}）")
            reset_block(model, data, rng)
            monitor.reset()
            success_until = None
            model.geom_rgba[area_geom] = area_rgba
            if recorder is not None:
                recorder.discard(reset_reason)
                recorder.arm()
            show_overlay(viewer, success_count, False, recorder)

        # VR 表示用に毎回最新の状態を渡す（送信は WebSocket 側が STATE_HZ で行う）
        rec = None if recorder is None else {"active": recorder.active, "saved": recorder.saved_count,
                                             "total": recorder.next_index}
        state.publish_scene(scene_poses(data, scene_ids), success_until is not None, rec)

        if viewer is not None:
            viewer.sync()
        time.sleep(0.005)


def main():
    parser = argparse.ArgumentParser(description="Quest 3 VR テレオペ統合サーバー")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-viewer", action="store_true", help="MuJoCo viewer を開かずに実行する")
    parser.add_argument("--record", action="store_true", help="配置成功したエピソードを data/episodes/ に記録する")
    args = parser.parse_args()

    state = TeleopState()
    model, data = load_model()
    scene, _ = scene_description(model)
    start_web_server(state, scene, args.host, args.port)
    recorder = EpisodeRecorder(model, MODEL_XML.read_text(encoding="utf-8")) if args.record else None

    print("\n==========================================")
    print(" Quest 3 VR テレオペ統合サーバー起動")
    print(f"  Web       : http://localhost:{args.port}/")
    print(f"  WebSocket : ws://localhost:{args.port}/ws")
    print("  グリップ  : 押し込むとグリッパーが閉じる")
    print("  A ボタン  : 原点リセット / B ボタン : ブロックを再配置")
    print(f"  配置成功  : 画面にメッセージ表示 → {SUCCESS_HOLD_SEC:.0f} 秒後にブロックをランダムに再配置")
    if recorder is not None:
        print(f"  記録      : 配置成功したエピソードを {recorder.out_dir} に保存（次は episode_{recorder.next_index:06d}）")
    print("==========================================")

    try:
        if args.no_viewer:
            run_simulation(model, data, state, recorder=recorder)
        else:
            with mujoco.viewer.launch_passive(model, data) as viewer:
                with viewer.lock():
                    viewer.cam.lookat[:] = VIEWER_CAMERA["lookat"]
                    viewer.cam.distance = VIEWER_CAMERA["distance"]
                    viewer.cam.azimuth = VIEWER_CAMERA["azimuth"]
                    viewer.cam.elevation = VIEWER_CAMERA["elevation"]
                run_simulation(model, data, state, viewer, recorder)
    except KeyboardInterrupt:
        pass
    print("\nサーバーを終了しました。")


if __name__ == "__main__":
    main()

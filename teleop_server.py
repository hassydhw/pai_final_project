"""
Quest 3 VR テレオペ統合サーバー（Phase 3）

1 つのプロセス・1 つのポートで以下をすべて処理する:
  - GET /    : index.html の配信（WebXR クライアント）
  - GET /ws  : WebSocket（Quest 3 からのコントローラ位置・グリップ値の受信）
  - MuJoCo   : SO-101 アームの IK 追従 + グリッパー開閉 + Pick & Place 物理シミュレーション

スレッド構成:
  - メインスレッド : MuJoCo の物理ループと viewer（GLFW はメインスレッド必須）
  - 別スレッド     : aiohttp の非同期イベントループ（HTTP / WebSocket）

使い方:
  python3 teleop_server.py               # viewer あり（WSLg でウィンドウ表示）
  python3 teleop_server.py --no-viewer   # viewer なし（通信テスト用）
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
HOME_TARGET = np.array([0.15, 0.0, 0.15])  # 初回接続時の手の位置に対応するアーム先端位置
SCALE = 0.5                                # 手の移動量 → アーム移動量の倍率
Z_MIN = 0.018                              # 指先が床にめり込まない最低高さ
R_MIN = 0.06                               # 土台に近づきすぎないための最小水平距離

JOINT_LIMITS = [(-3.14, 3.14), (-1.57, 1.57), (-2.2, 2.2), (-2.0, 2.0)]


class TeleopState:
    """WebSocket スレッドと物理ループで共有する最新入力（スレッドセーフ）"""

    def __init__(self):
        self.lock = threading.Lock()
        self.target_pos = HOME_TARGET.copy()
        self.gripper = 0.0
        self.initial_quest_pos = None
        self._origin_target = HOME_TARGET.copy()

    def reset_origin(self):
        """次に受信した手の位置を新しい原点にする（再接続時・A ボタン押下時）"""
        with self.lock:
            self.initial_quest_pos = None

    def update_from_quest(self, payload):
        q = np.array([payload["x"], payload["y"], payload["z"]], dtype=float)
        grip = float(np.clip(payload.get("gripper", 0.0), 0.0, 1.0))

        with self.lock:
            if payload.get("reset") or self.initial_quest_pos is None:
                # 原点を取り直すときは、アームの現在の目標位置から相対移動を続ける
                self.initial_quest_pos = q
                self._origin_target = self.target_pos.copy() if payload.get("reset") else HOME_TARGET.copy()

            # Quest 3 の座標変化をアームの作業可動域にスケーリング
            delta = (q - self.initial_quest_pos) * SCALE
            self.target_pos = self._origin_target + np.array([delta[2], -delta[0], delta[1]])
            self.gripper = grip

    def snapshot(self):
        with self.lock:
            return self.target_pos.copy(), self.gripper


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


def load_model():
    model = mujoco.MjModel.from_xml_path(str(MODEL_XML))
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, model.key("home").id)
    mujoco.mj_forward(model, data)
    return model, data


class TaskMonitor:
    """Pick & Place の進行状況（持ち上げ・配置成功）を判定してログ出力する"""

    LIFT_Z = 0.04

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

        if self.placed and not in_area:
            # 再挑戦できるよう状態を戻す
            self.lifted = self.placed = False


# ---------------------------------------------------------------------------
# HTTP / WebSocket サーバー（aiohttp）
# ---------------------------------------------------------------------------

def create_app(state):
    async def index(request):
        # トンネル越しに .git や .py が見えないよう、index.html だけを配信する
        return web.FileResponse(INDEX_HTML, headers={"Cache-Control": "no-cache"})

    async def websocket(request):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        print(f"\n[接続成功] Quest 3 Teleop 接続確立: {request.remote}")
        state.reset_origin()

        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    state.update_from_quest(json.loads(msg.data))
                except (ValueError, KeyError, TypeError) as e:
                    print(f"[警告] 不正なデータを無視しました: {e}")
            elif msg.type == WSMsgType.ERROR:
                print(f"[エラー] WebSocket: {ws.exception()}")

        print("[切断] Quest 3 との接続が切れました")
        return ws

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/index.html", index)
    app.router.add_get("/ws", websocket)
    return app


def start_web_server(state, host, port):
    """aiohttp を別スレッドで起動し、待ち受け開始まで待つ"""
    ready = threading.Event()
    error = []

    def run():
        async def main():
            runner = web.AppRunner(create_app(state), access_log=None)
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

def run_simulation(model, data, state, viewer=None):
    monitor = TaskMonitor(model)
    wall_start = time.perf_counter()
    sim_start = data.time

    while viewer is None or viewer.is_running():
        target, grip = state.snapshot()
        apply_control(model, data, target, grip)

        # viewer の Backspace でリセットされると data.time が巻き戻るので基準を取り直す
        if data.time < sim_start:
            wall_start, sim_start = time.perf_counter(), data.time

        # 実時間に追いつくまで物理ステップを進める（処理落ち時は 1 回 50 ステップまで）
        wall_elapsed = time.perf_counter() - wall_start
        for _ in range(50):
            if data.time - sim_start >= wall_elapsed:
                break
            mujoco.mj_step(model, data)
        else:
            wall_start, sim_start = time.perf_counter(), data.time

        monitor.update(data)
        if viewer is not None:
            viewer.sync()
        time.sleep(0.005)


def main():
    parser = argparse.ArgumentParser(description="Quest 3 VR テレオペ統合サーバー")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-viewer", action="store_true", help="MuJoCo viewer を開かずに実行する")
    args = parser.parse_args()

    state = TeleopState()
    start_web_server(state, args.host, args.port)
    model, data = load_model()

    print("\n==========================================")
    print(" Quest 3 VR テレオペ統合サーバー起動")
    print(f"  Web       : http://localhost:{args.port}/")
    print(f"  WebSocket : ws://localhost:{args.port}/ws")
    print("  グリップ  : 押し込むとグリッパーが閉じる / A ボタンで原点リセット")
    print("==========================================")

    try:
        if args.no_viewer:
            run_simulation(model, data, state)
        else:
            with mujoco.viewer.launch_passive(model, data) as viewer:
                run_simulation(model, data, state, viewer)
    except KeyboardInterrupt:
        pass
    print("\nサーバーを終了しました。")


if __name__ == "__main__":
    main()

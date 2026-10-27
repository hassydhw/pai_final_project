import asyncio
import json
import threading
import time
import mujoco
import mujoco.viewer
import numpy as np
import websockets

PORT = 8765

# --- SO-101 ロボットアームのモデル定義 ---
so101_xml = """
<mujoco model="so101_arm">
  <compiler angle="radian" coordinate="local"/>
  <option gravity="0 0 -9.81"/>
  <visual>
    <headlight ambient="0.5 0.5 0.5" diffuse="0.8 0.8 0.8"/>
  </visual>

  <worldbody>
    <light pos="0 0 2" dir="0 0 -1"/>
    <geom name="floor" type="plane" size="1 1 0.1" rgba="0.9 0.9 0.9 1"/>

    <!-- ターゲット位置表示用（赤小球） -->
    <body name="target_site" pos="0.15 0.0 0.15">
      <geom type="sphere" size="0.015" rgba="1 0 0 0.6" conaffinity="0" contype="0"/>
    </body>

    <!-- SO-101 アーム -->
    <body name="base_link" pos="0 0 0">
      <geom name="base_geom" type="cylinder" size="0.05 0.02" rgba="0.2 0.2 0.2 1" pos="0 0 0.01"/>
      
      <body name="link1" pos="0 0 0.02">
        <joint name="joint1" axis="0 0 1" range="-3.14 3.14" damping="0.5"/>
        <geom name="link1_geom" type="capsule" fromto="0 0 0 0 0 0.06" size="0.02" rgba="0.8 0.2 0.2 1"/>

        <body name="link2" pos="0 0 0.06">
          <joint name="joint2" axis="0 1 0" range="-1.57 1.57" damping="0.5"/>
          <geom name="link2_geom" type="capsule" fromto="0 0 0 0 0 0.12" size="0.018" rgba="0.2 0.8 0.2 1"/>

          <body name="link3" pos="0 0 0.12">
            <joint name="joint3" axis="0 1 0" range="-1.57 1.57" damping="0.5"/>
            <geom name="link3_geom" type="capsule" fromto="0 0 0 0 0 0.10" size="0.015" rgba="0.2 0.2 0.8 1"/>

            <body name="link4" pos="0 0 0.10">
              <joint name="joint4" axis="0 1 0" range="-1.57 1.57" damping="0.5"/>
              <geom name="link4_geom" type="capsule" fromto="0 0 0 0 0 0.05" size="0.012" rgba="0.8 0.8 0.2 1"/>

              <body name="gripper_link" pos="0 0 0.05">
                <geom name="gripper_geom" type="box" size="0.015 0.02 0.01" rgba="0.1 0.1 0.1 1"/>
              </body>
            </body>
          </body>
        </body>
      </body>
    </body>
  </worldbody>
</mujoco>
"""

# 共有状態変数（Quest 3からの最新入力）
target_pos = np.array([0.15, 0.0, 0.15])
gripper_val = 0.0
lock = threading.Lock()

# 基準初期位置の記録用
initial_quest_pos = None

async def handle_quest_data(websocket):
    global target_pos, gripper_val, initial_quest_pos
    print(f"\n[接続成功] Quest 3 Teleop 接続確立: {websocket.remote_address}")
    
    try:
        async for message in websocket:
            data = json.loads(message)
            qx, qy, qz = data["x"], data["y"], data["z"]
            grip = data["gripper"]

            # 初回受信時に手の基準位置をキャプチャ
            if initial_quest_pos is None:
                initial_quest_pos = np.array([qx, qy, qz])

            # Quest3の座標変化をアームの作業可動域にスケーリング
            delta = (np.array([qx, qy, qz]) - initial_quest_pos) * 0.5
            
            with lock:
                # ロボットアーム正面の位置を中心として追従
                target_pos = np.array([0.15 + delta[2], -delta[0], 0.15 + delta[1]])
                gripper_val = grip

    except websockets.exceptions.ConnectionClosed:
        print("[切断] Quest 3 との接続が切れました")

def start_websocket_server():
    loop = asyncio.new_event_event_loop() if hasattr(asyncio, 'new_event_event_loop') else asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    async def main():
        async with websockets.serve(handle_quest_data, "0.0.0.0", PORT):
            await asyncio.Event().wait()

    loop.run_until_complete(main())

# 別スレッドでWebSocket通信を常時実行
server_thread = threading.Thread(target=start_websocket_server, daemon=True)
server_thread.start()

# --- MuJoCo シミュレーション & 逆運動学（簡易IK）ループ ---
model = mujoco.MjModel.from_xml_string(so101_xml)
data = mujoco.MjData(model)

print("\n==========================================")
print(" MuJoCo リアルタイム VRテレオペ環境起動中...")
print("==========================================")

with mujoco.viewer.launch_passive(model, data) as viewer:
    while viewer.is_running():
        with lock:
            curr_target = target_pos.copy()
            curr_grip = gripper_val

        # ターゲットマーカー位置の更新
        model.body("target_site").pos = curr_target

        # 簡易IK：ターゲット位置に向け関節角度を制御
        tx, ty, tz = curr_target
        j1 = np.arctan2(ty, tx)
        r = np.sqrt(tx**2 + ty**2)
        j2 = -np.arctan2(tz - 0.08, r - 0.05) * 0.8
        j3 = np.arctan2(tz - 0.08, r) * 0.8

        # 関節角度の反映
        data.qpos[0] = j1
        data.qpos[1] = np.clip(j2, -1.2, 1.2)
        data.qpos[2] = np.clip(j3, -1.2, 1.2)

        mujoco.mj_step(model, data)
        viewer.sync()
        time.sleep(0.01)

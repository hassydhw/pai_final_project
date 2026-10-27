import os
import time
import urllib.request
import mujoco
import mujoco.viewer

# SO-101のMJCFモデル（簡易版XML定義）
so101_xml = """
<mujoco model="so101_arm">
  <compiler angle="radian" coordinate="local"/>
  <option gravity="0 0 -9.81"/>

  <visual>
    <headlight ambient="0.4 0.4 0.4" diffuse="0.8 0.8 0.8"/>
  </visual>

  <worldbody>
    <!-- 床と照明 -->
    <light pos="0 0 2" dir="0 0 -1"/>
    <geom name="floor" type="plane" size="1 1 0.1" rgba="0.9 0.9 0.9 1"/>

    # ロボットアーム（SO-101 構成モデル）
    <body name="base_link" pos="0 0 0">
      <geom name="base_geom" type="cylinder" size="0.05 0.02" rgba="0.2 0.2 0.2 1" pos="0 0 0.01"/>
      
      <!-- Joint 1: Base Rotation -->
      <body name="link1" pos="0 0 0.02">
        <joint name="joint1" axis="0 0 1" range="-3.14 3.14"/>
        <geom name="link1_geom" type="capsule" fromto="0 0 0 0 0 0.06" size="0.02" rgba="0.8 0.2 0.2 1"/>

        <!-- Joint 2: Shoulder Pitch -->
        <body name="link2" pos="0 0 0.06">
          <joint name="joint2" axis="0 1 0" range="-1.57 1.57"/>
          <geom name="link2_geom" type="capsule" fromto="0 0 0 0 0 0.12" size="0.018" rgba="0.2 0.8 0.2 1"/>

          <!-- Joint 3: Elbow Pitch -->
          <body name="link3" pos="0 0 0.12">
            <joint name="joint3" axis="0 1 0" range="-1.57 1.57"/>
            <geom name="link3_geom" type="capsule" fromto="0 0 0 0 0 0.10" size="0.015" rgba="0.2 0.2 0.8 1"/>

            <!-- Joint 4: Wrist Pitch -->
            <body name="link4" pos="0 0 0.10">
              <joint name="joint4" axis="0 1 0" range="-1.57 1.57"/>
              <geom name="link4_geom" type="capsule" fromto="0 0 0 0 0 0.05" size="0.012" rgba="0.8 0.8 0.2 1"/>

              <!-- End Effector (Gripper) -->
              <body name="gripper_base" pos="0 0 0.05">
                <geom name="gripper_geom" type="box" size="0.015 0.02 0.01" rgba="0.1 0.1 0.1 1"/>
                <site name="ee_site" pos="0 0 0.02" size="0.005" rgba="1 0 0 1"/>
              </body>
            </body>
          </body>
        </body>
      </body>
    </body>
  </worldbody>
</mujoco>
"""

print("SO-101モデルを読み込んでいます...")
model = mujoco.MjModel.from_xml_string(so101_xml)
data = mujoco.MjData(model)

print("MuJoCo ビューアを起動します。マウス操作でアームを回転・拡大観察できます。")
print("※画面を閉じるか Ctrl+C で終了できます。")

with mujoco.viewer.launch_passive(model, data) as viewer:
    # 初期姿勢を設定してアームを動かしてみる
    data.qpos[0] = 0.5   # Joint 1
    data.qpos[1] = -0.4  # Joint 2
    data.qpos[2] = 0.8   # Joint 3
    
    while viewer.is_running():
        mujoco.mj_step(model, data)
        viewer.sync()
        time.sleep(0.01)

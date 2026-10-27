import time
import mujoco
import mujoco.viewer

# 最小限のテスト用3Dシーン（床と緑色の立方体）
xml = """
<mujoco>
  <worldbody>
    <light name="top" pos="0 0 2"/>
    <geom name="floor" type="plane" size="1 1 0.1" rgba=".9 .9 .9 1"/>
    <body name="box" pos="0 0 0.5">
      <freejoint/>
      <geom name="box_geom" type="box" size="0.1 0.1 0.1" rgba="0 0.8 0.4 1"/>
    </body>
  </worldbody>
</mujoco>
"""

model = mujoco.MjModel.from_xml_string(xml)
data = mujoco.MjData(model)

print("MuJoCoの3D画面を起動します...")
with mujoco.viewer.launch_passive(model, data) as viewer:
    start = time.time()
    while viewer.is_running() and time.time() - start < 5:
        mujoco.mj_step(model, data)
        viewer.sync()
        time.sleep(0.01)

print("動作確認完了です！")



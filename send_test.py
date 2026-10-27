import asyncio
import json
import math
import websockets

async def send_dummy_pose():
    uri = "ws://localhost:8000/ws"
    print(f"サーバー ({uri}) に接続を試みます...")

    async with websockets.connect(uri) as websocket:
        print("接続成功！ダミーのコントローラ位置データを送信します...")

        # 手を円を描くように動かしつつ、グリップを開閉するダミーデータ（約 3 秒）
        for i in range(180):
            t = i / 60
            dummy_data = {
                "x": 0.1 * math.sin(t * 2),
                "y": 1.2 + 0.05 * math.cos(t * 2),
                "z": -0.3,
                "gripper": round((math.sin(t * 3) + 1) / 2, 2)  # グリップボタンの押し込み度合い (0.0〜1.0)
            }
            await websocket.send(json.dumps(dummy_data))
            await asyncio.sleep(1 / 60)

        print(f"送信完了（最後のデータ: {dummy_data}）")

if __name__ == "__main__":
    asyncio.run(send_dummy_pose())

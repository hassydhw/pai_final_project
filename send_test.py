import asyncio
import json
import websockets

async def send_dummy_pose():
    uri = "ws://localhost:8765"
    print(f"サーバー ({uri}) に接続を試みます...")
    
    async with websockets.connect(uri) as websocket:
        print("接続成功！ダミーのコントローラ位置データを送信します...")
        
        # ダミーのコントローラデータ（位置 x,y,z と グリップ状態）
        dummy_data = {
            "x": 0.15,
            "y": 0.20,
            "z": 0.35,
            "gripper": 0.8  # グリップボタンの押し込み度合い (0.0〜1.0)
        }
        
        await websocket.send(json.dumps(dummy_data))
        print(f"送信完了: {dummy_data}")

if __name__ == "__main__":
    asyncio.run(send_dummy_pose())



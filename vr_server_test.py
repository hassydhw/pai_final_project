import asyncio
import json
import websockets

PORT = 8765

async def handle_quest_data(websocket):
    print(f"\n[接続成功] クライアントが接続しました: {websocket.remote_address}")
    try:
        async for message in websocket:
            # 送られてきたJSONデータを解析
            data = json.loads(message)
            print(f"受信データ: {data}")
    except websockets.exceptions.ConnectionClosed:
        print("[切断] クライアントとの接続が閉じられました。")

async def main():
    print("==========================================")
    print(" Quest 3 受信テスト用 WebSocket サーバー起動中...")
    print(f" ポート番号: {PORT}")
    print("==========================================")
    async with websockets.serve(handle_quest_data, "0.0.0.0", PORT):
        await asyncio.Event().wait()  # サーバーを永続実行（待機）

if __name__ == "__main__":
    asyncio.run(main())



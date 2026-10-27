# Meta Quest 3 と WSL2 (MuJoCo) を用いた VR ロボットテレオペレーション環境構築ガイド

## 1. システム概要とアーキテクチャ構成

本ドキュメントは、Meta Quest 3 の VR コントローラ位置情報を利用して、WSL2 (Ubuntu) 上の物理シミュレータ MuJoCo 内の 5 軸ロボットアーム (SO-101) をリアルタイムで遠隔操作（テレオペレーション）するシステムの構築過程と技術仕様をまとめたものである。

本システムは、VR ヘッドセット側の WebXR (Three.js) アプリケーション、暗号化通信を担う WebSocket / LocalTunnel インフラ、および逆運動学 (IK) と物理演算を行う Python / MuJoCo バックエンドから構成される。

```
[ Meta Quest 3 (Meta Quest Browser) ]
  │  ・WebXR (Three.js) による 3D 空間描画（グリッド床、コントローラ位置表示）
  │  ・右手コントローラの 3 次元座標 (x, y, z) および Grip 値の取得
  ▼ HTTPS / WSS 暗号化通信 (LocalTunnel: ポート 8000 / 8765)
[ Windows 11 (ホストOS) ]
  │  ・ネットワークプロファイル: プライベート設定
  │  ・Windows Defender ファイアウォール規則（Port 8000 / 8765 許可）
  ▼ ミラーリング / ポートフォワーディング
[ WSL2 (Ubuntu / Python venv: pai_env) ]
  ├─ HTTP サーバー (Python http.server: Port 8000) -> index.html 配信
  ├─ WebSocket サーバー (Python websockets: Port 8765) -> 受信スレッド
  └─ MuJoCo 物理シミュレータ (teleop_mujoco.py)
       ・ターゲットマーカー (target_site) 位置更新
       ・簡易逆運動学 (IK) による関節角度 (joint1~3) 算出
       ・SO-101 ロボットアームのリアルタイム追従描画 (WSLg)
```

| 構成要素 | 採用技術・ライブラリ | 役割・機能概要 |
| :--- | :--- | :--- |
| **VR クライアント** | Meta Quest Browser, WebXR API, Three.js | コントローラの姿勢・入力のトラッキングおよび 3D 視覚フィードバック |
| **暗号化通信トンネル** | LocalTunnel (`npx localtunnel`) | WebXR の セキュリティ要件 (HTTPS/WSS) を充足するための TLS トンネル |
| **通信バックエンド** | Python `websockets`, `asyncio` | 非同期 WebSocket サーバーによるリアルタイム JSON データの受信 |
| **物理演算・描画** | MuJoCo (`mujoco`), WSLg, NumPy | 3D 物理シミュレーションおよびリアルタイム逆運動学 (IK) によるアーム制御 |

---

## 2. 開発環境のセットアップ（WSL2 / Python venv / MuJoCo）

開発環境は、Windows 11 上の WSL2 (Ubuntu) 環境に独立した Python 仮想環境を構築してセットアップを行った。仮想環境を作業ディレクトリ外（ホーム直下）に作成することで、Git 管理時の不要ファイルの混入を防止している。

### 2.1 作業ディレクトリと仮想環境の作成

```bash
# 作業用ディレクトリの作成と移動
mkdir -p ~/pai_final_project
cd ~/pai_final_project

# 開発用 Python 仮想環境の作成と有効化
python3 -m venv ~/pai_env
source ~/pai_env/bin/activate
```

有効化後、ターミナルプロンプトの先頭に `(pai_env)` が表示されることを確認した。

### 2.2 依存ライブラリのインストール

物理シミュレーション、数値計算、逆運動学、通信に必要なパッケージを構成した。

```bash
pip install --upgrade pip
pip install mujoco numpy scipy websockets ikpy
```

### 2.3 WSLg 描画および MuJoCo 動作確認

WSL2 上で 3D グラフィック画面（WSLg）が正しく起動するか確認するため、最小構成の MJCF XML（床と立方体）を用いた判定スクリプト `test_mujoco.py` を作成・実行した。

```python
import time
import mujoco
import mujoco.viewer

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

with mujoco.viewer.launch_passive(model, data) as viewer:
    start = time.time()
    while viewer.is_running() and time.time() - start < 5:
        mujoco.mj_step(model, data)
        viewer.sync()
        time.sleep(0.01)
```

実行結果として、Windows 上に 3D ウィンドウがポップアップし、緑色の立方体が重力に従って落下することを確認し、WSLg グラフィック描画環境の正常性を検証した。さらに、SO-101 アームの MJCF モデル定義（`load_so101.py`）を読み込み、多関節ロボットアームの重力下での挙動を確認した。

---

## 3. WebSocket 通信基盤とネットワーク透過性の確保

### 3.1 Python WebSocket サーバーの構築

Meta Quest 3 から送信されるコントローラ位置姿勢データをリアルタイムで非同期受信する WebSocket サーバー（`vr_server_test.py`）を作成した。

```python
import asyncio
import json
import websockets

PORT = 8765

async def handle_quest_data(websocket):
    print(f"\n[接続成功] クライアント接続: {websocket.remote_address}")
    try:
        async for message in websocket:
            data = json.loads(message)
            print(f"受信データ: {data}")
    except websockets.exceptions.ConnectionClosed:
        print("[切断] クライアント接続終了")

async def main():
    print(f"WebSocket サーバー起動中 (Port: {PORT})...")
    async with websockets.serve(handle_quest_data, "0.0.0.0", PORT):
        await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
```

### 3.2 ネットワーク障害の分析と解決プロセス

Meta Quest 3（Wi-Fi 接続）から WSL2 上のサーバーへアクセスする際、以下の 3 点のネットワーク的隔壁が発生したため、順次対応を行った。

1. **Windows ネットワークプロファイル制限**:
   - **症状**: 同一 Wi-Fi 上の外部機器から PC の IP アドレス（`192.168.10.112`）へのアクセスがタイムアウト。
   - **解決策**: Windows の「設定」>「ネットワークとインターネット」にて、接続設定を「パブリック」から「プライベートネットワーク」へ変更。
2. **Windows Defender ファイアウォールのブロック**:
   - **症状**: 外部からの Port 8000 / 8765 への TCP 着信が遮断。
   - **解決策**: 管理者権限 PowerShell にて受信ルールを追加。
     ```powershell
     New-NetFirewallRule -DisplayName "WSL Quest3 8000" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
     New-NetFirewallRule -DisplayName "WSL Quest3 8765" -Direction Inbound -LocalPort 8765 -Protocol TCP -Action Allow
     ```
3. **WebXR の HTTPS / WSS セキュリティ要件（Mixed Content 制限）**:
   - **症状**: Quest Browser 上で WebXR セッション（VR モード）を開始するには HTTPS 接続が必須。また、`https://` ページ内から平文の `ws://` 通信を呼ぶとブラウザの Mixed Content ポリシーにより接続が強制遮断される。
   - **解決策**: `localtunnel` を用いて、Web 配信（Port 8000）および WebSocket 通信（Port 8765）の両者を暗号化トンネル化。
     - Web 用: `npx localtunnel --port 8000` -> `https://<web-domain>.loca.lt`
     - WS 用: `npx localtunnel --port 8765` -> `wss://<ws-domain>.loca.lt`

---

## 4. WebXR (Three.js) フロントエンドの実装

VR ヘッドセット内で操作者が直感的に位置関係を把握できるよう、Three.js を組み込んだ WebXR アプリケーション（`index.html`）を開発した。

### 4.1 主要機能
- **3D 視覚フィードバック**: 青色の空間グリッド床および右手コントローラ位置を示す赤色球体マーカーのリアルタイム描画。
- **WSS 自動接続**: 入力されたドメイン名を認識し、プロトコルを暗号化 WebSocket (`wss://`) に自動変換して接続。
- **フレーム同期トラッキング**: `renderer.setAnimationLoop` 内で右手コントローラの 3 次元位置座標 (`pos.x`, `pos.y`, `pos.z`) および Grip ボタンの押し込み値 (`gripper`) を取得し、JSON データとして連続送信。

```html
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>Quest 3 VR Teleop Controller</title>
    <style>
        body { font-family: sans-serif; text-align: center; background: #111827; color: #fff; margin: 0; overflow: hidden; }
        #info { position: absolute; top: 20px; width: 100%; text-align: center; z-index: 100; }
        input { font-size: 18px; width: 320px; padding: 10px; text-align: center; border-radius: 8px; border: none; }
        button { font-size: 20px; padding: 12px 24px; margin-top: 15px; border-radius: 8px; cursor: pointer; background: #0284c7; color: white; border: none; }
        #status { font-size: 18px; color: #38bdf8; margin-top: 15px; font-weight: bold; }
    </style>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
</head>
<body>
    <div id="info">
        <h1>Quest 3 VR Teleop</h1>
        <p>WebSocket トンネルドメインを入力してください</p>
        <input type="text" id="ipInput" placeholder="例: pretty-dog-12.loca.lt">
        <br>
        <button id="startBtn">VR開始 & 通信スタート</button>
        <div id="status">未接続</div>
    </div>

    <script>
        let ws = null, scene, camera, renderer, controllerRight;
        const startBtn = document.getElementById('startBtn');
        const statusDiv = document.getElementById('status');

        function initThree() {
            scene = new THREE.Scene();
            scene.background = new THREE.Color(0x111827);
            camera = new THREE.PerspectiveCamera(70, window.innerWidth / window.innerHeight, 0.1, 10);

            const gridHelper = new THREE.GridHelper(10, 10, 0x38bdf8, 0x334155);
            scene.add(gridHelper);

            const light = new THREE.HemisphereLight(0xffffff, 0x444444, 1);
            light.position.set(0, 1, 0);
            scene.add(light);

            const sphereGeo = new THREE.SphereGeometry(0.03, 16, 16);
            const sphereMat = new THREE.MeshStandardMaterial({ color: 0xef4444 });
            const controllerMesh = new THREE.Mesh(sphereGeo, sphereMat);

            renderer = new THREE.WebGLRenderer({ antialias: true });
            renderer.setSize(window.innerWidth, window.innerHeight);
            renderer.xr.enabled = true;
            document.body.appendChild(renderer.domElement);

            controllerRight = renderer.xr.getController(0);
            controllerRight.add(controllerMesh);
            scene.add(controllerRight);
        }

        initThree();

        startBtn.addEventListener('click', async () => {
            let inputVal = document.getElementById('ipInput').value.trim();
            if (!inputVal) return;

            inputVal = inputVal.replace(/^https?:\/\//, '').replace(/^wss?:\/\//, '');
            const wsUrl = inputVal.includes('loca.lt') ? `wss://${inputVal}` : `ws://${inputVal}:8765`;

            statusDiv.innerText = `接続中... (${wsUrl})`;
            ws = new WebSocket(wsUrl);

            ws.onopen = async () => {
                statusDiv.innerText = "WebSocket 接続成功！VRを開始します...";
                document.getElementById('info').style.display = 'none';

                try {
                    const session = await navigator.xr.requestSession('immersive-vr', {
                        optionalFeatures: ['local-floor', 'bounded-floor']
                    });
                    await renderer.xr.setSession(session);

                    renderer.setAnimationLoop(() => {
                        renderer.render(scene, camera);

                        if (ws && ws.readyState === WebSocket.OPEN && controllerRight) {
                            const pos = controllerRight.position;
                            let gripVal = 0;
                            const sources = session.inputSources;
                            for (let source of sources) {
                                if (source.handedness === 'right' && source.gamepad) {
                                    if (source.gamepad.buttons) {
                                        gripVal = source.gamepad.buttons.value;
                                    }
                                }
                            }

                            const payload = {
                                x: parseFloat(pos.x.toFixed(3)),
                                y: parseFloat(pos.y.toFixed(3)),
                                z: parseFloat(pos.z.toFixed(3)),
                                gripper: parseFloat(gripVal.toFixed(2))
                            };
                            ws.send(JSON.stringify(payload));
                        }
                    });
                } catch (err) {
                    alert("VRセッション開始失敗: " + err.message);
                }
            };

            ws.onerror = () => { statusDiv.innerText = "接続エラー"; };
            ws.onclose = () => { statusDiv.innerText = "切断されました"; };
        });
    </script>
</body>
</html>
```

---

## 5. MuJoCo 統合テレオペレーションと逆運動学 (IK) 制御

受信用 WebSocket サーバーと MuJoCo シミュレーション描画を並列動作させるため、Python のマルチスレッド (`threading`) 構成を採用した統合制御スクリプト `teleop_mujoco.py` を実装した。

### 5.1 統合制御アルゴリズム
1. **別スレッド通信ループ**: 非同期イベントループを別スレッドで実行し、Quest 3 から送信される最新の `x, y, z` 座標をスレッドセーフ（`threading.Lock`）に共有変数 `target_pos` へ保存。
2. **位置座標のスケーリングと原点設定**: 初回受信時の手の座標を原点 `initial_quest_pos` として記録し、人体とロボットの可動域差分を補正するため移動量をスケーリング（`0.5` 倍）。
3. **簡易逆運動学 (IK)**: 受信ターゲット位置 $(x_t, y_t, z_t)$ に対し、関節角度 $(j_1, j_2, j_3)$ を三角関数演算により算出して反映。
   - Base Rotation ($j_1$): $\theta_1 = \arctan2(y_t, x_t)$
   - Shoulder / Elbow Pitch ($j_2, j_3$): 水平距離 $r = \sqrt{x_t^2 + y_t^2}$ および高度 $z_t$ に応じた俯仰角算出。
4. **シミュレーション同期**: `model.body("target_site").pos` に赤色球体マーカーを更新描画し、アーム関節角度 `data.qpos` を追従更新。

```python
import asyncio
import json
import threading
import time
import mujoco
import mujoco.viewer
import numpy as np
import websockets

PORT = 8765

# (SO-101 MJCF XML 定義は省略)

target_pos = np.array([0.15, 0.0, 0.15])
gripper_val = 0.0
lock = threading.Lock()
initial_quest_pos = None

async def handle_quest_data(websocket):
    global target_pos, gripper_val, initial_quest_pos
    print(f"\n[接続成功] Quest 3 Teleop 接続確立: {websocket.remote_address}")
    
    try:
        async for message in websocket:
            data = json.loads(message)
            qx, qy, qz = data["x"], data["y"], data["z"]
            grip = data["gripper"]

            if initial_quest_pos is None:
                initial_quest_pos = np.array([qx, qy, qz])

            delta = (np.array([qx, qy, qz]) - initial_quest_pos) * 0.5
            
            with lock:
                target_pos = np.array([0.15 + delta[0], -delta[1], 0.15 + delta[2]])
                gripper_val = grip

    except websockets.exceptions.ConnectionClosed:
        print("[切断] Quest 3 接続切断")

def start_websocket_server():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    async def main():
        async with websockets.serve(handle_quest_data, "0.0.0.0", PORT):
            await asyncio.Event().wait()

    loop.run_until_complete(main())

# WebSocket 通信用バックグラウンドスレッド起動
server_thread = threading.Thread(target=start_websocket_server, daemon=True)
server_thread.start()

# シミュレーションメインループ
model = mujoco.MjModel.from_xml_string(so101_xml)
data = mujoco.MjData(model)

with mujoco.viewer.launch_passive(model, data) as viewer:
    while viewer.is_running():
        with lock:
            curr_target = target_pos.copy()
            curr_grip = gripper_val

        model.body("target_site").pos = curr_target

        tx, ty, tz = curr_target
        j1 = np.arctan2(ty, tx)
        r = np.sqrt(tx**2 + ty**2)
        j2 = -np.arctan2(tz - 0.08, r - 0.05) * 0.8
        j3 = np.arctan2(tz - 0.08, r) * 0.8

        data.qpos[0] = j1
        data.qpos[1] = np.clip(j2, -1.2, 1.2)
        data.qpos[2] = np.clip(j3, -1.2, 1.2)

        mujoco.mj_step(model, data)
        viewer.sync()
        time.sleep(0.01)
```

---

## 6. トラブルシューティングガイド

構築過程において発生した主なエラーとその対処法を以下に記録する。

| 事象・エラーログ | 発生原因 | 解決手順・コマンド |
| :--- | :--- | :--- |
| `AttributeError: module 'asyncio' has no attribute 'future'` | 非同期処理の API 指定ミス (`asyncio.future()`) | `asyncio.Event().wait()` に変更し非同期ループ待機を正常化 |
| `OSError: [Errno 98] Address already in use` | 以前のプロセスが Port 8765 を占有 | `fuser -k 8765/tcp` または `pkill -f vr_server_test.py` によりプロセス開放 |
| Quest Browser で VR 起動後画面が真っ暗 | Three.js の WebGL レンダラー/シーン未初期化 | Three.js を導入し `GridHelper` とライト・カメラ環境を構築 |
| VR 内から WS 通信が接続不可（未接続状態） | HTTPS ページからの `ws://` 呼び出しによる Mixed Content ブロック | WebSocket 側も LocalTunnel 経由で `wss://` 化して接続 |

---

## 7. 今後の拡張および自動化ロードマップ

本基盤の完成を踏まえ、提出物（最終課題）に向けた次の実装ステップを以下のように提示する。

1. **サーバー・トンネルの 1 ポート統合**:
   - `aiohttp` または `FastAPI` を導入し、Web アプリ配信 (Port 8000) と WebSocket 通信 (Port 8765) を 1 つの Python プロセスに統合。
   - `npx localtunnel --port 8000 --subdomain my-so101-teleop` により URL を固定し、Quest 3 での毎回の手入力を全廃。
2. **グリッパー動作用アクチュエータの制御統合**:
   - `gripper` 値 (0.0 ～ 1.0) を SO-101 先端グリッパーの関節角度にマッピングし、物体の把握動作を実現。
3. **Pick & Place タスク環境の構築**:
   - MuJoCo 空間内に把握対象オブジェクト（ブロック）と目標配置場所（コンテナ）を追加。
4. **LeRobot 模倣学習用データ収集（データレコーダー）**:
   - テレオペ実行中の関節角度 $\mathbf{q}(t)$、エンドエフェクタ位置、および視覚画像をエピソードデータ（HDF5 / Zarr 形式）として記録し、LeRobot（ACT / Diffusion Policy）による学習パイプラインへ接続。

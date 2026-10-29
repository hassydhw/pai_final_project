# CLAUDE.md

東大 PAI 応用コース最終課題「Meta Quest 3 + WebXR による VR テレオペ & MuJoCo 上の SO-101 アーム連動環境」。
やり取りは日本語で行う。詳しい経緯・設計判断は `261029-phase3-handover.md`（以下、引継書）を参照。

## 作業のルール（必ず守る）

- **git のコミットはユーザーが VS Code から手動で行う。** 頼まれない限り `git add` / `git commit` をしない。
- git は **WSL 側の git**（`/usr/bin/git`）を使う。Windows 版 git は dubious ownership になり、実行ビットも落ちる。
- 改行は **LF**（`.gitattributes` で `* text=auto eol=lf`。WSL の git は `core.autocrlf=true` なので CRLF に注意）。
- `.sh` を追加したら実行ビット（`git ls-files -s` で 100755）を確認する。
- **実機（Quest 3）での確認はユーザーが行う。** Claude はヘッドレスのテストで検証する（トンネルの外部公開や MuJoCo ウィンドウの表示を伴うため）。

## 構成

```
Quest 3 (index.html: WebXR + Three.js r128)
  │  https://my-so101-teleop.loca.lt/ と wss://.../ws（LocalTunnel 1 本）
  ▼
teleop_server.py（ポート 8000）
  ├─ aiohttp（別スレッド）: GET / → index.html のみ、GET /ws → WebSocket
  └─ MuJoCo（メインスレッド）: IK → data.ctrl、Pick & Place 判定、viewer（WSLg）
```

| ファイル | 役割 |
|---|---|
| `teleop_server.py` | 本体。調整用の定数は先頭にまとまっている |
| `models/so101_pick_place.xml` | MJCF（アーム、2 本指グリッパー、赤ブロック、緑の目標エリア） |
| `index.html` | WebXR クライアント（同じホストの `/ws` に自動接続） |
| `start_teleop.sh` | サーバー + LocalTunnel の一括起動（ユーザーが実行する） |
| `test_pick_place.py` | ヘッドレスの Pick & Place 回帰テスト（初期位置 + ランダム配置の四隅） |
| `send_test.py` | `ws://localhost:8000/ws` にダミーの姿勢を送る |
| `teleop_mujoco.py`, `load_so101.py`, `test_mujoco.py`, `vr_server_test.py` | Phase 2 以前の旧ファイル（使っていない） |

## 検証コマンド

```bash
source ~/pai_env/bin/activate && cd ~/pai_final_project
python test_pick_place.py    # 最後に「✅ Pick & Place テスト成功」

# スモークテスト（8000 番と被らないポートで）
timeout 10 python -u teleop_server.py --no-viewer --port 8011 &
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8011/                  # 200
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8011/teleop_server.py  # 404
```

- オフスクリーン描画は `MUJOCO_GL=egl` + `mujoco.Renderer`（1 プロセスで 1 個を使い回す。終了時の `EGLError` は無視してよい）。
- node は nvm 経由（`source ~/.nvm/nvm.sh`）。PATH には Windows 側の `npx`（`/mnt/c/...`）も入っているので注意。

## 変更するときの注意（詳細は引継書 §4）

- 制御は `data.ctrl`（position アクチュエータ）で行い、`qpos` を直接書かない（接触が破綻する）。
- qpos のインデックスは決め打ちせず `model.joint(name).qposadr` で取る（先頭は `red_block` の freejoint）。
- `SHOULDER_Z`・`L1`・`L2`・`L_TOOL`・`FINGER_OPEN` は XML と一致させる。
- 座標の変換：`target = origin + [-dz, -dx, dy] * SCALE`（WebXR → ロボット座標）。
- ブロックの初期配置（ランダムな範囲）は、先端が届く r ≈ 0.217 m の内側に収める（掴める限界は指の幅の分だけ広く r ≈ 0.24 m）。配置や `TaskMonitor.REACH_R` を変えたら `test_pick_place.py` を回す。
- viewer に重ねる文字（`viewer.set_texts`）は ASCII のみ。
- HTTP で配信するのは `index.html` だけ（ルートを静的配信すると `.git` や `.py` が見える）。

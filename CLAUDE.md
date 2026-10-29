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
| `recorder.py` | エピソード記録（`teleop_server.py --record`）。配置成功で `data/episodes/episode_XXXXXX.hdf5` に保存、B ボタン・切断などで破棄 |
| `render_episodes.py` | 記録の `sim/qpos` から EGL でカメラ画像（`cam_front` / `cam_wrist`）を描いて `observation/images/*` に追加 |
| `test_recorder.py` | 記録 → 画像追加のヘッドレス結合テスト（実時間で約 17 秒） |
| `convert_to_lerobot.py` | 記録を LeRobot 形式（`data/lerobot/so101_pick_place`、画像は PNG）に変換。`~/lerobot_env` で実行 |
| `eval_policy.py` | 学習した方策を MuJoCo 上で動かし成功率を測る（GIF を `outputs/eval/` に保存）。`~/lerobot_env` で実行 |
| `teleop_mujoco.py`, `load_so101.py`, `test_mujoco.py`, `vr_server_test.py` | Phase 2 以前の旧ファイル（使っていない） |

## 検証コマンド

```bash
source ~/pai_env/bin/activate && cd ~/pai_final_project
python test_pick_place.py    # 最後に「✅ Pick & Place テスト成功」
python test_recorder.py      # 最後に「✅ 記録テスト成功」（記録・画像まわりを変えたとき）

# スモークテスト（8000 番と被らないポートで）
timeout 10 python -u teleop_server.py --no-viewer --port 8011 &
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8011/                  # 200
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8011/teleop_server.py  # 404
```

- オフスクリーン描画は `MUJOCO_GL=egl` + `mujoco.Renderer`（1 プロセスで 1 個を使い回す。終了時の `EGLError` は無視してよい）。
## 学習（`~/lerobot_env`：LeRobot 0.6.1 + torch CUDA。`~/pai_env` とは numpy の版が合わないので別の venv）

```bash
~/lerobot_env/bin/python convert_to_lerobot.py --overwrite
~/lerobot_env/bin/lerobot-train --dataset.repo_id=local/so101_pick_place --dataset.root=data/lerobot/so101_pick_place \
  --policy.type=act --policy.device=cuda --policy.push_to_hub=false --output_dir=outputs/train/<名前> \
  --steps=<N> --batch_size=8 --save_freq=5000 --wandb.enable=false --num_workers=12
~/lerobot_env/bin/python eval_policy.py outputs/train/<名前>/checkpoints/last/pretrained_model --episodes 10
```
- 本番の学習の前に、ユーザーへ準備（WSL メモリ・電源・スリープ・冷却など）をリマインドする。
- 目安（RTX 3080 Ti Laptop）：GPU で約 0.33 秒/ステップ（CPU は約 2.9 秒/ステップ）。チェックポイント 1 個 591MB。

- node は nvm 経由（`source ~/.nvm/nvm.sh`）。PATH には Windows 側の `npx`（`/mnt/c/...`）も入っているので注意。

## 変更するときの注意（詳細は引継書 §4）

- 制御は `data.ctrl`（position アクチュエータ）で行い、`qpos` を直接書かない（接触が破綻する）。
- qpos のインデックスは決め打ちせず `model.joint(name).qposadr` で取る（先頭は `red_block` の freejoint）。
- `SHOULDER_Z`・`L1`・`L2`・`L_TOOL`・`FINGER_OPEN` は XML と一致させる。
- 座標の変換：`target = origin + [-dz, -dx, dy] * SCALE`（WebXR → ロボット座標）。ただし高さだけは VR 内の机からの手の高さ（絶対値）。
- VR 内の机の高さは、VR 開始時の目の高さ（クライアントが `eye` と `recenter` を送る）にアームの最上部（`ARM_TOP_Z`）が来るよう固定する。A ボタンは前後左右の原点だけを取り直す。`eye` が来なければ従来どおり手の位置に合わせる。
- ブロックの初期配置（ランダムな範囲）は、先端が届く r ≈ 0.217 m の内側に収める（掴める限界は指の幅の分だけ広く r ≈ 0.24 m）。配置や `TaskMonitor.REACH_R` を変えたら `test_pick_place.py` を回す。
- 目標の球（`target_site`）は geom group 2。記録用の画像には写さない（手の位置 = 行動なので学習データに混ぜない）。
- viewer に重ねる文字（`viewer.set_texts`）は ASCII のみ。
- HTTP で配信するのは `index.html` だけ（ルートを静的配信すると `.git` や `.py` が見える）。

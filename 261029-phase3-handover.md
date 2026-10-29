# 引継書：Phase 3 完了時点（2026-10-29）

Windows 側の VS Code（`\\wsl.localhost\...` 経由）で進めてきた作業を、WSL 接続の VS Code（Remote - WSL）に移すための引継書。
**次の担当（WSL 側の Claude Code）は、まずこのファイルを読み、次に `261028-quest3-wsl2-teleop-setup-guide.md`（Phase 2 までの構築ガイド）を読むこと。**

- やり取りは日本語で行う。
- 最終目標：東大 PAI 応用コース最終課題「Meta Quest 3 + WebXR による VR テレオペ & MuJoCo 上の SO-101 アーム連動環境」。

---

## 1. 作業のルール（必ず守る）

| ルール | 理由 |
|---|---|
| **git のコミットはユーザーが VS Code から手動で行う。** Claude は頼まれない限りコミットしない | ユーザーの方針（2026-10-28 に決定） |
| git は **WSL 側の git** を使う。Windows 版 git（Sourcetree / `\\wsl.localhost` 経由）は使わない | Windows 版だと dubious ownership エラーになり、実行ビットも落ちる（`start_teleop.sh` が 100644 で記録されたことがある） |
| 改行は LF（`.gitattributes` の `* text=auto eol=lf`） | WSL の git は `core.autocrlf=true`。CRLF になると `.sh` が bash で動かない |
| `.sh` を新しく追加したら、実行ビットが付いているか（100755）を `git ls-files -s` で確認する | 上記と同じ |
| 実機（Quest 3）での確認はユーザーが行う。Claude はヘッドレスのテストで検証する | トンネルを外部公開する操作で、MuJoCo のウィンドウもユーザーの画面に出るため |

---

## 2. 現在の構成（Phase 3 完了）

```
[Meta Quest 3 / Quest Browser]  index.html（WebXR + Three.js r128）
   │  https://my-so101-teleop.loca.lt/      ← index.html を取得
   │  wss://my-so101-teleop.loca.lt/ws      ← 開いたページと同じホストに自動接続（入力欄なし）
   ▼  LocalTunnel（1 本、サブドメイン固定）
[WSL2 Ubuntu]  teleop_server.py（ポート 8000 の 1 本）
   ├─ aiohttp（別スレッド）: GET / → index.html、GET /ws → WebSocket
   └─ MuJoCo（メインスレッド）: IK + position アクチュエータ + Pick & Place + viewer（WSLg）
```

Phase 2 では 8000 番（http.server）と 8765 番（websockets）の 2 本で、トンネルも 2 本だった。これを統合した。8765 番は使っていない。

### 起動方法
```bash
cd ~/pai_final_project
./start_teleop.sh            # サーバー + LocalTunnel をまとめて起動。Ctrl+C か MuJoCo ウィンドウを閉じると全部止まる
./start_teleop.sh --no-viewer   # 引数は teleop_server.py にそのまま渡る
SUBDOMAIN=foo ./start_teleop.sh # サブドメインを変える
```
- Quest 3 では表示された URL を開き、初回だけトンネルパスワード（スクリプトが表示する）を入力する。
- `start_teleop.sh` の中身：`~/pai_env` の venv と `~/.nvm` の node を読み込む → 8000 番のポートを空ける → サーバー起動 → `npx localtunnel --port 8000 --subdomain my-so101-teleop`（切れたら 3 秒後に張り直す。ログは `logs/tunnel.log`）。

### Quest 3 での操作
| 入力 | 動作 |
|---|---|
| 右手コントローラの位置 | アーム先端の目標位置（初回受信時の位置が原点。手の移動量の 0.5 倍で動く） |
| グリップ（`buttons[1]`, 0〜1） | グリッパー（0 = 全開、1 = 全閉） |
| A（`buttons[4]`） | 原点リセット（`reset: true`）。今のアーム目標位置から続けて動く |
| B（`buttons[5]`） | 赤ブロックを再配置（`reset_block: true`） |

WebSocket で送る JSON：`{x, y, z, gripper, reset?, reset_block?}`

---

## 3. ファイル一覧

| ファイル | 役割 |
|---|---|
| `teleop_server.py` | **本体。** HTTP / WS 配信、IK、制御、タスク判定、viewer の表示 |
| `models/so101_pick_place.xml` | MJCF。SO-101 の簡易モデル、2 本指グリッパー、赤ブロック、緑の目標エリア |
| `index.html` | WebXR クライアント（自動接続・自動再接続、handedness で右手を判定、グリップ量で球の色が赤→緑） |
| `start_teleop.sh` | 一括起動スクリプト |
| `test_pick_place.py` | ヘッドレスの自動 Pick & Place テスト（`--view` を付けると viewer で動きを見られる） |
| `send_test.py` | `ws://localhost:8000/ws` にダミーの姿勢を約 3 秒送る |
| `requirements.txt` | mujoco, numpy, aiohttp, websockets |
| `teleop_mujoco.py`, `load_so101.py`, `test_mujoco.py`, `vr_server_test.py` | Phase 2 以前の旧ファイル（比較用に残している。使っていない） |
| `261028-quest3-wsl2-teleop-setup-guide.md` | Phase 2 までの構築ガイド（ユーザーが書いたもの） |

---

## 4. 設計上の判断（変えるときは理由を踏まえること）

1. **制御は `data.ctrl`（position アクチュエータ）。`qpos` を直接書き換えない。** Phase 2 は qpos を直接書いていたが、それだとアームが物体をすり抜けて接触が破綻する。アームの自重は `gravcomp="1"` で打ち消している。
2. **IK は 2 リンクの解析解（肘上）で、グリッパーは常に真下を向く。** `j1 = atan2(y, x)`。手首位置 = 把持中心 + `L_TOOL` だけ真上。`j4 = π − j2 − j3`。届く範囲は手首まで `L1 + L2 = 0.22m`。範囲外の目標は届く位置に丸めるので、高さ 10cm の目標では 5mm ほど届かない。
3. **座標マッピング**（WebXR：x = 右、y = 上、z = 手前 → ロボット：x = 前、y = 左、z = 上）：`target = origin + [-dz, -dx, dy] * SCALE`。Phase 2 の `+dz` は前後が逆だったので、10/29 に修正した。
4. **viewer のカメラはアームの真後ろのやや上（azimuth=0、elevation=-30）。** 操作者と同じ向きなので、「手を前に出す = 画面の奥」「手を右に出す = 画面の右」になる。斜め・真上寄り・正面からの視点はオフスクリーンで描画して比較済みで、どれも掴む姿勢か置く姿勢のどちらかで腕が対象を隠したため不採用。
5. **qpos の並びは body の木構造順。** 先頭が `red_block` の freejoint（7 要素）で、その後に joint1〜4、指 ×2 が続く。インデックスを決め打ちせず、`model.joint(name).qposadr` で取ること。
6. **衝突フィルタ**：world 系（床・ブロック・エリア）は `contype=1 conaffinity=3`、arm 系は `contype=2 conaffinity=1`。arm 同士は衝突しない。
7. **掴みを安定させる設定**：`cone="elliptic"`、`impratio="10"`、`integrator="implicitfast"`。指とブロックは `condim=4`、摩擦 1.5。ブロックは 3cm 角で 30g。
8. **緑のエリアは不透明（alpha = 1）。** MuJoCo は半透明の geom に影を落とさない。影の解像度は `shadowsize=4096`。
9. **viewer に重ねる文字（`viewer.set_texts`）は ASCII のみ。** MuJoCo のフォントは日本語を表示できない。ターミナルのログは日本語で構わない。
10. **配信は `index.html` だけ。** プロジェクトのルートをまるごと静的配信すると、トンネル越しに `.git` や `.py` が見えてしまう。
11. **配置成功の判定**（`TaskMonitor`）：一度持ち上げた（z > 0.04）＋ エリア内 ＋ z < 0.03 ＋ ほぼ静止 ＋ **指がブロックに触れていない**。最後の条件がないと、掴んだまま降ろしただけで成功になってしまう。

---

## 5. 調整用パラメータ（`teleop_server.py` の先頭にある）

| 定数 | 値 | 意味 |
|---|---|---|
| `SCALE` | 0.5 | 手の移動量 → アームの移動量 |
| `HOME_TARGET` | (0.15, 0, 0.15) | 接続時の手の位置に対応するアームの目標位置 |
| `Z_MIN` / `R_MIN` | 0.018 / 0.06 | 床・土台との干渉を避ける下限 |
| `VIEWER_CAMERA` | lookat (0.15, 0, 0.04)、dist 0.5、az 0、el -30 | viewer の初期視点 |
| `BLOCK_RANDOM_XY` / `BLOCK_RANDOM_YAW` | 0.03 / 0.5 | 再配置時のランダム幅（初期位置 (0.17, 0.10) を中心に） |
| `SUCCESS_HOLD_SEC` | 2.0 | 成功メッセージを出してからブロックを再配置するまでの時間 |
| `TaskMonitor.REACH_R` | 0.24 | ブロックがこれより遠くで止まったら自動で再配置する |
| 指の `kp` / `forcerange`（XML の `finger` クラス） | 150 / ±5 | 握る強さ。滑るときはここを上げる |

幾何の定数（`SHOULDER_Z`、`L1`、`L2`、`L_TOOL`、`FINGER_OPEN`）は XML と一致させること。

---

## 6. Phase 3 でやったこと（時系列）

1. git 化（`.gitignore`、`.gitattributes`）。
2. Task 1〜4：サーバーを 1 本に統合、`start_teleop.sh`、HTML の自動接続とグリッパー連動、Pick & Place 環境。qpos 直書きから ctrl 制御への変更と IK の作り直しもここで行った。
3. 実機フィードバック 1（10/29）：前後が逆 → 修正。キューブのリセット → B ボタンと自動再配置を追加。カメラを近づける → 後方からの視点に変更。
4. 実機フィードバック 2（10/29）：成功時に画面にメッセージ（`SUCCESS!  Block placed`）を出し、エリアを黄色に光らせて 2 秒後に再配置。成功回数は右下に表示。初期位置のランダム化。緑のエリアに影が出るよう修正。

### 確認状況
| 項目 | 状態 |
|---|---|
| Quest 3 からの接続、グリップでの開閉、操作性の改善 | ✅ ユーザーが実機で確認済み |
| 自動 Pick & Place（ランダム幅の四隅を含む）、成功 → 再配置の流れ | ✅ ヘッドレスのテストで確認済み |
| 手動操作での Pick & Place 成功 | ❓ 未報告（10/29 の時点では「まだ掴んで成功していない」） |
| viewer 上の成功メッセージ・成功回数の文字の見た目、緑のエリアの影 | ❓ 実際の viewer では未確認（オフスクリーン描画でのみ確認） |

---

## 7. 検証のやり方（Claude 向け）

```bash
source ~/pai_env/bin/activate && cd ~/pai_final_project
python test_pick_place.py                              # 回帰テスト（最後に「✅ Pick & Place テスト成功」）
timeout 10 python -u teleop_server.py --no-viewer --port 8011 &   # 8000 番と被らないポートでスモークテスト
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8011/   # 200
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8011/teleop_server.py  # 404 であること
```
- **オフスクリーン描画**（カメラ・影の確認）：`MUJOCO_GL=egl` で `mujoco.Renderer` を使い、PIL で PNG に保存する。
  - 終了時に `EGLError` が出るが、後片付けの段階で出ているだけで画像は正常に保存される。
  - `Renderer` を 1 プロセスで 2 個作ると画像が乱れる。1 個を使い回すこと。
- viewer なしで `run_simulation` を試したいときは、`is_running` / `sync` / `set_texts` だけを持つダミーの viewer を渡せばよい。
- node は nvm 経由（`bash -ic` か `source ~/.nvm/nvm.sh`）。PATH には Windows 側の `npx` / `claude`（`/mnt/c/...`）も入っているので注意する。

---

## 8. 次の候補

1. 手動操作での Pick & Place 成功の確認と、操作性の微調整（`SCALE`、指の握る力など）。
2. 構築ガイドに Phase 3 の章を追記する（ガイド §7 のロードマップの 1〜3 はこれで達成）。
3. **ロードマップ 4：LeRobot 用のデータレコーダー。** テレオペ中の関節角 q(t)、エンドエフェクタの位置、グリップ値、カメラ画像をエピソード単位で HDF5 か Zarr に保存し、ACT / Diffusion Policy の学習につなげる。`.gitignore` では `data/`、`recordings/`、`*.hdf5`、`*.zarr/` を除外済み。
4. 必要なら `CLAUDE.md` を作り、§1 のルールを常に読み込まれるようにする。

### 既知の制約
- SO-101 の簡易モデルで、手首のロール軸がない（グリッパーの向きは j1 の旋回だけで決まる）。リンク長も実機とは異なる。
- 手の姿勢（回転）は使っていない（位置とグリップ値だけ）。
- LocalTunnel はときどき切れる（スクリプトが自動で張り直す）。サブドメインが他の人に使われているときは別の URL になり、警告が出る。

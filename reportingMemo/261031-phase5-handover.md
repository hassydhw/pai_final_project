# Phase 5 引継書（学習・評価・提出準備）— 2026-10-31

締切：**2026-11-02（月）10:00**（Omnicampus に PDF と GitHub URL、YouTube に動画）。
それ以前の経緯は `261030-phase4-handover.md` を参照。

## 1. ここまでの結果

| 内容 | 結果 |
|---|---|
| データ | Quest 3 テレオペで 36 エピソード（18,941 フレーム、約 10.5 分）。配置範囲は中心 ±3 cm、向き ±30° |
| 学習 | ACT、30,000 ステップ（`outputs/train/act_v1`）。最終 loss 0.067。途中で Windows Update の再起動があり、5,000 ステップから再開した |
| 評価（10 回） | 10/10（100%）`outputs/eval/act_v1_030000` |
| 評価（50 回、seed 100） | **48/50（96%）、95% 信頼区間 87〜99%** `outputs/eval/act_v1_030000_n50` |
| パイロット（4 エピソード、4,000 ステップ） | 10 回で 0/10。同じ 50 個の配置での評価を実行中 |

中間報告（先生との相談用）は Claude Docs：https://claude.ai/artifact/6bvwQNyXgUuaCoNfTQy1kS

### 先生との相談結果
- Quest 3（WebXR）の自作テレオペ環境が高評価。
- 追加実験は **汎化の確認（案 E）** を行う。
- パイロットとの差に「記録方法の変更（成功後 1.5 秒の記録）」も含まれる点は、データ収集仕様が成功率を左右した知見としてレポートに明記する。
- レポートの比率の目安：テレオペ環境の自作・遅延対策 40%、学習・評価・汎化の結果と考察 60%。

## 2. 実行中のジョブ（systemd のユーザーサービス `pai-eval`）

`outputs/run_generalization.sh` が以下を順に実行する（07:42 開始、10:00〜10:15 ごろ終了の見込み）。
tmux で動かしていたら、VS Code を閉じたときに一緒に止まった（07:14）。そのため `loginctl enable-linger hal`（設定済み）+ `systemd-run --user` で、VS Code やターミナルを閉じても止まらない形にしている。

| 順 | 内容 | ログ | GIF・results.json |
|---|---|---|---|
| 1 | パイロット 50 回（seed 100、本番と同じ配置） | `outputs/eval_act_pilot_n50.log` | `outputs/eval/act_pilot_n50/` |
| 2〜4 | 汎化：中心から 4 / 5 / 6 cm（正方形の周上）を各 20 回（seed 200） | `outputs/eval_gen_offset{4,5,6}cm.log` | `outputs/eval/gen_offset{4,5,6}cm/` |
| 5 | 汎化：向き 30〜45°（位置は学習範囲内）を 20 回 | `outputs/eval_gen_yaw30-45.log` | `outputs/eval/gen_yaw30-45/` |

- 最後に「=== 評価 すべて終了」と表示される。
- PC の再起動・シャットダウンで止まる。Windows Update は一時停止しておく。
- 様子を見る：`systemctl --user status pai-eval`（動いていれば active (running)）、`journalctl --user -u pai-eval`（各条件の開始時刻と成功率）。
- 止まったら、同じコマンドで再開できる。`results.json` がある条件（終わった条件）は飛ばし、途中だった条件は最初からやり直す。
  ```bash
  cd ~/pai_final_project
  systemd-run --user --unit=pai-eval --working-directory=$HOME/pai_final_project /bin/bash outputs/run_generalization.sh
  ```
  （前回のユニットが failed で残っていて起動できないときは、先に `systemctl --user reset-failed pai-eval`）

## 3. 再開手順

1. VS Code で WSL の `~/pai_final_project` を開き、Claude Code パネル上部の Session history からこのセッションを再開する。CLI なら `claude --resume`。
2. ジョブの状況を確認する。
   ```bash
   systemctl --user status pai-eval   # active (running) なら実行中、inactive なら終了
   journalctl --user -u pai-eval      # 各条件の開始時刻と成功率
   tail -3 outputs/eval_*.log    # 各条件の「成功率: x/y」
   ```
3. Claude に「handover の続きから」と伝える。

## 4. これからやること（順番）

### 今日の夕方以降：データの確定
- [ ] 評価結果の集計。パイロットと本番の比較（同じ 50 個の配置）と、汎化の 4 条件。
- [ ] グラフ作成：「中心からのずれ（3 = 学習範囲の端、4 / 5 / 6 cm）× 成功率」。各 `results.json` に試行ごとのずれ・向き・成否がある。
- [ ] 素材の切り出し：成功例・失敗例（範囲外）の GIF と静止画。
- [ ] `eval_policy.py` の変更（`--offset-cm`、`--yaw-deg`、`results.json`）を確認してコミットし、`generalization-eval` を main にマージする。

### 11/1（日）前半：デモ動画（1〜3 分、YouTube）
- [ ] 構成：Quest 3 でのテレオペ → データ収集 → 学習 → 方策だけで成功（96%）→ 汎化テスト。
- [ ] Quest 3 の操作の様子の録画（ユーザー）。
- [ ] YouTube のタイトルは `PAI最終課題_<omnicampusアカウント名>`。公開（推奨）または限定公開。「子ども向け」は「いいえ」。

### 11/1（日）後半：レポート（PDF）
- [ ] ファイル名は `説明資料_PAI最終課題_<omnicampusアカウント名>.pdf`。**氏名・所属・メールなどの個人情報は書かない。**
- [ ] 構成：システム構成図、テレオペの工夫（VR 内のアーム表示・影・目の高さ合わせ・遅延対策）、データ収集の工夫（成功後 1.5 秒の記録・B ボタン破棄）、学習、評価（50 回・パイロット比較・汎化）、考察。

### 11/2（月）9:00 までに：提出
- [ ] GitHub リポジトリを **Public** にする。公開前に、個人情報・秘密情報・大きなファイル（`data/`、`outputs/` は ignore 済み）が入っていないか確認する。
- [ ] Omnicampus に PDF と GitHub URL を提出する。YouTube にアップロードする。
- [ ] 優秀生選出を希望するなら、通常提出のあとに表示されるフォームからも出す。

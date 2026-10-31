# Phase 5 引継書（学習・評価・提出）— 2026-10-31

**状況：提出済み（2026-10-31）。** 通常提出（Omnicampus に PDF と GitHub URL）と優秀生選出のフォームを提出した。締切は 2026-11-02（月）10:00 だった。
それ以前の経緯は `261030-phase4-handover.md` を参照。

## 1. 結果

| 内容 | 結果 |
|---|---|
| データ | Quest 3 テレオペで 36 エピソード（18,941 フレーム、約 10.5 分）。配置範囲は中心 ±3 cm、向き ±30° |
| 学習 | ACT、30,000 ステップ（`outputs/train/act_v1`）。最終 loss 0.067。途中で Windows Update の再起動があり、5,000 ステップから再開した |
| 評価（50 回、seed 100） | **48/50（96%）、95% 信頼区間 87〜99%**。失敗 2 回は持ち上げたあと置ききれず `outputs/eval/act_v1_030000_n50` |
| パイロット（4 エピソード、4,000 ステップ、同じ 50 配置） | **0/50（0%、0〜7%）**。持ち上げは 16 回、一度も離さない `outputs/eval/act_pilot_n50` |
| 汎化：中心から 4 cm（学習範囲の 1 cm 外） | 17/20（85%、64〜95%）。失敗 3 回はすべて奥側（届く限界の近く） |
| 汎化：中心から 5 cm（2 cm 外） | 3/20（15%、5〜36%）。失敗はほぼ空振り（持ち上げ 5/20） |
| 汎化：中心から 6 cm（3 cm 外） | 2/20（10%、3〜30%） |
| 汎化：向き 30〜45°（位置は範囲内） | 20/20（100%、84〜100%）。手首の回転軸がなく、向きで動作が変わらないため |

- 汎化は seed 200、配置は中心からの L∞ 距離 d の正方形の周上（土台から 0.235 m より遠い位置は引き直し）。各条件の `outputs/eval/gen_*/results.json` に試行ごとのずれ・向き・成否がある。
- 図は `make_figures.py` → `outputs/figures/`（成功率のグラフ、代表的な試行の GIF と 5 コマの静止画）。
- **注意：成功後の記録は 36 回中 33 回が 1.0 秒、最後の 3 回だけ 1.5 秒**（`POST_SUCCESS_SEC` を 1.5 にしたのは収集の途中、`46031a2`）。レポート・動画では「約 1 秒」と書いた。

### 提出物

| 提出物 | 場所 |
|---|---|
| レポート PDF | `説明資料_PAI最終課題_halhalhal.pdf`（10 ページ）。元は Claude Docs：https://claude.ai/artifact/EReHtVjPthL4aX2StDRWX6 |
| デモ動画 | https://youtu.be/sKHabK-YvUY （タイトル `PAI最終課題_halhalhal`、2 分 14 秒） |
| GitHub | https://github.com/hassydhw/pai_final_project （Public） |
| 中間報告（先生との相談用） | Claude Docs：https://claude.ai/artifact/6bvwQNyXgUuaCoNfTQy1kS |

- デモ動画の元は `pai-final-video/`（Remotion、git 管理外。使い方は同フォルダの README）。書き出しは `out/pai-final-demo.mp4`。
  - Quest 3 の録画（10/31 19:23、adb で取り出し、32〜56 秒を使用）と、同じ時刻の PC の MuJoCo 画面の録画を左右に並べている。
  - 方策の映像は、評価と同じ試行を `scripts/rollout_clip.py` で再実行し、1280×720 で描き直したもの（評価と成否・秒数が一致することを確認済み）。

### 先生との相談結果（10/31）
- Quest 3（WebXR）の自作テレオペ環境が高評価。
- 追加実験は **汎化の確認（案 E）** を行う。
- パイロットとの差に「記録方法の変更（成功後の記録）」も含まれる点は、データ収集仕様が成功率を左右した知見としてレポートに明記する（レポート 7.1 に記載済み）。
- レポートの比率の目安：テレオペ環境の自作・遅延対策 40%、学習・評価・汎化の結果と考察 60%。

## 2. 長時間のジョブの動かし方（参考）

評価は `outputs/run_generalization.sh`（git 管理外）で、systemd のユーザーサービスとして実行した（すべて終了済み）。
tmux で動かすと、VS Code を閉じたときに一緒に止まる（10/31 07:14 に発生）。`loginctl enable-linger hal`（設定済み）+ `systemd-run --user` なら、VS Code やターミナルを閉じても止まらない。

```bash
cd ~/pai_final_project
systemd-run --user --unit=pai-eval --working-directory=$HOME/pai_final_project /bin/bash outputs/run_generalization.sh
systemctl --user status pai-eval     # active (running) なら実行中
journalctl --user -u pai-eval        # 各条件の開始時刻と成功率
```
- `results.json` がある条件は飛ばすので、止まっても同じコマンドで再開できる。前回のユニットが failed で残っていたら、先に `systemctl --user reset-failed pai-eval`。
- PC の再起動・シャットダウンでは止まる。

## 3. 再開手順

1. VS Code で WSL の `~/pai_final_project` を開き、Claude Code パネル上部の Session history からこのセッションを再開する。CLI なら `claude --resume`。
2. Claude に「handover の続きから」と伝える。

## 4. やったこと

### 10/31：データの確定
- [x] 評価結果の集計（パイロットと本番の比較、汎化の 4 条件）。
- [x] グラフ作成（`make_figures.py`）。
- [x] 素材の切り出し（成功例・失敗例の GIF と静止画）。
- [x] `eval_policy.py` の変更（`--offset-cm`、`--yaw-deg`、`results.json`）をコミットし、`generalization-eval` を main にマージ・push。

### 10/31：デモ動画（YouTube）
- [x] 構成：システム構成 → Quest 3 でのテレオペ → データ収集 → 学習 → パイロットとの比較（0% → 96%）→ 汎化 → まとめ。
- [x] Quest 3 と PC の MuJoCo 画面の録画（ユーザー）を組み込み。
- [x] YouTube に公開（タイトル `PAI最終課題_halhalhal`）。

### 10/31：レポート（PDF）
- [x] `説明資料_PAI最終課題_halhalhal.pdf`。氏名・所属・メールが入っていないことを確認。
- [x] 「成功後 1.5 秒」の記述を実態（約 1 秒）に訂正。推測だった 7.2 の一文を観察した事実だけの表現に修正。

### 10/31：提出
- [x] GitHub リポジトリは Public。追跡ファイルに個人情報・秘密情報がないことを確認（`data/`、`outputs/`、`pai-final-video/` は ignore 済み）。
- [x] Omnicampus に PDF と GitHub URL を提出。
- [x] 優秀生選出のフォームも提出。

### 残っていること（任意）
- [ ] `outputs/` と `pai-final-video/` の大きなファイルの整理（当面このまま残す）。
- [ ] 記録仕様の切り分け実験（同じ 36 回から成功後の部分を削って学習し、成功率を比べる）。レポートの「今後」に記載。

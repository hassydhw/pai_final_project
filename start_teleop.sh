#!/usr/bin/env bash
# Quest 3 VR テレオペ環境 一括起動スクリプト
#
#   ./start_teleop.sh                 # 既定のサブドメインで起動
#   SUBDOMAIN=foo ./start_teleop.sh   # サブドメインを変える
#   ./start_teleop.sh --no-viewer     # 引数は teleop_server.py にそのまま渡す
#
# 起動するもの:
#   1. teleop_server.py（Web 配信 + WebSocket + MuJoCo、ポート 8000）
#   2. LocalTunnel（https://<SUBDOMAIN>.loca.lt → localhost:8000、切れたら自動再接続）
# Ctrl+C または MuJoCo ウィンドウを閉じると両方まとめて終了する。

set -u

PORT="${PORT:-8000}"
SUBDOMAIN="${SUBDOMAIN:-my-so101-teleop}"
VENV="${VENV:-$HOME/pai_env}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

# --- Python 仮想環境 ---
if [ ! -f "$VENV/bin/activate" ]; then
    echo "[エラー] 仮想環境が見つかりません: $VENV" >&2
    exit 1
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -c "import aiohttp, mujoco" 2>/dev/null || {
    echo "[セットアップ] 依存ライブラリをインストールします..."
    pip install -r "$SCRIPT_DIR/requirements.txt" || exit 1
}

# --- Node（nvm 経由。Windows 側の npx を誤って使わないようにする） ---
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
# shellcheck disable=SC1091
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"
if ! command -v npx >/dev/null 2>&1 || [[ "$(command -v npx)" == /mnt/* ]]; then
    echo "[エラー] WSL 側の npx が見つかりません（nvm で Node をインストールしてください）" >&2
    exit 1
fi

# --- 前回のプロセスが残っていればポートを解放 ---
if command -v fuser >/dev/null 2>&1 && fuser "$PORT/tcp" >/dev/null 2>&1; then
    echo "[準備] ポート $PORT を使用中のプロセスを終了します"
    fuser -k "$PORT/tcp" >/dev/null 2>&1
    sleep 1
fi

SERVER_PID=""
TUNNEL_LOOP_PID=""
cleanup() {
    trap - INT TERM EXIT
    echo -e "\n[終了] サーバーとトンネルを停止します..."
    [ -n "$TUNNEL_LOOP_PID" ] && kill "$TUNNEL_LOOP_PID" 2>/dev/null
    # npm exec と node(lt) の両方のコマンドラインに含まれる引数で一括停止
    pkill -f -- "--port $PORT --subdomain $SUBDOMAIN" 2>/dev/null
    [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
    wait 2>/dev/null
}
trap cleanup INT TERM EXIT

# --- 1. 統合サーバー ---
cd "$SCRIPT_DIR" || exit 1
python teleop_server.py --port "$PORT" "$@" &
SERVER_PID=$!

# 待ち受け開始を待つ
for _ in $(seq 1 30); do
    curl -s -o /dev/null "http://localhost:$PORT/" && break
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "[エラー] teleop_server.py の起動に失敗しました" >&2
        exit 1
    fi
    sleep 0.5
done

# --- 2. LocalTunnel（切断されたら自動で張り直す） ---
(
    while true; do
        npx --yes localtunnel --port "$PORT" --subdomain "$SUBDOMAIN" 2>&1 \
            | tee -a "$LOG_DIR/tunnel.log" \
            | while read -r line; do
                if [[ "$line" == *"your url is:"* ]]; then
                    url="${line#*your url is: }"
                    echo ""
                    echo "=========================================="
                    echo " Quest 3 のブラウザで開く URL:"
                    echo "   $url"
                    if [[ "$url" != *"//$SUBDOMAIN."* ]]; then
                        echo " ⚠ サブドメイン '$SUBDOMAIN' は使用中のため別の URL になりました"
                    fi
                    echo " トンネルパスワード（初回アクセス時に入力）:"
                    echo "   $(curl -s https://loca.lt/mytunnelpassword)"
                    echo "=========================================="
                else
                    echo "[tunnel] $line"
                fi
            done
        echo "[tunnel] トンネルが切断されました。3 秒後に再接続します..."
        sleep 3
    done
) &
TUNNEL_LOOP_PID=$!

# サーバー（= MuJoCo ウィンドウ）が終了するまで待つ
wait "$SERVER_PID"

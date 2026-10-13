#!/bin/bash
# マイアシスタントを起動して、ブラウザで開く。
# Finder でダブルクリックするか、ターミナルから ./起動.command で実行する。

cd "$(dirname "$0")" || exit 1

pause_and_exit() {
  echo
  read -r -p "Enter キーでこのウィンドウを閉じます" _
  exit "${1:-1}"
}

# 初回だけ: 仮想環境とライブラリを用意する
if [ ! -x .venv/bin/python ]; then
  echo "初回セットアップをしています(数分かかります)..."
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || {
    echo "セットアップに失敗しました。"
    pause_and_exit 1
  }
fi

# 設定ファイルがなければ見本から作る
if [ ! -f config.json ]; then
  cp config.example.json config.json
  echo "config.json を作りました。読ませたいフォルダを allowed_roots に書いてください。"
  pause_and_exit 0
fi

PORT=$(.venv/bin/python -c "import json; print(json.load(open('config.json')).get('port', 8000))")
URL="http://127.0.0.1:${PORT}"

open_browser() {
  # テスト用: ASSISTANT_NO_OPEN があればブラウザを開かない
  [ -z "$ASSISTANT_NO_OPEN" ] && open "$URL"
}

# すでに起動していれば、ブラウザを開くだけにする
if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "すでに起動しています: $URL"
  open_browser
  exit 0
fi

( sleep 3; open_browser ) &

echo "起動します: $URL"
echo "止めるには Ctrl+C を押すか、このウィンドウを閉じてください。"
echo
exec .venv/bin/python -m assistant

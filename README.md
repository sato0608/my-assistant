# マイアシスタント

Claude Agent SDK をラップした、記憶付きのローカル Web アシスタント。

- 自分のマシン上でだけ動く(`127.0.0.1` で待ち受け)
- 会話の記憶を `memory/` に Markdown で保存し、次の会話に引き継ぐ
- 読ませるフォルダは `allowed_roots` で許可したものだけ

## 前提

- Python 3.10 以上
- 認証: Claude Code にログイン済みであるか、環境変数 `ANTHROPIC_API_KEY` を設定してあること
- 動作確認は macOS(`起動.command` は macOS 用。他の OS では下の手動手順を使う)

## セットアップ

```bash
git clone https://github.com/sato0608/my-assistant.git
cd my-assistant
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
```

`config.json` の `allowed_roots` に、読ませたいフォルダの**絶対パス**を書く。

```json
{
  "memory_dir": "memory",
  "allowed_roots": ["/Users/あなた/Documents/research"],
  "host": "127.0.0.1",
  "port": 8000
}
```

## 起動

```bash
python -m assistant
```

ブラウザで http://127.0.0.1:8000 を開く。

macOS では `起動.command` をダブルクリックしても起動できる
(初回は仮想環境の作成と `config.json` の生成まで自動で行う)。

## テスト

```bash
python -m pytest
```

## 記憶

`memory/` に Markdown で保存される(Git 管理外)。エディタで直接編集してよい。
追記は会話後に画面で承認した分だけ書き込まれ、「新しい会話」から反映される。

## 設計資料

`docs/superpowers/` に設計書と実装計画がある。

## ライセンス

[MIT License](LICENSE)

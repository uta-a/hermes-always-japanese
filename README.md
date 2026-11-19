# always-japanese（Hermes Agent プラグイン）

Hermes Agent では、英語のツール出力が続くと、日本語で話しかけていても返答が英語に流れることがあります。このプラグインは、ターンの開始時とツール結果に、モデルだけが読む短いリマインダー（「返答は必ず日本語で」）を差し込み、直近の文脈に常に言語指定がある状態を保ちます。

Claude Code の mod [always-japanese](https://github.com/uta-a/always-japanese) を Hermes のプラグインとして実装し直したものです。

## 動作

- ターン開始時（`pre_llm_call`）: その回のユーザーメッセージの後ろにリマインダーを足します。Hermes の仕様で、この追記は API 呼び出し時だけのもので、会話履歴やセッション DB には残りません
- ツール結果（`transform_tool_result`）: 結果の末尾に `[always-japanese] 返答は必ず日本語で…` を 1 行足します。英語が長く出やすいエラー結果にも付けます。`pre_tool_call` などで拒否された呼び出しには付きません（Hermes が transform を呼ばないため）
  - 並列に呼んだツールは結果がまとめて次の要求に載るため、モデルの応答 1 回（`api_request_id`）につき 1 つだけ付けます
  - 20,000 文字を超える結果では先頭に付けます。Hermes は大きな結果を先頭のプレビューだけ残してファイルへ退避するため、末尾だと消えてしまうからです
  - 文字列でない結果（画像を含むマルチモーダル結果など）は変更しません
- サブエージェント（`delegate_task`）: ツール結果ごとには付けず、子エージェントのターン開始時に「最終報告は日本語で」を 1 回だけ足します。子の判定は `subagent_start` / `subagent_stop` で受け取る `child_session_id` で行います

## コスト

リマインダーは 1 文（日本語で約 50 文字）です。ターン開始時の分は履歴に残りませんが、ツール結果に付けた分は会話履歴に残ります。ツールを多く呼ぶ長いセッションでは積み上がり、圧縮（compaction）が早まることがあります。気になる場合は `on_tool_call` を `false` にしてください。

## 設定

`config.yaml` の `plugins.entries.always-japanese.settings` に書きます。デスクトップアプリの Settings → Plugins からも変更できます。

| キー | 既定値 | 内容 |
| --- | --- | --- |
| `language` | `日本語` | 返答させる言語。空白だけの値は既定値に戻します |
| `on_tool_call` | `true` | ツール結果にも差し込むか。`false` ならターン開始時とサブエージェントのみ |

```yaml
plugins:
  enabled: [always-japanese]
  entries:
    always-japanese:
      settings:
        language: 日本語
        on_tool_call: true
```

設定は起動時（プラグインの読み込み時）に読みます。変更後は Hermes を再起動してください。

## インストール

GitHub から入れる場合:

```sh
hermes plugins install <owner>/<repo>
hermes plugins enable always-japanese
```

手元のフォルダを直接使う場合は、`~/.hermes/plugins/always-japanese` に置き（シンボリックリンクでも可）、`config.yaml` の `plugins.enabled` に `always-japanese` を足します。

## 制約

- `transform_tool_result` は、最初に文字列を返したプラグインの結果だけが採用されます。このプラグインはほぼすべてのツール結果に文字列を返すため、読み込み順（ディレクトリ名のアルファベット順）で後ろにある別の transform 系プラグイン（秘匿情報のマスクなど）が無効になることがあります。併用する場合は読み込み順に注意してください
- MCP ツールの結果は、Hermes が信頼できない外部コンテンツとして囲むことがあり、その場合はリマインダーも囲みの内側に入ります

## 開発

テストは Hermes 本体の Python 環境で、実際の PluginManager の discovery を通して動かします（一時ディレクトリを `HERMES_HOME` にするので、手元の `~/.hermes` には触れません）。

```sh
hermes --run-module unittest discover -s "$PWD/tests"
hermes plugins validate "$PWD"
hermes plugins doctor --ci "$PWD"
```

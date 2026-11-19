# always-japanese（Hermes Agent プラグイン）

Hermes Agent では、英語のツール出力が続くと、日本語で話しかけていても返答が英語に流れることがあります。このプラグインは、ターンの開始時とツール結果に短いリマインダー（「返答は必ず日本語で」）を差し込み、直近の文脈に常に言語指定がある状態を保ちます。

Claude Code の mod [always-japanese](https://github.com/uta-a/always-japanese) を Hermes のプラグインとして実装し直したものです。

## 動作

- ターン開始時（`pre_llm_call`）: その回のユーザーメッセージの後ろにリマインダーを足します。Hermes の仕様で、この追記は API 呼び出し時だけのもので、会話履歴やセッション DB には残りません
- ツール結果（`transform_tool_result`）: 結果にリマインダーを足します。英語が長く出やすいエラー結果にも付けます。`pre_tool_call` などで拒否された呼び出しには付きません（Hermes が transform を呼ばないため）
  - 結果が JSON オブジェクトのときは、`"always_japanese": "返答は必ず日本語で…"` というキーを埋め込み、妥当な JSON のまま返します。Hermes は結果を JSON として解析して `exit_code` や `error` から失敗を判定するため、後ろにテキストを足すと失敗したコマンドが成功扱いになってしまうからです
  - それ以外の結果は末尾に `[always-japanese] 返答は必ず日本語で…` を 1 行足します。文字列結果の失敗判定は先頭を見るため、先頭には付けません
  - 20,000 文字を超える JSON オブジェクトでは先頭にキーを埋め込みます。Hermes は大きな結果（既定 100K 文字、MCP は 50K 文字）を先頭のプレビューだけ残してファイルへ退避するため、末尾だと消えてしまうからです。テキストの結果は退避されるとリマインダーも消えます
  - 並列に呼んだツールは結果がまとめて次の要求に載るため、モデルの応答 1 回（`api_request_id`）につき 1 つだけ付けます
  - 文字列でない結果（画像を含むマルチモーダル結果など）は変更しません
  - 足した内容は会話履歴に残り、UI のツール結果プレビューにも表示されます
- サブエージェント（`delegate_task`）: ツール結果ごとには付けず、子エージェントのターン開始時に「最終報告は日本語で」を 1 回だけ足します。子の判定は `subagent_start` / `subagent_stop` で受け取る `child_session_id` で行います

## コスト

リマインダーは 1 文（日本語で約 50 文字）です。

- ツール結果に付けた分は会話履歴に残ります。ツールを多く呼ぶ長いセッションでは積み上がり、圧縮（compaction）が早まることがあります。気になる場合は `on_tool_call` を `false` にしてください
- ターン開始時の分は、Hermes の仕様で API 呼び出しのときだけ現在ターンのユーザーメッセージに付き、履歴には残りません。そのため次のターンでは、前のターンのユーザーメッセージが送信時と変わり、そこから後ろ（前のターンのツールのやり取りを含む）はプロンプトキャッシュが効かず書き直しになります。これは `pre_llm_call` で文脈を足すプラグインに共通の性質です

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

手元のフォルダを使う場合は、`~/.hermes/plugins/always-japanese` に置き（シンボリックリンクでも可）、`config.yaml` の `plugins.enabled` に `always-japanese` を足します。

```sh
ln -s "$PWD" ~/.hermes/plugins/always-japanese
hermes plugins enable always-japanese
```

GitHub から入れる場合:

```sh
hermes plugins install uta-a/hermes-always-japanese
hermes plugins enable always-japanese
```

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

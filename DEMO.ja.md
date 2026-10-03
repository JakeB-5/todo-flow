# TODO Flow の動作を見る

[English](DEMO.md) · [한국어](DEMO.ko.md) · [日本語](DEMO.ja.md) · [简体中文](DEMO.zh-CN.md)

[README](README.ja.md) · [エージェントのインストール](AGENT_INSTALL.ja.md) · [運用](OPERATIONS.ja.md)

<!-- translation-source: DEMO.md; source-sha256: 6723cd1053d11cecc427d635e36a6ac26979a8af5fd82e97fb64c7662db4a5ef; status: translated -->

## このリポジトリ自身の設定

[セルフホスティングの手順](examples/self-hosting/README.md)には、2026年9月26日にこのリポジトリで行った実際の設定を記録しています。別途インストールした `0.0.4` エンジン、韓国語のプロジェクト言語、Codex ワーカー、review エンドポイントを使用しました。実際の空のダッシュボードと、他の環境でも使える検証手順を含みます。その時点では、このプロジェクトでトラックの登録や実行は行われていませんでした。今後の実際のタスク結果は、この設定記録とともに残してください。

## ダッシュボードツアー

![ダッシュボードツアー](assets/demo/dashboard-tour.gif)

これは、**合成された読み取り専用データ**を使った実際のアプリケーションのキャプチャです。モデル実行の証拠ではありません。fixture には、現在の作業、判断待ち、条件付き Watch を含む48件のアクティブなトラックと2,500件の完了したトラックがあります。

[英語の画面](assets/demo/dashboard-en.png) · [韓国語の画面](assets/demo/dashboard-ko.png) · [アクティビティ](assets/demo/activity-en.png) · [リッチな計画の例](assets/demo/track-example.png)

ソースのチェックアウトから、**リポジトリ外の新しいディレクトリ**を指定して実行します。

```sh
uv sync --frozen
uv run python scripts/dashboard_fixture.py --state /absolute/new-dashboard-demo --language en
uv run todo-flow --state /absolute/new-dashboard-demo serve --port 8766
```

`http://127.0.0.1:8766` を開きます。

1. 連続表示されるアクティブ一覧をスクロールし、検索して実行可能なトラックを2件選択します。
2. `trackrun` コマンドをコピーします。コピーだけでは作業は始まりません。
3. アクティビティを開き、担当者、現在のタスク、判断待ちを確認します。
4. トラックを開き、文書、条件、証拠を読みます。
5. 独立した完了アーカイブを検索し、必要に応じて以前の記録を読み込みます。
6. English / 한국어 / 日本語 / 简体中文を切り替えます。選択と判断の下書きは保持され、作成済みコンテンツは元の言語のままです。

fixture はダッシュボードからの変更要求を拒否します。実際の作業は、別途初期化したプロジェクトで実行してください。

実行中のブラウザで画面をキャプチャした後、ツアーを組み立てるには次のコマンドを使います。

```sh
uv run --no-project --with Pillow==11.3.0 python scripts/render_demo.py \
  --output assets/demo/dashboard-tour.gif \
  assets/demo/dashboard-en.png assets/demo/selection-en.png \
  assets/demo/activity-en.png assets/demo/track-en.png assets/demo/completed-en.png
```

このスクリプトは渡されたキャプチャを結合するだけで、実行状態を作り上げることはありません。

## ワークフロー全体を実行する

初期コミット、origin リモート、認証、正常に動くテストを備えた使い捨てのプロジェクトを使用してください。[設定手順](AGENT_INSTALL.ja.md)に従って `en`、`ko`、`ja`、`zh-CN` のいずれかを選びます。実際の統合と triage を含める場合は `--endpoint land --allow-land` を指定します。これらのフラグがない場合、レビュー済み候補の段階で終了します。

例えば、範囲を限定した2つの要件をエージェントに渡します。

```text
todo 一時的なネットワーク障害に回数を制限した再試行を追加してください。恒久的な障害の扱いは維持し、テストを追加してください。
todo 恒久的なリクエスト失敗を、有用な次の対処方法とともに説明してください。メッセージのテストを追加してください。
trackpicks
```

[再試行計画の例](examples/retry-backoff.html)と[エラーメッセージ計画の例](examples/request-error-message.html)は、レビュー可能な HTML 文書の例です。登録前に、実際の fixture に合わせて範囲と証拠を調整してください。これらは要件の例であり、完了した作業ではありません。

生成された文書を確認し、実際に返された ID を指定して実行を依頼します。

```sh
trackrun retry-backoff request-error-message
```

ダッシュボードで分離された worktree とタスクを観察します。各候補の実際の検証と独立レビューを確認してください。統合が承認されている場合は、統合 SHA、統合後の triage、イシューのクローズ、完了状態を照合します。判断待ちになったら回答し、必要に応じてドライバーを再起動してください。後続の TODO は未選択のままです。

## 過去の公開受け入れテストを確認する

2026年9月24日の使い捨ての公開テストで、次の成果物が生成されました。

| 要件 | イシュー | マージされた変更 |
|---|---|---|
| テキストのスラッグ化 | [Issue #1](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/issues/1) | [PR #3](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/pull/3) |
| シーケンスの分割 | [Issue #2](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/issues/2) | [PR #4](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/pull/4) |
| 数値の範囲制限 | [Issue #5](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/issues/5) | [PR #6](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/pull/6) |

最初の2つのトラックは、triage の重複検索のバグを修正した後に復旧が必要でした。新しい3つ目のトラックは追加の介入なしで完了しました。これらは当時の実行ワークフローを示すものであり、その後のすべての UI・多言語対応の変更や大規模プロジェクトでの動作を検証するものではありません。

## リモート受け入れテストを再現する

最新の開発実行では、2026年9月24日に表示可能な Orca ターミナルでパスベースのワーカーを動かしました。

| 要件 | イシュー | マージされた変更 |
|---|---|---|
| 空白の圧縮 | [Issue #1](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/1) | [PR #4](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/4) |
| 異なる値の最初の出現を保持 | [Issue #2](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/2) | [PR #5](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/5) |
| 明示的な代替動作を持つ除算 | [Issue #3](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/3) | [PR #6](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/6) |

3つの実装ワーカーが同時に動く時間帯がありました。ベースの更新に伴う新たな triage を含め、実際の Codex ワーカー14個が Orca ターミナルで実行されました。選択した3つのトラックはすべて完了し、新しい利用文書の TODO と既存のライセンスの TODO は未選択のまま残りました。この実行には判断待ちやランタイムエラーがなく、提供された fixture は19件のテストを通過しました。fixture には150 KBを超えるソースファイルも含まれていました。これは範囲を限定した受け入れテストであり、大規模プロジェクトのベンチマークや実際の Claude の検証ではありません。

完了した実行で生成された11個の worktree と14個のワーカーターミナルはその後削除され、既存の証拠ファイル466個とすべてのローカルブランチの先端コミットが保持されました。続いて、別の[クリーンアップのライフサイクルタスク（PR #8）](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/8)が実際の Codex ワーカーで完了しました。統合と triage の後に、生成された3個の worktree と4個のワーカーターミナルが自動的に削除され、メインのチェックアウトと保持された証拠だけが残りました。[Issue #7](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/7)はワークフローによってクローズされました。

次のコマンドは、**公開リポジトリと実際のイシュー、PR、モデル呼び出し、マージを作成します**。その外部実験を意図する場合にのみ、自分のアカウントと新しいテストディレクトリで実行してください。

```sh
uv run python scripts/parallel_smoke.py \
  --worker codex --launcher orca --register-orca --exercise-triage \
  --create-public YOUR_ACCOUNT/NEW_TEST_REPOSITORY \
  --root /absolute/new-test-directory
```

この方法では、実行中のローカル Orca アプリにも使い捨ての fixture を登録するため、実際のターミナルが必要です。ヘッドレスで実行する場合は `--launcher headless` を使い、`--register-orca` を省略します。スクリプトはレビュー可能な HTML 計画を登録します。結果のレポートには、実際のトラック、リモートの成果物、ワーカー実行の時間的な重なり、ターミナルの記録、入力サイズが記録されます。最初の失敗、復旧、新たな実行の結果は区別して保持してください。実験の境界については [CONTRIBUTING](CONTRIBUTING.md#demos-and-external-acceptance) を参照してください。

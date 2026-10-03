# TODO Flow の動作を見る

[English](DEMO.md) · [한국어](DEMO.ko.md) · [日本語](DEMO.ja.md) · [简体中文](DEMO.zh-CN.md)

[README](README.ja.md) · [エージェントのインストール](AGENT_INSTALL.ja.md) · [運用](OPERATIONS.ja.md) · [更新](UPDATES.ja.md)

<!-- translation-source: DEMO.md; source-sha256: 181597c84a147f8b0a8c41d739588656e153da14097006b5d541e294a88fe283; status: translated -->

## このリポジトリ自身の設定

[self-hosting ガイド](examples/self-hosting/README.md)には、2026年9月26日に行ったこのリポジトリの実際の設定を記録しています。別途インストールした `0.0.4` エンジン、韓国語のプロジェクト言語、Codex ワーカー、review エンドポイントを使用しました。実際の空のダッシュボードと、他の環境でも使える検証手順が含まれています。この時点では、このプロジェクトに登録・実行されたトラックはありませんでした。今後の実作業の結果は、この設定記録とともに残します。

## ダッシュボードツアー

![ダッシュボードツアー](assets/demo/dashboard-tour.gif)

これらは**合成された読み取り専用データ**を使った実際のアプリケーションの画面であり、モデル実行の証拠ではありません。fixture には、現在の作業、判断待ち、条件付き Watch を含む48件のアクティブなトラックと2,500件の完了済みトラックがあります。

[英語の画面](assets/demo/dashboard-en.png) · [韓国語の画面](assets/demo/dashboard-ko.png) · [アクティビティ](assets/demo/activity-en.png) · [豊かな計画文書の例](assets/demo/track-example.png)

ソースのチェックアウトから、**リポジトリ外の新しいディレクトリ**を使います。

```sh
uv sync --frozen
uv run python scripts/dashboard_fixture.py --state /absolute/new-dashboard-demo --language en
uv run todo-flow --state /absolute/new-dashboard-demo serve --port 8766
```

`http://127.0.0.1:8766` を開きます。

1. 連続したアクティブ一覧をスクロールし、検索して実行可能なトラックを2件選びます。
2. `trackrun` コマンドをコピーします。コピーだけでは作業は始まりません。
3. アクティビティを開き、担当者、現在のタスク、判断待ちを確認します。
4. トラックを開き、文書、条件、証拠を読みます。
5. 独立した完了済みアーカイブを検索し、必要に応じて古い記録を読み込みます。
6. English / 한국어 / 日本語 / 简体中文 を切り替えます。選択と判断の下書きは保持され、作成済みの内容は元の言語のままです。

fixture はダッシュボードの変更リクエストを拒否します。実作業は別途初期化したプロジェクトで実行してください。

実行中のブラウザーで画面を撮影した後、ツアーを組み立てるには次を実行します。

```sh
uv run --no-project --with Pillow==11.3.0 python scripts/render_demo.py \
  --output assets/demo/dashboard-tour.gif \
  assets/demo/dashboard-en.png assets/demo/selection-en.png \
  assets/demo/activity-en.png assets/demo/track-en.png assets/demo/completed-en.png
```

スクリプトは渡された画面だけを組み合わせ、実行状態を作り出すことはありません。

## ワークフロー全体を実行する

初期コミット、origin リモート、認証、動作するテストがある使い捨てプロジェクトを使用します。[セットアップ](AGENT_INSTALL.ja.md)に従って `en`、`ko`、`ja`、`zh-CN` を選びます。実際の統合と triage まで試す場合は `--endpoint land --allow-land` を使用してください。指定しなければ、レビュー済みの候補で終了します。

例えば、エージェントに範囲を限定した2つの要件を伝えます。

```text
todo 一時的なネットワーク障害に回数制限付きの再試行を追加してください。恒久的な障害の処理は保持し、テストを追加してください。
todo 恒久的なリクエスト失敗を、有用な次の操作とともに説明してください。メッセージのテストを追加してください。
trackpicks
```

[再試行の計画例](examples/retry-backoff.html)と[エラーメッセージの計画例](examples/request-error-message.html)は、レビュー可能な HTML 文書の例です。登録前に実際の fixture に合わせて範囲と証拠を調整してください。これらは要件の例であり、完了済みの作業ではありません。

生成された文書を確認したら、実際に返された ID で実行を依頼します。

```sh
trackrun retry-backoff request-error-message
```

ダッシュボードで分離された worktree とタスクを観察します。各候補の実際の検証と独立レビューを確認してください。統合が承認されている場合は、統合 SHA、統合後の triage、Issue のクローズ、完了状態を照合します。判断待ちになったら回答し、必要に応じてドライバーを再起動します。後続 TODO は未選択のままです。

## 過去の公開受け入れテストを確認する

2026年9月24日の使い捨て公開テストでは、次の成果物が作成されました。

| 要件 | Issue | マージされた変更 |
|---|---|---|
| テキストのスラッグ化 | [Issue #1](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/issues/1) | [PR #3](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/pull/3) |
| シーケンスの分割 | [Issue #2](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/issues/2) | [PR #4](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/pull/4) |
| 数値の範囲制限 | [Issue #5](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/issues/5) | [PR #6](https://github.com/JakeB-5/todo-flow-terminal-20260924-r5/pull/6) |

最初の2件は、triage の重複検索の不具合を修正した後に復旧が必要でした。新たに実行した3件目は追加の介入なしで完了しました。これらの成果物は当時の実行ワークフローを示すもので、その後のすべての UI・多言語対応変更や大規模プロジェクトでの動作を検証するものではありません。

## リモート受け入れテストを再現する

直近の開発実行では、2026年9月24日に可視の Orca ターミナルでパスベースのワーカーを使用しました。

| 要件 | Issue | マージされた変更 |
|---|---|---|
| 空白の圧縮 | [Issue #1](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/1) | [PR #4](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/4) |
| 異なる値の最初の出現を保持 | [Issue #2](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/2) | [PR #5](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/5) |
| 明示的な代替動作を持つ除算 | [Issue #3](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/3) | [PR #6](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/6) |

3つの実装ワーカーが同時に動作した区間がありました。ベースブランチが進んだ際の新しい triage を含め、実際の Codex ワーカー14件が Orca ターミナルで実行されました。選択した3トラックはすべて完了し、新しい利用文書 TODO と既存のライセンス TODO は未選択のままでした。この実行には判断待ちやランタイムエラーがなく、提供された fixture は19件のテストに合格しました。fixture には150 KBを超えるソースファイルが含まれていました。これは範囲を限定した受け入れテストであり、大規模プロジェクトのベンチマークや実際の Claude の検証ではありません。

完了した実行で作成された11個の worktree と14個のワーカーターミナルは、その後クリーンアップされました。既存の466個の証拠ファイルとすべてのローカルブランチの先端は保持されています。続いて、別の[クリーンアップのライフサイクルタスク（PR #8）](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/pull/8)が実際の Codex ワーカーで完了しました。統合と triage 後、そのタスクで作成された3個の worktree と4個のワーカーターミナルは自動的に削除され、メインのチェックアウトと保持された証拠だけが残りました。ワークフローは [Issue #7](https://github.com/JakeB-5/todo-flow-terminal-e2e-20260924/issues/7) をクローズしました。

次のコマンドは**公開リポジトリと実際の Issue、PR、モデル呼び出し、マージを作成します**。この外部実験を意図する場合に限り、自分のアカウントと新しいテストディレクトリで実行してください。

```sh
uv run python scripts/parallel_smoke.py \
  --worker codex --launcher orca --register-orca --exercise-triage \
  --create-public YOUR_ACCOUNT/NEW_TEST_REPOSITORY \
  --root /absolute/new-test-directory
```

この方式は、実行中のローカル Orca アプリにも使い捨て fixture を登録し、実際のターミナルを必要とします。ヘッドレス実行では `--launcher headless` を使い、`--register-orca` を省略してください。スクリプトはレビュー可能な HTML 計画を登録します。生成されたレポートには、実際のトラック、リモート成果物、ワーカー実行時間の重なり、ターミナルのレシート、入力サイズが記録されます。初回の失敗、復旧、新しい実行の結果は分けて保持してください。実験の範囲は [CONTRIBUTING](CONTRIBUTING.md#demos-and-external-acceptance) を参照してください。

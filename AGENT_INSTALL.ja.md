# TODO Flow — エージェントのインストールと初回実行

[English](AGENT_INSTALL.md) · [한국어](AGENT_INSTALL.ko.md) · [日本語](AGENT_INSTALL.ja.md) · [简体中文](AGENT_INSTALL.zh-CN.md)

[README](README.ja.md) · [運用](OPERATIONS.ja.md) · [更新](UPDATES.ja.md) · [デモ](DEMO.ja.md)

<!-- translation-source: AGENT_INSTALL.md; source-sha256: 91a1c6b7bd92fffce08b72274b66cfd8aa79147fa24d24cf049ce5c283855ed8; status: translated -->

ユーザーが依頼した範囲に従ってください。既存のインストール、設定、許可を再利用します。この文書自体は、作業の実行や変更のランディングを許可するものではありません。許可済みで元に戻せる準備を完了し、不足している判断やユーザー自身が行う必要のある認証だけを尋ねてください。

## 1. 対象と言語を確認する

TODO Flow のチェックアウトと、ユーザーの対象プロジェクトを区別してください。対象の `AGENTS.md`、開発手順、検証コマンド、Git リモート、ベースブランチ、既存スキル、状態設定を読みます。

**主言語はユーザーが選びます。英語（`en`）、韓国語（`ko`）、日本語（`ja`）、簡体字中国語（`zh-CN`）に対応しています。** 既存のプロジェクト言語、または今回の依頼で明示された希望を再利用してください。どちらもなければ、使用する言語をユーザーに尋ねます。回答を待つ間も独立した環境調査は続けられますが、この README の言語から黙って決めないでください。スキルの指示は英語のままです。報告、質問、新しく書く文書は、明示的な指定がない限り選択した言語を使います。

```sh
FLOW_SOURCE='/absolute/todo-flow'
FLOW_PROJECT='/absolute/my-project'
FLOW_STATE="$FLOW_PROJECT/todo"
FLOW_SKILLS="$FLOW_PROJECT/.agents/skills"

git -C "$FLOW_PROJECT" rev-parse --show-toplevel
git -C "$FLOW_PROJECT" status --short
git -C "$FLOW_PROJECT" rev-parse --verify HEAD
```

Claude セッションでは `.claude/skills` を使ってください。別々のシェル呼び出しの間では変数が保持されないことがあるため、再宣言するか絶対パスを使います。`todo/` が別のツールのものなら、上書きせず別の状態ディレクトリを選んでください。既存の変更、Git 履歴、スキルのインストールを保持します。

## 2. ツールと認証を準備する

Python 3.11 以上、uv、Git、および選択した認証済みの Claude/Codex CLI を確認してください。GitHub 連携には、認証済みの `gh` と実際の対象リモートへのアクセスも必要です。ユーザーの依頼に根拠がなければモデルを変更しないでください。認証情報を文書、設定、出力に含めないでください。

リリースを新規インストールする場合、チェックアウトは不要です。

```sh
uv tool install https://github.com/JakeB-5/todo-flow/releases/download/v0.0.9/todo_flow-0.0.9-py3-none-any.whl
export PATH="$(uv tool dir --bin):$PATH"
todo-flow --version
trackrun --version
```

公式リポジトリは [https://github.com/JakeB-5/todo-flow](https://github.com/JakeB-5/todo-flow) です。リリース成果物とチェックサムは [GitHub Releases](https://github.com/JakeB-5/todo-flow/releases) にあります。ソース開発を依頼された場合は、TODO Flow のチェックアウトからインストールしてください。

```sh
cd "$FLOW_SOURCE"
uv sync --frozen
uv tool install .
export PATH="$(uv tool dir --bin):$PATH"
todo-flow --help
trackrun --help
```

互換性のある既存のインストールを再利用してください。再インストールの前に PATH を調べます。共有エンジンを置き換える前に、ほかの実行中プロジェクトを考慮してください。文書に記載された GitHub リリースの wheel を使い、別のインデックスにある同名パッケージがこのプロジェクトだと仮定しないでください。

`0.0.2` では、必要に応じたファイル読み取りと、ターミナルを優先するワーカーが追加されました。既存の `0.0.1` インストールでこれらを使うには、エンジンとプロジェクトスキルの更新が必要です。[更新ガイド](UPDATES.ja.md)に従ってください。新しいランチャーの既定値は `auto` です。Orca、設定済みターミナルまたは既存の tmux を使い、その後に headless を使います。ワーカーが自動で動くという理由だけで headless を強制しないでください。[ワーカーの実行](OPERATIONS.ja.md#worker-context-and-terminal-launchers)を参照してください。

`0.0.3` では、統合修正を現在のベースに接続し、ランディング前に新たな検証と独立レビューを要求するようになりました。この修正を受け取るには共有エンジンを更新してください。[修正の動作](OPERATIONS.ja.md#review-landing-and-completion)を参照してください。

`0.0.9` には、提案コミットの分離、正確な候補チェックアウトの確認、宣言された検証入力の同一性確認、永続的なプロセスクリーンアップ、個数制限のない実行ごとのターミナルクリーンアップ、ネイティブ Orca/Codex セッションのサポートが含まれます。ワーカーには既定の制限時間がありません。ネイティブのサイドバー状態、実行中のキャンセル、所有権を検証したワークツリーのクリーンアップは、それぞれ所有権の境界を維持します。チェックアウト内の既存の手動変更は保持され、復旧の判断が必要になることがあります。自動でリセットしたりステージングしたりしないでください。

## 3. プロジェクトを設定する

初期化済みなら既存の状態設定を読み、ワーカー、言語、検証、範囲、エンドポイントを再利用してください。実行中に `init` を再実行したり、実行設定を編集したりしないでください。

新規プロジェクトでは、実際のプロジェクトから値を取得します。

| 設定 | 取得元 |
|---|---|
| `--repo`, `--state` | 対象の Git ルートと、別に設けたプロジェクトの正本状態 |
| `--language en`, `--language ko`, `--language ja`, `--language zh-CN` | ユーザーが選択した主言語 |
| `--base`, 任意の `--github` | 実際のリモート/ベースと GitHub の所有者/リポジトリ |
| `--worker` | ユーザーが選択した、または利用可能な認証済み Claude/Codex CLI |
| `--verify` | 動作する既存の検証コマンドを JSON argv で表したもの |
| `--context`, `--write` | `0.0.2` の探索ヒント（`0.0.1` ではスナップショット選択）と許可された書き込みパターン |
| エンドポイント | 既定値は `review`。ランディングが許可済みなら `--endpoint land --allow-land` を使用 |

設定する前に検証コマンドを実行してください。既存の失敗を隠さず報告します。秘密情報を含むファイルを除外してください。実行には初回コミット、Git の作成者情報、`origin` が必要です。それを含む依頼がない限り、不足するリモートの作成、無関係な作業のコミット、履歴のリセットを行わないでください。

Python プロジェクトの例です。値と言語を置き換えてください。

```sh
todo-flow --state "$FLOW_STATE" init \
  --repo "$FLOW_PROJECT" --base main --worker codex --language en \
  --verify '["python3","-m","unittest","discover","-v"]' \
  --write 'src/*.py' --write 'tests/*.py' \
  --context 'src/*.py' --context 'tests/*.py' --context README.md
```

対応する GitHub アダプターを使う場合に限り `--github OWNER/REPOSITORY` を追加してください。Forgejo や複数リポジトリをまとめた成果の引き渡しは未実装です。プロジェクトの既存ポリシーまたはローカルの Git 除外設定で、ランタイム記録の誤コミットを防いでください。すでにバージョン管理されているトラック台帳の追跡を解除しないでください。

## 4. スキルをインストールしてダッシュボードを開く

```sh
todo-flow --state "$FLOW_STATE" install-skills --target "$FLOW_SKILLS"
todo-flow --state "$FLOW_STATE" serve --port 8765
```

インストーラーは既存のディレクトリを保持し、各インストール済みスキルの `project.json` に言語と状態を記録します。初期化済みプロジェクトの言語を継承します。単独インストールでは `--language en|ko|ja|zh-CN` を指定できます。初期化済みプロジェクトと矛盾する明示的な値は拒否されます。一つの競合を解決するためにスキルディレクトリ全体を置き換えないでください。既存のインストールには `update-skills --target PATH --dry-run` を使って競合を確認し、依頼された更新範囲でのみ適用します。マニフェストの引き継ぎとロールバックは [UPDATES.ja.md](UPDATES.ja.md) を参照してください。

<a id="coexist-with-occupied-skill-names"></a>

### 使用済みのスキル名と共存する

競合がなければ、既存の九つの名前をすべて維持します。`todo`、`track-picks`、`trackrun`、`track-run`、`watchlist`、`track-work`、`track-review`、`track-land`、`track-triage` です。エイリアスを使えるのは、正規名が使用済みで、この TODO Flow インストールがまだ所有していない場合だけです。`todo-flow install-skills --help` に `--alias` があることを確認してください。古いリリースでは、許可されたインストール/更新手順を通じて、この機能を含むソースビルドが必要になる場合があります。

まず対象を調べてください。次の例は、別のワークフローが `todo`、`track-picks`、`track-run`、`watchlist` の四つだけを使用し、提案する四つのエイリアスがすべて未使用であると仮定しています。実際に競合する名前のエイリアスだけを指定してください。Codex では `.agents/skills`、Claude では `.claude/skills` を使います。

```sh
FLOW_SKILLS="$FLOW_PROJECT/.claude/skills"
# Choose this separate STATE before initialization if another tool owns todo/.
FLOW_STATE="$FLOW_PROJECT/todo-flow-state"
todo-flow --state "$FLOW_STATE" install-skills --target "$FLOW_SKILLS" \
  --alias todo=flow-todo \
  --alias track-picks=flow-track-picks \
  --alias track-run=flow-track-run \
  --alias watchlist=flow-watchlist
```

初期化済みの TODO Flow プロジェクトでは、既存の STATE を維持してください。新規プロジェクトでは、インストール前に、選択した別の STATE で手順 3 を実行します。別のツールの状態に重ねて初期化しないでください。インストーラーは、そのツールの `project.json` を TODO Flow のコンテキストとして取り込みません。

エイリアスが使用済みなら、スキルファイルを変更する前にインストール全体が失敗します。エラーには役割とエントリーポイントが示されます。未使用のエイリアス、またはエージェントが対応する別のインストール先を選んでください。既存のファイルとシンボリックリンクを保持します。別のワークフローを取り込むために `--adopt` を使わないでください。引き継ぎは、一致する旧形式の TODO Flow バンドルに限られます。競合のない役割の名前を変更しないでください。

返された `entrypoints` マッピング（正規の役割 → インストール名）、`state`、`language` を確認し、各インストール名の `SKILL.md` と隣接する `project.json` を読んでください。この例では、エージェントに `flow-todo`、`flow-track-picks`、`flow-watchlist` の使用を依頼します。元の名前は引き続き別のワークフローに属します。`flow-track-run` は、変更されていない `trackrun` スキルにリンクします。インストールされた役割表は、九つすべての TODO Flow の役割を実際のエントリーポイントに結び付けます。Claude では、対象プロジェクトで開いたセッションで名前を確認してください。検出結果が更新されていなければ、新しいセッションを開くか、`.claude/skills/flow-todo/SKILL.md` を明示的に読んでください。

エイリアスはシェルコマンドを変更しません。引き続き `todo-flow --state "$FLOW_STATE" ...` と `trackrun --state "$FLOW_STATE" ACTUAL_TRACK_ID` を使います。インストールマニフェストを保持してください。更新時は `--alias` を繰り返さなくても保存済みのマッピングを再利用します。

```sh
todo-flow --state "$FLOW_STATE" update-skills --target "$FLOW_SKILLS" --dry-run
# After reviewing the plan and resolving any conflicts:
todo-flow --state "$FLOW_STATE" update-skills --target "$FLOW_SKILLS"
# Only when an interrupted update is reported:
todo-flow --state "$FLOW_STATE" update-skills --target "$FLOW_SKILLS" --recover
# To undo a completed update, use its actual returned backup ID:
todo-flow --state "$FLOW_STATE" update-skills --target "$FLOW_SKILLS" --rollback BACKUP_ID
```

上のコマンドは、まず計画を確認して競合を解決してから適用します。`--recover` は更新の中断が報告された場合だけ使ってください。完了した更新を元に戻す場合は、実際に返されたバックアップ ID を使います。

同梱ファイルが変わっていなければ、ローカル編集は保持されます。ローカルと同梱ファイルの両方が変わった場合、調整のため更新全体が中止されます。ロールバックは後から加えられた編集を破棄せず、処理を拒否します。復旧とロールバックは、所有するファイル、マッピング、コンテキストをバックアップから復元し、別のワークフローは管理しません。未知のマニフェスト形式は拒否されます。この検査を回避するためにマニフェストを削除しないでください。

`tests/test_skill_coexistence_bundle.py` の七つの回帰テストは、`.agents/skills` と `.claude/skills` の両方で、実際の九つのスキルバンドルの使い捨てコピーを使います。既定名の維持、四つの同時競合、使用済みエイリアスの拒否、既存ファイルとシンボリックリンク、インストールされた frontmatter、役割リンク、テンプレートのバイト列、STATE、CLI の例の保持を確認します。エイリアス更新のテストでは、ローカル編集、ベースラインとマッピングの保持、競合時の更新全体の拒否、中断からの復旧、ロールバック、未知のマニフェスト形式を確認します。`uv run python -m unittest discover -s tests -p 'test_skill_coexistence_bundle.py' -v` で実行し、評価対象の候補に対する実際の結果を報告してください。これらの合格は、ファイルシステム構成とメタデータの証拠です。Claude の起動、セッションでのスキル検出の確認、モデルの呼び出しは行いません。実際の Claude の検出とモデル呼び出しは別途報告する必要があります。この fixture はどちらも実行せず、実行したことを意味しません。

ダッシュボードは継続して動くターミナル/プロセスで実行してください。ポートが使用中なら無関係なサーバーを終了せず、空いているポートを使います。実際の URL、プロジェクト、既定言語を確認してください。表示言語の切り替えはブラウザ内でプロジェクトごとに保存されます。ワーカーの言語を変えたり、過去の文書を翻訳したりはしません。

現在のエージェントセッションが新しいスキルを検出しない場合は、インストールされた `SKILL.md` を直接読み、検出に新しいセッションが必要かどうかを説明してください。

## 5. 最初の実際の要件を登録する

セットアップのみの依頼なら、ダッシュボードの URL と `todo [requirement]` の依頼例を提供してください。作業中のプロジェクトに任意のサンプルトラックを作成しないでください。初回実行を依頼されたものの要件がない場合は、希望する変更を尋ねます。

インストールされた todo スキルを読んでください。実際の要件を調査し、既存のトラックと Watch を検索して、インストール済みテンプレートからレビュー可能な HTML 文書を正本状態の外に作成します。表示される文書と構造化された説明には選択した言語を使い、`language` と HTML の `lang` を `en`、`ko`、`ja`、`zh-CN` のいずれかに設定してください。ID とスキーマキーは維持します。

```sh
todo-flow --state "$FLOW_STATE" register /absolute/scratch/first-track.html
```

必要なら `--assets` を含めてください。`http://127.0.0.1:PORT/documents/ID/REVISION/index.html` には実際に返された ID とリビジョンを使います。登録された文書のブラウザ表示と代表的な操作を確認し、リンクを提供してください。実施していない視覚的な検証を行ったと主張しないでください。

todo と watchlist スキルは、任意で Jev の支援を勧めています。利用可能なツールで依頼された作業を開始し、完了してください。Jev がなくてもセットアップの妨げにはなりません。依頼されていない前提条件としてインストールしたり認証情報を求めたりしないでください。

## 6. 選択、実行、引き継ぎ

track-picks で現在の状態、依存関係、実行可能な範囲を確認します。ユーザーがこの最初の要件の選択と実行をすでに依頼している場合は、その範囲で進めてください。登録や推薦だけでは実行は許可されません。

trackrun を読み、実際に登録された ID を実行します。

```sh
trackrun --state "$FLOW_STATE" ACTUAL_TRACK_ID
```

`--jobs` は任意です。`--request-only` を使う場合は、別のドライバーが動いていることを確認してください。要求の保存だけでは初回実行の完了にはなりません。

`state.json`、`tasks`、`attempt-records`、`results`、`decisions`、`effects` を確認します。GitHub では実際の Issue/PR の状態を処理記録と照合してください。`review` エンドポイントはレビュー済みの候補を作ります。`land` エンドポイントには、ランディングされた SHA、現在有効なトリアージ完了記録、完了したトラック、Issue がある場合はそのクローズが必要です。

中断時は証拠を確認し、同じ状態に対して再開してください。再初期化しないでください。実際のユーザー回答を `answer` で記録し、必要ならドライバーを実行します。技術的なエラーを架空のユーザー判断にしないでください。失敗を保持し、介入を伴う復旧と問題なく終わった実行を区別します。新しい後続 TODO は選択を待ちます。

選択した言語で、**インストールと状態のパス、言語、ダッシュボード/文書のリンク、実際の結果と Issue/PR のリンク、残っている判断と次のコマンド**を引き継いでください。動かしたままのターミナル/プロセスを明示します。

## 既存のインストールを更新する

[UPDATES.ja.md](UPDATES.ja.md) を読んでください。まずインストール済みのバージョンとプロジェクトの互換性を確認します。現在の主言語、状態の紐付け、ユーザーの編集、これまでの実行許可を保持してください。更新だけでは新しいトラックやランディングは許可されません。

uv tool によるインストールでは、信頼するリリースを明示的に選び、保護機構のある `upgrade --wheel` の経路を使います。置き換え前に、ユーザー/ドライバーが進行中の作業を完了または一時停止し、ダッシュボードを停止するようにしてください。パッケージマネージャーで直接置き換えてメンテナンスの競合を回避しないでください。表示された復旧コマンドと返されたバックアップ ID を保持します。プロジェクトにインストールされたスキルは、まず dry run で確認してから更新してください。競合には調整が必要です。ディレクトリの削除や強制置換で解決しないでください。新しいエンジン、スキル、既存文書を検証してから、ユーザーの依頼に含まれるプロセスだけを再起動します。

ネットワーク経由の自動バージョン検出は現在提供していません。ソースチェックアウトでは、作業を実行していないときに既存の更新/インストール手順を使い、その後に同じ互換性とスキルの確認を行います。更新の中断後に CLI がなくなった場合は、置き換えた環境の外に保存されたベース Python の復旧ランナーを使ってください。

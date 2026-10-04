"""Japanese and Simplified Chinese translations of fixed launch labels.

English labels are the source keys; tuple order is Japanese, Simplified Chinese.
Unknown runtime codes and authored content are never looked up here.
"""

MESSAGES = {
    "Interactive Claude session selected": (
        "Claude の対話型セッションを選択",
        "已选择 Claude 交互式会话",
    ),
    "Claude executable not found": (
        "Claude 実行ファイルが見つかりません",
        "找不到 Claude 可执行文件",
    ),
    "Installed Claude lacks the required interactive isolation flags": (
        "インストール済みの Claude に必要な対話型隔離オプションがありません",
        "已安装的 Claude 缺少所需的交互式隔离选项",
    ),
    "Orca Claude terminal session": (
        "Orca Claude ターミナルセッション",
        "Orca Claude 终端会话",
    ),
    "Interactive Claude; completed session records supply the proposal.": (
        "対話型 Claude。完了したセッション記録から提案を取得します。",
        "交互式 Claude；从已完成的会话记录中获取提案。",
    ),
    "Orca terminal (command worker)": (
        "Orca ターミナル（コマンドワーカー）",
        "Orca 终端（命令工作进程）",
    ),
    "Headless process": ("ヘッドレスプロセス", "无界面进程"),
    "tmux terminal": ("tmux ターミナル", "tmux 终端"),
    "Configured terminal": ("設定されたターミナル", "已配置的终端"),
    "Supported native route selected": ("対応するネイティブ経路を選択", "已选择受支持的原生路径"),
    "Existing checkout has no managed ownership receipt": (
        "既存のチェックアウトに管理所有権の記録がありません",
        "现有检出目录没有托管所有权记录",
    ),
    "Codex executable not found": ("Codex 実行ファイルが見つかりません", "找不到 Codex 可执行文件"),
    "Codex native protocol version unverified": (
        "Codex ネイティブプロトコルのバージョンは未検証です",
        "Codex 原生协议版本未经验证",
    ),
    "Native workspace contract check failed": (
        "ネイティブ作業スペースの契約確認に失敗しました",
        "原生工作区契约检查失败",
    ),
    "Authentication storage unsupported by native adapter": (
        "ネイティブアダプターが対応していない認証保存方式です",
        "原生适配器不支持此认证存储方式",
    ),
    "Native implementation session provenance unavailable": (
        "ネイティブ実装セッションの出所記録がありません",
        "原生实现会话缺少来源记录",
    ),
    "Headless explicitly requested": ("ヘッドレス実行を明示的に要求", "已明确请求无界面执行"),
    "Launcher explicitly requested": ("起動方式を明示的に要求", "已明确请求启动方式"),
    "Orca CLI not found": ("Orca CLI が見つかりません", "找不到 Orca CLI"),
    "Orca terminal unavailable": ("Orca ターミナルを利用できません", "Orca 终端不可用"),
    "Orca host is not local": ("Orca ホストがローカルではありません", "Orca 主机不在本地"),
    "Orca host could not be verified": ("Orca ホストを確認できません", "无法验证 Orca 主机"),
    "Orca capability discovery failed": ("Orca の機能検出に失敗しました", "Orca 功能探测失败"),
    "Installed Orca does not support capability discovery": (
        "インストール済みの Orca は機能検出に対応していません",
        "已安装的 Orca 不支持功能探测",
    ),
    "Orca command schema not recognized": (
        "Orca コマンドのスキーマを認識できません",
        "无法识别 Orca 命令模式",
    ),
    "Native session contract remains unverified": (
        "ネイティブセッションの契約は未検証です",
        "原生会话契约仍未经验证",
    ),
    "Worker failed; recovery required": (
        "ワーカーが失敗しました。復旧が必要です",
        "工作进程失败，需要恢复",
    ),
    "Reading the same session history; input is not resent": (
        "同じセッション履歴を確認中です。入力は再送しません",
        "正在读取同一会话的历史，不会重新发送输入",
    ),
    "Server launch recorded; start pending": (
        "サーバー起動要求を記録しました。開始待ちです",
        "已记录服务器启动请求，等待启动",
    ),
    "Server process started": ("サーバープロセスが開始しました", "服务器进程已启动"),
    "Read-only session created": ("読み取り専用セッションを作成しました", "已创建只读会话"),
    "Work turn accepted": ("作業要求を受け付けました", "已接受工作请求"),
    "Visible session client requested": (
        "表示用セッションクライアントを要求しました",
        "已请求可视会话客户端",
    ),
    "Complete proposal received": ("完全な提案を受信しました", "已收到完整提案"),
    "Server stopped; group confirmation pending": (
        "サーバーが停止しました。プロセスグループの確認待ちです",
        "服务器已停止，等待确认进程组状态",
    ),
    "Cleanup requires reconciliation": ("クリーンアップ状態の再確認が必要です", "需要核对清理状态"),
    "Proposal saved; supervisor confirmation pending": (
        "提案を保存しました。監督プロセスの確認待ちです",
        "提案已保存，等待监督进程确认",
    ),
    "Proposal and process cleanup confirmed": (
        "提案とプロセスのクリーンアップを確認しました",
        "已确认提案和进程清理",
    ),
    "Selected; process start not established": (
        "選択済みです。プロセス開始の証拠ではありません",
        "已选择，但尚无进程启动的证据",
    ),
    "Selection failed before launch": ("起動前の選択に失敗しました", "启动前选择失败"),
    "Launch requested; outcome pending": (
        "起動を要求しました。結果は未確認です",
        "已请求启动，结果待确认",
    ),
    "Terminal request accepted; worker start not established": (
        "ターミナル要求は受理されました。ワーカー開始の証拠ではありません",
        "终端请求已接受，但尚无工作进程启动的证据",
    ),
    "Launch outcome unconfirmed; do not duplicate": (
        "起動結果は未確認です。重複起動しないでください",
        "启动结果未确认，请勿重复启动",
    ),
    "Not recorded": ("記録なし", "未记录"),
    "Orca Codex sidebar session": ("Orca Codex サイドバーセッション", "Orca Codex 侧栏会话"),
    "Orca Codex terminal client": ("Orca Codex ターミナルクライアント", "Orca Codex 终端客户端"),
    "No backend selected": ("バックエンド未選択", "未选择后端"),
    "Host-owned App Server; sidebar lifecycle is projected by the host.": (
        "ホストが App Server を所有し、サイドバーのライフサイクルを表示に反映します。",
        "App Server 由宿主管理，侧栏生命周期由宿主投影显示。",
    ),
    "Compatibility worker route selected.": (
        "互換ワーカー経路を選択しました。",
        "已选择兼容工作进程路径。",
    ),
    "Native session selection not recorded.": (
        "ネイティブセッションの選択記録がありません。",
        "未记录原生会话选择。",
    ),
    "Real-model and external live tests for this integration have not been performed.": (
        "この統合の実モデル試験と外部の実環境試験は未実施です。",
        "尚未对此集成执行真实模型测试或外部实环境测试。",
    ),
    "Selection and terminal acceptance do not prove worker start or completion.": (
        "選択とターミナル要求の受理は、ワーカーの開始や完了を証明しません。",
        "选择和终端请求获接受并不能证明工作进程已启动或完成。",
    ),
    "Sidebar session observed": ("サイドバーのセッション表示を確認済み", "已观察到侧栏会话"),
    "Sidebar session not confirmed": ("サイドバーのセッション表示は未確認", "侧栏会话尚未确认"),
}

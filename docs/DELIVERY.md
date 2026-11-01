# ローカル提出パッケージと再現手順

提出期限は2026-11-02 10:00 Asia/Tokyo、アカウントは未確定の`<omnicampus_account>`。この資料はローカルで準備しており、実際のGit公開・YouTubeアップロード・Omni提出はまだ行っていない。PDF、動画、正式画像の欄は実生成・確認後に実ファイル名とSHA256を記録する。未生成のファイルを完成品として含めない。

## 現行の提出資料（2026-11-01）

課題10/19修訂版に合わせ、レポートは教員向けの概要・目的・実装上の工夫・結果・考察として整理した。正本はtracked `docs/REPORT_JA.md` / `REPORT_JA.html`。現在のPDFは6頁・738811bytes、SHA256 `025d5d2179d9b7cac61917e3805b074c74f16894cca35a7dcf8fce29a487f91c`で、同じHTMLから生成した。個人の提出操作・アカウントやURLの待機事項は本文に含めず、ローカル`upload/`の私用ガイドに分ける。

現在の推奨動画は日本語解説版、正式第2回A緑→B赤→base、2倍速178.1秒。MP4 SHA256は `845085b99108f429b1e63a8301ab8fc72582e3afb17d4e52e6c7f97ae1f12edf`。実中国語入力は原文と日訳を区別し、末尾2秒は別の正式試験の実画像要約として明示する。新規実験・API呼出しはない。

コードはZIPではなく、ユーザーが指定するGitHubリポジトリへGitで公開する。既存ローカル履歴の個人メタデータはそのままpushせず、原feature履歴とignoredデータを保持して、安全確認した新しいroot snapshotを`release/public-submission`へ用意する計画である。実際のremote設定とpushはリポジトリ名の指定後に行う。受講者側の主再現手順は現在のtracked README・Dockerfile・scripts・設定・精簡証拠で完結し、内部commitや既存ZIPは必要ない。

旧`submission/`は過去の成果・失敗を残すローカル履歴として変更しない。以下はその履歴資料の範囲と作成手順であり、現在の源码ZIP提出を推奨するものではない。

## 2026-10-11時点の履歴資料

## 含めるもの

| 区分 | 実際の対象 | 選択理由 |
| --- | --- | --- |
| 再現可能なコード | `core/`、`src/`、`tests/core/`、`scripts/`、`simulation/` | モデルが返した原計画を使う固定実行器、仮想環境、単体・ROS検証 |
| ビルド・依存固定 | `Dockerfile`、`compose.yaml`、`compose.vertex.yaml`、`pyproject.toml`、`requirements.lock`、`dependencies.repos` | Ubuntu/Jazzy/Harmonic、公式SDK、ROBOTISソースrevisionと既定設定 |
| 説明・出典 | `README.md`、`LICENSE`、`docs/THIRD_PARTY_NOTICES.md`、`src/pai_sim_safety/UPSTREAM.md` | Qiitaの参考構成、独自変更、上流ライセンス・著作権を保持 |
| 検証・調整説明 | `docs/STATUS.md`、`docs/AI_EVALUATION.md`、`docs/ADC_SETUP.md`、`docs/REPORT_JA.md`、`docs/REPORT_JA.html`、本書 | 成功・失敗の分母、技術詳細、人が改良する位置、費用の推定と未知予約を区別 |
| 固定問題とコンパクト証拠 | `docs/semantic_cases.json`、`semantic_acceptance_cases.json`、`visual_formal_cases.json`、`docs/evidence/` | 元応答・検証済計画・実使用量・UUID・実到着・失敗履歴・raw SHA。大きい反復path配列は要約する |
| 実画像 | 正式評価で保存した必要な実PNGと、そのframe/config/hash情報 | 期待色から生成した画像や合成fixtureを実測証拠にしない。校正画像は正式採点から分離 |
| 日本語PDF・動画 | 実生成・検品した報告PDFと、一つの真正な正式タスクを記録した動画 | 動画左は実カメラ、右は実TFのログ可視化。倍率・元wall時間・欠測範囲を明記 |
| パッケージ索引 | 最終実装commit、検証image ID、実際に含めた各ファイルのSHA256とbyte数 | ソース・モデル・画像・実行の同一性を辿れるようにする |

本プロジェクトの独自コードはApache-2.0。Qiita記事コードのライセンスを推定せず、参考構成のみとして記載する。SafeDiffDriveはHarmonicの上流ヘッダとrevisionを保持する。依存はbuild時に取得するためソース包に巨大な第三者checkoutは必要ないが、実際に再配布する上流checkoutやコンテナからLICENSEを削除しない。

## 除外するもの

`logs/`全体、約281MBの反復path生JSON、`.local/`、`.env`、ADCおよび認証コード・OAuth URL・token・ユーザー識別情報、`state/`とSQLite台帳、`.venv/`、`.git/`、build/install/cache、生成中の媒体、Docker image tarを一括提出しない。公開可能なraw要約だけを`docs/evidence/`に選び、元ログSHAとローカル相対再現パスを残す。台帳を包から除外しても、稼働中のローカル台帳を削除・初期化して累積上限を回避してはならない。

履歴の失敗を削って成功だけの包にしない。元のP2二失敗、P1テスト時刻失敗、semantic hard失敗、contact probe首失敗、P4 fixture首回帰失敗はコンパクト証拠に残す。期待動作のP3、校正stub、保存応答の二段階P1、真正なオンラインP2/P4は別集計にする。

## 最小再現

Docker Engine / Compose v2が必要。現存し利用権限のあるCloudプロジェクトと外部ADCを利用し、新規クラウド資源やIAM/課金変更はしない。公開包に実プロジェクトIDやADCを入れない。

```bash
./scripts/build.sh
# 自分の既存projectと外部ADCを設定。認証済みならOAuthを繰り返さない。
export GOOGLE_CLOUD_PROJECT='existing-project-id'
export PAI_ADC_PATH="${XDG_CONFIG_HOME:-$HOME/.config}/pai_ai_control/gcloud/application_default_credentials.json"
export GOOGLE_CLOUD_LOCATION=global
export PAI_VERTEX_MODEL=gemini-3.1-pro-preview
./scripts/start.sh vertex
./scripts/diagnose.sh 15
./scripts/ros.sh pai-budget show
# 静止・clock・sensor診断が通った後。sendは真正なAPIを1回使用する。
./scripts/ros.sh ros2 run pai_inspection_ros instruction_cli resume
./scripts/ros.sh ros2 run pai_inspection_ros instruction_cli send '先去 B，再去 A，最后回起点'
./scripts/ros.sh ros2 run pai_inspection_ros instruction_cli stop
./scripts/stop.sh
```

初期状態はロックされている。未知の目標/取消終態による故障ロックでは通常resumeできず、仮想環境・Nav2・guard全体を再起動する。最大8step、地点座標は人が設定する。新しい台帳はUS$1/100で始まり、追加許可がある場合のみ`pai-budget set --max-usd 5 --max-attempts 300`で明示変更する。本検証の既存台帳はユーザー承認済み5/300を保持する。80%警告や未知予約を無視しない。

実画像評価を再現する場合は、`visual_formal_cases.json`の場景と原文を使い、先に独立校正を終えた同一classifier設定SHAを固定する。正式色ラベルは採点側だけに渡し、planner/classifierに入力しない。録画・ContactsはAction前に開始し、最初と最後の実観測がタスクを覆ったかを別途確認する。追加実行は新API利用となり、累積台帳に記録される。

単体検証は`.venv/bin/python -m pytest tests/core -q`、ROS/画像検証は新build後の`./scripts/ros.sh python3 -m unittest discover -s /workspace/src/pai_inspection_ros/test -v`。真正なモデル固定問題評価は`AI_EVALUATION.md`のsentinel→残り手順を使い、失敗・未実行項を保存する。高性能モデルによる固定24問合格も、任意入力や長期走行の保証ではない。

## 現在の媒体状態

日本語Markdown/HTMLは正式三場景3/3タスク・5/5実画像の検証結果を反映した原本。最終PDF6頁/動画127.8秒・3倍速は全停止後に生成・検品済み。日文字体のみの独立PDF smokeは成功したが、それを巡回結果PDFと呼ばない。正式媒体の生成後、実際のファイルパス、SHA、動画時間・倍率・capture被覆、PDF頁数・日文抽出の確認結果をこの節へ追記する。アカウントが確定しても、自動公開・自動提出はしない。

## 媒体の停止後オフライン再生成

以下は記録済みformal第1回の原フレームを使う。仮想環境が全停止してから実行する。Action前の直近実フレームから終態後の最初の実フレームまでを保存し、全Actionを覆う。3倍速で約127秒の候補となるが、実符号化後のffprobe値を最終値とする。新しいカメラ画像・API・Nav2目標は生成しない。既存出力の上書きは拒否する。

```bash
python3 scripts/prepare_action_video_clip.py p4-formal1-red-green
./scripts/render_video.sh logs/p4-formal1-red-green-action-video 3
./scripts/render_pdf.sh docs/REPORT_JA.html logs/REPORT_JA.pdf
```

JPEGの元hash、原capture/raw hash、実際の開始・終端と各wall間隔を保持する。地図側は実TFの記録を描画したもので、Gazebo俯瞰レンダリングではない。正式第2回の色交換captureも原記録としてローカルに保持するが、全JPEGを提出包に入れる必要はない。

## 持久化した正式observerによる再現

新buildの静止診断、locked状態、clock一致、画像・Contacts・動画采集readyを先に確認する。場景red_greenを選ぶ。observer自身がresumeして1Actionを送るため、先にCLI resumeしない。以下は新しい真正なAPIを1回使用する。期待色は採点側だけに渡る。実際のDocker image SHA、新規run IDを置き、既存出力は上書きしない。

```bash
./scripts/ros.sh python3 /workspace/src/pai_inspection_ros/test/live_online_acceptance.py \
  --instruction '先去设备 B 检查指示灯，再去设备 A 检查指示灯，最后回起点' \
  --expect indicator_inspection --vision-phase formal \
  --expected-inspections station_b:green,station_a:red \
  --vision-config-sha256 d03fa3824871b01fa28a2b66beedbc51e2fc5de4079ad9fa4ad840778db7a005 \
  --run-id '<new_unique_run_id>' --output '/opt/pai/logs/<new_unique_run_id>.json' \
  --image-sha256 '<actual_docker_image_sha>' --model gemini-3.1-pro-preview
```

検証済みcae82 imageは49回帰を含む。実正式実行は同imageにexternal observer/clock helperを用い、時刻用3target回帰は別に確認した。現在は実行したexternal3ソースをsrc/testへSHA一致で持久化しており、次buildで含まれるが、既存imageへ含まれたとは記載しない。

最終PDF/MP4とソース・証拠包はGit除外の`submission/`へ置く。`submission/MANIFEST.json`に実byte/SHA、検証結果、実装commitと包範囲を記録し、`docs/evidence/delivery-media.json`には個人情報を含まない媒体検証を保持する。生281MBログ、外部ADC、.local、state、.git、全JPEGは含めない。

## 検品済み媒体（2026-10-11）

| ファイル | 実測仕様 | SHA256 |
| --- | --- | --- |
| `submission/説明資料_PAI最終課題_<omnicampus_account>.pdf` | 732647bytes、6頁A4、Noto日本語字体Unicode埋込、全文抽出・全頁/変更末頁目視 | 9e29f358d83f17919341edd567b2471c0c2b0646d26560876a404ff1f3fb4f54 |
| `submission/task-video.mp4` | 748875bytes、127.8秒、3倍速、H2641060×560、yuv420p、10fps、首中末デコード目視 | 7348f4b5f5b009ce7045a3a7e180f2332dcad38b38eaeb8a4ebbf1310274d4f8 |

formal1全Action381.144wall秒を覆う実サンプル画像を使い、フレーム間の元時間を保持する。末実フレームはbaseでTask完了後、独立cleanup lock前であり、表示guard unlockedを塗り替えない。元auditが連続停止とその後のlockを証明する。`docs/evidence/delivery-media.json`にはsource HTML、元capture/clip/raw、符号化スクリプトのhashと三場景の采集被覆を保持する。

`submission/pai_ai_control_submission.zip`は選択ソース/compact証拠/必要PNG、日本語PDF、実動画、MANIFESTを含むローカル包。`submission/SHA256SUMS`をプロジェクト根で`sha256sum -c submission/SHA256SUMS`して確認する。実装commit承認後にMANIFESTの帰属SHAとZIPを確定する。

YouTube用タイトル案（未公開・未アップロード）：**自然言語でTurtleBot3を巡回：Vertex Gemini・Nav2・実RGB指示灯確認（Gazebo、3倍速）**。提出アカウント`<omnicampus_account>`は未確定であり、本作業は自動提出を行わない。

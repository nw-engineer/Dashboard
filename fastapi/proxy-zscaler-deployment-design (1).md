# Observability AI PoC Proxy + Zscaler 対応デプロイ設計

更新日: 2026-09-04
対象ベース: Phase 5 完了版 v11.4 / 実装コード v11.3

## 1. 目的

既存の New Relic + Splunk + A2UI / AG-UI PoC を、HTTP/HTTPS Proxy と Zscaler CA が必須の別環境へ、1 から再セットアップできる配布物として再構成する。

今回の成果物は、次を満たす。

- Proxy URL を `.env` から設定できる。
- Zscaler CA は利用者が `certs/zscaler-ca.crt` に配置する。
- 外向き通信を行うアプリ系コンテナへ Proxy / CA を共通適用する。
- Compose 内部通信および RFC1918 プライベートアドレスは Proxy を経由させない。
- New Relic MCP Server、Console API、React UI を同一 Compose プロジェクトで管理する。
- Splunk の初期設定、Read Only RBAC、HEC、New Relic APM、MCP、LLM、Phase 4/5 E2E まで、再構築とテスト手順を文書化する。
- 既存の Evidence-first / Read Only / provider 非自動 failover の境界は変更しない。

## 2. 明示的なスコープ外

以下は今回の成果物では変更しない。

- Docker daemon 自体の Proxy 設定
- `docker pull` が利用するホスト側 Proxy / CA 設定
- Zscaler CA 証明書そのものの配布
- Proxy サーバーの構築または認証方式の変更
- Splunk / New Relic / Azure OpenAI の契約・アカウント作成
- 本番向け HA、Secrets Manager、Vault、Kubernetes 化

Docker daemon / image pull が Proxy を必要とする環境では、ホスト側で事前に利用可能な状態であることを前提とする。

## 3. 採用アプローチ

### 3.1 Base Compose + Proxy override

既存の `docker-compose.yml` は通常環境向けベースとして維持し、Proxy/Zscaler 環境では `compose.proxy.yml` を重ねる。

実行例:

```bash
docker compose \
  -f docker-compose.yml \
  -f compose.proxy.yml \
  --profile splunk \
  up -d --build
```

利用者が毎回長いコマンドを入力しなくて済むよう、`Makefile` に Proxy 環境向けターゲットを追加する。

```bash
make proxy-up
make proxy-ps
make proxy-test
make proxy-down
```

この方式を採用する理由:

- Phase 1〜5 で検証済みのベース構成との差分を限定できる。
- 通常環境と Proxy/Zscaler 環境を同一コードベースで維持できる。
- Proxy 固有設定のレビューが容易になる。
- Proxy 環境だけで必要な New Relic MCP / Console API / Console UI の起動条件を一箇所へ集約できる。

## 4. 目標ディレクトリ構成

```text
observability-ai-poc/
├── docker-compose.yml
├── compose.proxy.yml
├── .env.example
├── .dockerignore
├── .gitignore
├── Makefile
├── certs/
│   └── README.md
├── docker/
│   ├── python-service-proxy.Dockerfile
│   ├── console-api.Dockerfile
│   ├── console-ui.Dockerfile
│   ├── console-ui-nginx.conf
│   └── newrelic-mcp.Dockerfile
├── vendor/newrelic-mcp-server/
│   └── ... validated newrelic-mcp-server 0.1.0 source ...
├── scripts/
│   ├── preflight_proxy.sh
│   ├── verify_proxy_ca.sh
│   ├── verify_stack.sh
│   └── bootstrap_splunk_readonly.sh
├── docs/
│   ├── SETUP_PROXY_ZSCALER.md
│   └── TESTING_PROXY_ZSCALER.md
└── existing Phase 1-5 sources...
```

`vendor/newrelic-mcp-server/` には、過去に `newrelic-mcp-server-newrelic-mcp-1` として実機確認した `newrelic-mcp-server` 0.1.0 の固定版ソースを同梱する。MCP の `/mcp` Streamable HTTP 契約と既存 Tool 契約は変更しない。

## 5. `.env` 設計

### 5.1 Canonical Proxy 設定

利用者が編集する Proxy 値は大文字の 3 変数を正とする。

```env
HTTP_PROXY=http://proxy.example.local:8080
HTTPS_PROXY=http://proxy.example.local:8080
NO_PROXY=localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,host.docker.internal,splunk,postgres,scenario-controller,payment-mock,payment-service,inventory-service,order-service,api-gateway,load-generator,newrelic-mcp,console-api,console-ui
NO_PROXY_EXTRA=
```

Compose 内では互換性のため、次も同値で注入する。

```text
http_proxy
https_proxy
no_proxy
```

`NO_PROXY_EXTRA` は環境固有の内部 FQDN、追加サブネット、Proxy を通したくない社内 API 用に使用する。

### 5.2 NO_PROXY の方針

必須範囲:

```text
localhost
127.0.0.1
::1
10.0.0.0/8
172.16.0.0/12
192.168.0.0/16
```

CIDR の `NO_PROXY` 対応はクライアント実装差があるため、Compose サービス名を必ず併記する。

内部通信例:

```text
console-api -> splunk:8089
console-api -> newrelic-mcp:8000
api-gateway -> order-service:8081
order-service -> inventory-service:8082
order-service -> payment-service:8083
payment-service -> payment-mock:8084
services -> splunk:8088
inventory-service -> postgres:5432
```

### 5.3 CA 関連環境変数

Python/httpx 系:

```env
SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
```

Node/npm 系:

```env
NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/zscaler-ca.crt
npm_config_cafile=/etc/ssl/certs/ca-certificates.crt
```

New Relic Python Agent:

```env
NEW_RELIC_PROXY_HOST=${HTTPS_PROXY}
NEW_RELIC_CA_BUNDLE_PATH=/etc/ssl/certs/ca-certificates.crt
```

`NEW_RELIC_PROXY_HOST` には scheme / host / port を含む Proxy URI を渡す。既存 New Relic Python Agent の app name、distributed tracing、logging 設定は維持する。

### 5.4 New Relic MCP 資格情報

MCP Server が必要とする User API Key は、PoC 本体で使う変数と衝突させず明示的に分離する。

```env
NEW_RELIC_MCP_USER_KEY=
NEW_RELIC_ACCOUNT_ID=
NEW_RELIC_REGION=US
NEW_RELIC_GRAPHQL_URL=
NEW_RELIC_ALLOWED_ACCOUNT_IDS=
```

Compose 内で:

```text
NEW_RELIC_USER_KEY <- NEW_RELIC_MCP_USER_KEY
```

とマッピングする。

`NEW_RELIC_ALLOWED_ACCOUNT_IDS` が空で `NEW_RELIC_ACCOUNT_ID` が設定済みの場合は、セットアップ手順で同一 Account ID の allow-list 設定を推奨する。

## 6. Zscaler CA の扱い

利用者が次へ PEM/CRT 形式で配置する。

```text
certs/zscaler-ca.crt
```

要件:

- 実 CA は ZIP に同梱しない。
- `.gitignore` で `certs/zscaler-ca.crt` を除外する。
- `certs/README.md` に配置方法と確認コマンドを書く。
- Proxy 環境向け build は CA が存在しない場合、preflight で起動前に失敗させる。
- コンテナイメージ内では `/usr/local/share/ca-certificates/zscaler-ca.crt` へコピーし `update-ca-certificates` を実行する。
- OS trust store と各ランタイム用 CA 環境変数を併用する。

CA を無視する `verify=false` を外部 New Relic / LLM 通信へ適用しない。

例外として、既存 PoC の Splunk Management REST は Compose 内の自己署名 TLS を使用しており、現行設計どおり `SPLUNK_SEARCH_VERIFY_SSL=false` を保持する。これは Zscaler の外向き TLS 検証とは別の境界である。

## 7. Docker build 設計

### 7.1 Python アプリ共通イメージ

`docker/python-service-proxy.Dockerfile` を用意し、Proxy/Zscaler 環境の Demo Commerce / load-generator が共通利用する Project image を作る。通常環境向けの既存 `services/*/Dockerfile` と `load_generator/Dockerfile` は変更せず、Proxy override でのみこの Dockerfile へ差し替える。Console API は `docker/console-api.Dockerfile` を使用する。

責務:

1. `python:3.11-slim` を使用。
2. Zscaler CA を OS trust store へ登録。
3. Compose の build args で Docker の定義済み Proxy build arguments (`HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`) を渡す。Dockerfile では `ARG HTTP_PROXY` 等を宣言せず、Proxy credential を image metadata/cacheへ残しにくい形を維持する。
4. `pip install --no-cache-dir .` を Proxy + CA 配下で実行する。実行時 Proxy 値は Compose の `environment` から注入し、image の `ENV` へ bake しない。
5. 実行時 command は Compose 側でサービスごとに明示する。

これにより、既存各サービス Dockerfile を Proxy 専用に複製しない。

### 7.2 Console UI

`docker/console-ui.Dockerfile` を使用する。

- Node 22 系
- Zscaler CA を trust store と Node/npm に設定
- `npm install` を build stage で実行
- `npm test` と production build はテスト手順で別途確認
- production build は Nginx から配信し、ホスト `5173` -> コンテナ `80` で公開

Vite の `/api` proxy target はローカル開発用に環境変数化する。Proxy Compose の production UI は Nginx が `/api/` を `http://console-api:8095/api/` へ reverse proxy する。

### 7.3 New Relic MCP

既存固定版 MCP source を `vendor/newrelic-mcp-server/` に同梱し、専用 Proxy Dockerfile で build する。

- Python 3.12 系を維持
- Streamable HTTP
- `MCP_HOST=0.0.0.0`
- `MCP_PORT=8000`
- `HTTP_PROXY/HTTPS_PROXY/NO_PROXY`
- OS trust store + Zscaler CA
- `SSL_CERT_FILE`
- read-only NRQL default を維持

MCP source の `httpx.AsyncClient` は環境 Proxy を利用する前提を維持し、Proxy をハードコードしない。

## 8. Compose サービス設計

Proxy override で以下を一元管理する。

```text
splunk
postgres
scenario-controller
payment-mock
payment-service
inventory-service
order-service
api-gateway
load-generator
newrelic-mcp
console-api
console-ui
```

### 8.1 内部 URL

`.env.example` のhost/manual既定値は v11.4 と同じ `SPLUNK_SEARCH_URL=https://localhost:8089`、`NEW_RELIC_MCP_URL=http://localhost:8000/mcp`、`OLLAMA_BASE_URL=http://localhost:11434` を維持する。Proxy overlay の `console-api` だけ接続先をCompose内向けに固定する。

```env
SPLUNK_SEARCH_URL=https://splunk:8089
NEW_RELIC_MCP_URL=http://newrelic-mcp:8000/mcp
OLLAMA_BASE_URL=${OLLAMA_CONTAINER_BASE_URL:-http://host.docker.internal:11434}
```

`.env.example` には `OLLAMA_CONTAINER_BASE_URL=http://host.docker.internal:11434` を用意し、host/manual用URLとcontainer用URLを分離する。

ホスト公開ポートは既存を維持する。

```text
API Gateway       8085
Scenario           8091
Splunk Web        18000
Splunk HEC         8088
Splunk Mgmt        8089
New Relic MCP      8000
Console API        8095
Console UI         5173
```

### 8.2 外向き Proxy 対象

Proxy を注入する対象:

```text
scenario-controller
payment-mock
payment-service
inventory-service
order-service
api-gateway
load-generator
newrelic-mcp
console-api
console-ui (build-time)
```

理由:

- Demo Commerce 各 Python service -> New Relic APM
- New Relic MCP -> NerdGraph / New Relic API
- Console API -> Azure OpenAI / OpenAI
- build 時 -> PyPI / npm registry

Splunk と PostgreSQL は PoC の内部基盤として扱い、通常は外向き Proxy を注入しない。

## 9. LLM provider 設計

既存 provider 契約を維持する。

```text
ollama
azure_openai
openai
```

自動 failover は追加しない。

### Azure OpenAI / OpenAI

Console API へ Proxy / CA を注入することで外向き通信を Proxy 経由にする。

### Ollama

同一 Docker network 外のホスト Ollama を利用する場合に備え、Linux では:

```text
host.docker.internal -> host-gateway
```

を Console API に付与可能とし、`host.docker.internal` を `NO_PROXY` に含める。

例:

```env
OLLAMA_BASE_URL=http://host.docker.internal:11434
```

別の `10.x.x.x` 等で Ollama を提供する場合は RFC1918 `NO_PROXY` で Proxy を回避する。

## 10. Splunk 再セットアップ設計

手順書と bootstrap script で、次を再現する。

### 10.1 HEC / index

```text
index      = poc_observability
sourcetype = poc:app:json
```

HEC token は `.env` から渡す。

### 10.2 Read Only RBAC

```text
Role: poc_ai_search
User: ai_search
```

最終実効 Capability は:

```text
search
```

のみとする。

検索可能 index:

```text
poc_observability
```

内部 index 例 `index=_internal` は取得不可であることを検証する。

Splunk 10.4.x で新規 role に継承されうる不要 capability は local authorize 設定で明示的に無効化する。

PoC アプリ側 Guard も維持する。

- `index=poc_observability` 固定
- 最大検索期間 2 時間
- 最大結果 200 件
- write / collect / output / script / map / macro 系 SPL を拒否

## 11. New Relic 再セットアップ設計

### 11.1 Demo Commerce APM

`.env`:

```env
NEW_RELIC_ENABLED=true
NEW_RELIC_LICENSE_KEY=
NEW_RELIC_DISTRIBUTED_TRACING_ENABLED=true
```

各 service の既存 app name は維持する。

```text
poc-api-gateway
poc-order-service
poc-inventory-service
poc-payment-service
poc-payment-mock
poc-scenario-controller
```

Proxy + CA:

```env
NEW_RELIC_PROXY_HOST=${HTTPS_PROXY}
NEW_RELIC_CA_BUNDLE_PATH=/etc/ssl/certs/ca-certificates.crt
```

### 11.2 MCP

起動後は:

```text
http://localhost:8000/mcp
```

Compose 内からは:

```text
http://newrelic-mcp:8000/mcp
```

を使用する。

既存の `tools/list` integration test を MCP transport smoke test として維持する。

代表 Tool:

```text
analyze_golden_metrics
analyze_transactions
analyze_deployment_impact
list_change_events
execute_nrql_query
```

既知事項として `list_change_events` の NerdGraph schema 不整合が起こりうるため、Change Tracking の NRQL fallback を維持する。

## 12. UI / Console API 設計

### Console API

- 0.0.0.0:8095
- Splunk / MCP 内部 URL を Compose 名で利用
- LLM provider は `.env` で選択
- Proxy / CA は runtime env から利用
- 既存の safe LLM error classification を維持
- API Key、Evidence 本文、Proxy credential をログへ出さない

### Console UI

- 0.0.0.0:5173
- `/api` -> `console-api:8095`
- 日本語既定
- A2UI / AG-UI 契約維持
- Read Only UI 維持

## 13. セキュリティ設計

秘密情報は `.env` のみに保持する。

```text
NEW_RELIC_LICENSE_KEY
NEW_RELIC_API_KEY
NEW_RELIC_MCP_USER_KEY
SPLUNK_PASSWORD
SPLUNK_HEC_TOKEN
SPLUNK_SEARCH_PASSWORD
AZURE_OPENAI_API_KEY
OPENAI_API_KEY
Proxy credential を URL に含める場合の HTTP_PROXY / HTTPS_PROXY
```

次を禁止する。

- `.env` の ZIP 同梱
- 実 Zscaler CA の ZIP 同梱
- `docker compose config` の生出力を手順で推奨すること
- Proxy URL に credential が含まれる場合、その URL をログへ出すこと
- API Key を healthcheck / command line に直接展開して表示すること

構成検証には `docker compose config --quiet` を使う。

## 14. エラー処理 / 診断設計

### preflight failure

起動前に以下を検査し、欠落時は明示的に停止する。

- `.env` 存在
- `HTTP_PROXY` / `HTTPS_PROXY` 設定
- `certs/zscaler-ca.crt` 存在
- CA ファイルが PEM certificate 形式らしいこと
- `NO_PROXY` に必須 RFC1918 範囲と Compose service 名が含まれること
- 選択 provider に必要な credential が設定されていること

### runtime failure

診断順序を手順書で固定する。

```text
container health
-> Proxy env
-> CA trust
-> internal NO_PROXY routing
-> Splunk
-> New Relic APM
-> MCP
-> provider-only LLM
-> Phase 4 E2E
-> Phase 5 E2E
```

これにより、LLM失敗を即 quota と判断するような誤診断を避ける。

## 15. セットアップドキュメント

### `docs/SETUP_PROXY_ZSCALER.md`

1. 前提確認
2. ZIP 展開
3. `.env.example` -> `.env`
4. Proxy 設定
5. credentials 設定
6. Zscaler CA 配置
7. preflight
8. Proxy Compose build
9. Splunk 起動
10. Splunk index / HEC / RBAC bootstrap
11. Demo Commerce 起動
12. New Relic APM 確認
13. New Relic MCP 確認
14. Console API / UI 起動
15. LLM provider 設定
16. ブラウザ確認
17. 停止 / 再起動 / ログ確認

### `docs/TESTING_PROXY_ZSCALER.md`

テストを以下の 3 層に分離する。

#### Layer 1: Proxy / CA

- `.env` preflight
- Proxy env が対象コンテナへ渡っている
- `NO_PROXY` 必須値
- CA が trust store に登録済み
- 外向き HTTPS probe が TLS error にならない
- 内部 `splunk` / `newrelic-mcp` が Proxy を使わず到達可能

#### Layer 2: Component

- Python unit tests
- Console node tests
- Console production build
- Demo Commerce integration
- Splunk HEC integration
- Splunk Read Only integration
- New Relic MCP transport `tools/list`
- Cross-source `request.id -> trace.id`
- provider-only Ollama / Azure OpenAI / OpenAI（選択 provider のみ必須）

#### Layer 3: Full E2E

```text
request.id
-> Splunk
-> trace.id
-> New Relic MCP
-> EvidenceBundle
-> selected LLM
-> IncidentAnalysis
-> AG-UI progress
-> A2UI result
-> browser
```

Python テストは既存方針どおり `python:3.11-slim` を使用する。

## 16. TDD / 検証要件

実装は TDD で行う。

追加する最低限の自動テスト:

- Proxy Compose が新 3 service を定義する
- 各アプリ service へ Proxy / CA env が伝播する
- 必須 `NO_PROXY` の RFC1918 範囲と service names を検証する
- Console API の Proxy 環境用 internal URL を検証する
- MCP の read-only / account allow-list env を検証する
- preflight が CA 欠落、Proxy 欠落、NO_PROXY 欠落を拒否する
- preflight が正しい fixture を受理する
- Vite proxy target が環境変数化される
- secrets が生成ドキュメントや `.env.example` に実値として入らない

最終 fresh verification:

```text
Python unit
Python integration (offline subset)
console npm test
console npm run build
docker compose config --quiet
shell syntax check
ZIP integrity
```

実 Proxy/Zscaler 環境でのみ確認可能な live 項目:

```text
Proxy TLS probe
New Relic APM ingest
New Relic MCP tools/list
Azure/OpenAI provider-only
Splunk real search
Phase 4 real E2E
Phase 5 live console E2E
Browser E2E
```

## 17. 成功条件

新規 Proxy + Zscaler 環境で、利用者が以下を完了できること。

1. ZIP を展開する。
2. `.env` に Proxy / credentials を設定する。
3. `certs/zscaler-ca.crt` を配置する。
4. preflight を PASS する。
5. `make proxy-up` で必要サービスを build / start する。
6. Splunk bootstrap を実行する。
7. `make proxy-test` で component verification を実行する。
8. 実 request を生成する。
9. Splunk と New Relic MCP の相関を確認する。
10. 選択 LLM で IncidentAnalysis を取得する。
11. `http://VMのIPアドレス:5173` から日本語 Console で分析結果を確認する。

最終成果物には、コード ZIP、セットアップ手順、テスト手順、更新済み引継ぎ Markdown を含める。

## 18. 互換性 / 非変更事項

以下は Phase 5 完了版から変えない。

- Evidence-first
- Splunk / New Relic Read Only
- Observe -> Investigate -> Correlate -> Recommend
- `request.id` / `trace.id` 相関
- `IncidentAnalyzer` Protocol
- `IncidentAnalysis` contract
- Evidence / Query / Contradiction reference validation
- AG-UI progress semantics
- A2UI fixed catalog
- advisory-only Recommendation
- LLM provider 自動 failover 禁止
- 日本語 UI / 日本語分析既定


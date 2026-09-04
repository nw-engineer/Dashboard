# New Relic + Splunk + A2UI/AG-UI PoC 引継ぎメモ v12.0

更新日: 2026-09-04

## 1. v12.0 の位置づけ

v12.0 は Phase 5 完了版 v11.4 をベースに、**Proxy + Zscaler CA 環境へ新規デプロイできる配布版**として拡張したもの。

Phase 1〜5 の Evidence-first / Read Only / AG-UI / A2UI / 日本語既定 UI / multi-provider LLM の契約は維持する。

この版で追加した主な機能:

- `.env` で `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` を設定
- Zscaler CA を利用者が `certs/zscaler-ca.crt` に配置
- Proxy/Zscaler 環境専用 `compose.proxy.yml`
- 通常環境の既存 Dockerfile は v11.4 のまま維持
- Proxy 環境のみ共通 `docker/python-service-proxy.Dockerfile` に差し替え
- Demo Commerce の New Relic Agent 通信を Proxy + Zscaler CA 対応
- New Relic MCP Server を固定版ソースごと同梱
- Console API / Console UI / New Relic MCP を同一 Compose project へ統合
- Splunk Read Only role/user の bootstrap script
- Proxy/CA preflight / smoke verification scripts
- 1からのセットアップ手順書とテスト手順書

## 2. 重要な安全境界

変更していない基本方針:

```text
Observe -> Investigate -> Correlate -> Recommend
```

AIから本番変更は行わない。Splunk/New Relic は調査用途で Read Only。LLM は Evidence-first で、Evidence/Query/Contradiction reference validation を維持する。Provider の自動 failover は行わない。

秘密情報は `.env` のみに置き、ZIPには含めない。実 Zscaler CA も ZIP には含めない。

## 3. Proxy / NO_PROXY

`.env.example` から `.env` を作成し、利用環境の Proxy URL を設定する。

```dotenv
HTTP_PROXY=http://proxy.example.local:8080
HTTPS_PROXY=http://proxy.example.local:8080
```

必須 NO_PROXY 範囲:

```text
localhost
127.0.0.1
::1
10.0.0.0/8
172.16.0.0/12
192.168.0.0/16
host.docker.internal
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

環境固有の内部 FQDN/IP は `NO_PROXY_EXTRA` に追加する。

CIDR 解釈は HTTP client 実装差があるため、Compose 内部通信は CIDR だけに依存せず service DNS name も明示する。

## 4. Zscaler CA

利用者が以下へ PEM/CRT を配置する。

```text
certs/zscaler-ca.crt
```

実証明書は `.gitignore` 対象で、配布 ZIP には含めない。

Proxy 専用 Python images は OS trust store を更新し、以下を使用する。

```text
SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
NEW_RELIC_CA_BUNDLE_PATH=/etc/ssl/certs/ca-certificates.crt
```

Node builder は `NODE_EXTRA_CA_CERTS` と `npm_config_cafile` を設定する。

## 5. 通常環境と Proxy 環境の分離

通常環境:

```bash
docker compose up -d --build
```

既存 `services/*/Dockerfile` / `load_generator/Dockerfile` を使用し、Zscaler CA を要求しない。

Proxy + Zscaler 環境:

```bash
make proxy-preflight
make proxy-up
./scripts/bootstrap_splunk_readonly.sh
make proxy-test
```

`compose.proxy.yml` が Demo Commerce/load-generator を `docker/python-service-proxy.Dockerfile` へ差し替える。

## 6. Host/manual URL と Compose internal URL

通常の host/manual tests 用 `.env` default は維持する。

```dotenv
SPLUNK_SEARCH_URL=https://localhost:8089
NEW_RELIC_MCP_URL=http://localhost:8000/mcp
OLLAMA_BASE_URL=http://localhost:11434
```

Proxy Compose 内の `console-api` は以下へ差し替える。

```text
Splunk Search  -> https://splunk:8089
New Relic MCP  -> http://newrelic-mcp:8000/mcp
Ollama on host -> http://host.docker.internal:11434
```

`.env` では Ollama container 用に:

```dotenv
OLLAMA_CONTAINER_BASE_URL=http://host.docker.internal:11434
```

を使用する。

## 7. 外向き通信

Proxy を通す対象:

```text
Demo Commerce -> New Relic APM
New Relic MCP -> New Relic NerdGraph/API
Console API -> Azure OpenAI / OpenAI
その他 Internet-bound client
```

内部通信は NO_PROXY:

```text
service-to-service HTTP
PostgreSQL
Splunk HEC/Search
Console API -> New Relic MCP
Console UI -> Console API
host Ollama via host.docker.internal
```

Docker daemon / base image pull 用 Proxy 設定は **本プロジェクトの対象外**。新環境側で `docker pull` 可能な状態を事前準備する。

## 8. New Relic MCP

同梱 source:

```text
vendor/newrelic-mcp-server/
version = 0.1.0
source zip sha256 = 60a34b9b82cddc9d7c3dcb69f0e6b279d3deb812d7ab10467fd5178475ca2141
```

Compose service:

```text
newrelic-mcp
http://newrelic-mcp:8000/mcp
host: http://localhost:8000/mcp
transport: streamable-http
```

主な設定:

```text
NEW_RELIC_MCP_USER_KEY
NEW_RELIC_REGION
NEW_RELIC_GRAPHQL_URL
NEW_RELIC_ALLOWED_ACCOUNT_IDS
NEW_RELIC_ACCOUNT_ID
```

既存の MCP `/mcp` / `tools/list` contract は維持する。

## 9. Splunk

固定データ契約:

```text
index      = poc_observability
sourcetype = poc:app:json
role       = poc_ai_search
user       = ai_search
search time limit = 7200 seconds
```

`./scripts/bootstrap_splunk_readonly.sh` が role/user を作成/更新し、実効 capability が **exactly `search`** であることを確認する。

過去の Splunk Enterprise 10.4.3 実機で確認した inherited capability 対策を維持:

```ini
[role_poc_ai_search]
edit_own_objects = disabled
list_all_objects = disabled
run_collect = disabled
run_mcollect = disabled
schedule_rtsearch = disabled
search = enabled
srchIndexesAllowed = poc_observability
srchIndexesDefault = poc_observability
srchTimeWin = 7200
```

bootstrap はさらに:

- `index=_internal` が読めないこと
- `index=poc_observability` が検索可能なこと

を確認する。

## 10. LLM Provider

既存 3 provider を維持:

```text
ollama       (default)
azure_openai
openai
```

自動 provider failover はない。

Azure/OpenAI を選ぶ場合、Console API の外向き HTTPS は Proxy + Zscaler trust を利用する。

## 11. Make targets

```text
make proxy-preflight
make proxy-up
make proxy-ps
make proxy-test
make proxy-down
```

通常環境向け既存 `make up/down/logs/test` も維持。

## 12. セットアップ・テスト手順

必ず以下を参照:

```text
docs/SETUP_PROXY_ZSCALER.md
docs/TESTING_PROXY_ZSCALER.md
```

セットアップ手順は、`.env` / Proxy / NO_PROXY / CA / Splunk / APM / MCP / LLM / Console API/UI / Phase 4 E2E / Phase 5 E2E / browser 確認までを1から記載している。

## 13. v12.0 作成環境で確認済み

この配布版作成環境には Docker CLI/daemon と npm dependency cache が無いため、Docker build/live と Vite production build は target environment 確認として残す。一方、現在実行可能な範囲は fresh run で以下を確認済み。

```text
Python unit tests                  241 passed
Python offline integration         3 passed
Vendored New Relic MCP tests      14 passed
Console Node tests                22 passed / 0 failed
Python compileall                 PASS
Shell syntax check                PASS
Base/Proxy YAML parse             PASS
Base Dockerfiles vs v11.4         unchanged
Real .env in package              absent
Real zscaler-ca.crt in package    absent
```

Python の上記ローカル検証は Python 3.13.5（PoC 要件 `>=3.11,<3.14` 内）で実施。最終 target では要件どおり `python:3.11-slim` で再実行する。

Console production build はこの作成環境に `node_modules` がなく、外部 registry も利用できないため未実施。これは source regression を示す失敗ではなく dependency unavailable。v11.4 ではユーザー VM 上で production build PASS 済みだが、v12.0 は target Proxy/Zscaler 環境で再確認する。

## 14. Target Proxy/Zscaler 環境で必須の最終確認

以下は配布先で実施する。

```text
1. docker compose ... config --quiet
2. make proxy-preflight
3. make proxy-up
4. ./scripts/bootstrap_splunk_readonly.sh
5. make proxy-test
6. python:3.11-slim full unit suite
7. Node 22 npm test + npm run build
8. vendored MCP tests
9. Splunk live search integration
10. New Relic MCP tools/list integration
11. selected LLM provider-only live test
12. Phase 4 complete live E2E
13. Phase 5 live Console E2E
14. browser investigation (5 progress steps complete)
```

## 15. 新環境で最初に実行する流れ

```bash
unzip observability-ai-poc-proxy-zscaler-v12.0.zip
cd observability-ai-poc
cp .env.example .env
# .env に実際の Proxy / credentials を設定
# certs/zscaler-ca.crt を配置
make proxy-preflight
make proxy-up
./scripts/bootstrap_splunk_readonly.sh
make proxy-test
```

その後 `docs/TESTING_PROXY_ZSCALER.md` の順番で full verification を実施する。

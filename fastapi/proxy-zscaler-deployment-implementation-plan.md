# Proxy + Zscaler Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Phase 5 complete PoC reproducibly deployable from a fresh checkout/ZIP in a Proxy + Zscaler CA environment, while preserving the existing Evidence-first/read-only behavior and adding complete setup and verification instructions.

**Architecture:** Keep the validated `docker-compose.yml` as the base stack and add `compose.proxy.yml` as a deployment overlay. The overlay injects build-time/runtime proxy settings and Zscaler trust, adds New Relic MCP, Console API, and Console UI to the same Compose project, and switches internal endpoints from host `localhost` URLs to Compose service DNS names. The actual Zscaler CA file is supplied by the operator at `certs/zscaler-ca.crt` and is never shipped in the ZIP.

**Tech Stack:** Docker Compose, Docker BuildKit-compatible build args, Python 3.11/3.12, FastAPI/Uvicorn, New Relic Python Agent, self-hosted New Relic MCP, Splunk Enterprise, React/Vite/TypeScript, Nginx, Bash, pytest, Node test runner.

**Spec:** `docs/superpowers/specs/2026-09-04-proxy-zscaler-deployment-design.md`

## Global Constraints

- Host/Docker daemon proxy configuration is out of scope; the host must already be able to pull base images.
- Real Zscaler CA material is not committed or packaged. Runtime path: `certs/zscaler-ca.crt`.
- Proxy values are provided only through `.env`; do not hard-code proxy hostnames, ports, usernames, or passwords.
- `NO_PROXY` must include `localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16` plus Compose service DNS names.
- External calls use the proxy: New Relic APM, New Relic MCP -> New Relic API, Azure OpenAI/OpenAI, and any other Internet-bound client.
- Internal calls bypass the proxy: service-to-service HTTP, PostgreSQL, Splunk HEC/Search, Console API -> New Relic MCP, Console UI -> Console API.
- Preserve Splunk read-only contract: index `poc_observability`, maximum two-hour search window, maximum 200 rows, search-only `ai_search` credential.
- Preserve current MCP interface: Streamable HTTP `/mcp`; required tools include `execute_nrql_query` and `list_change_events`.
- Preserve provider selection and no automatic LLM provider failover.
- Python test/runtime requirement for the PoC remains `>=3.11,<3.14`; project tests run with `python:3.11-slim`.
- Never print `.env`, API keys, proxy credentials, license keys, HEC tokens, or evidence payloads in setup scripts or diagnostics.

---

## File Structure

Create or modify the following files only for this feature:

```text
.env.example                                  # proxy, CA, internal service defaults
.gitignore                                    # keep real CA and .env out of source control
Makefile                                      # proxy-up/proxy-down/proxy-test helpers
compose.proxy.yml                             # overlay for proxy/CA + MCP/API/UI
certs/README.md                               # operator CA placement instructions
certs/.gitkeep                                # keep directory in ZIP
docker/python-ca-install.sh                   # shared Python image CA install helper
docker/console-api.Dockerfile                 # packaged Console API image
docker/console-ui.Dockerfile                  # Vite build + Nginx runtime
docker/console-ui-nginx.conf                  # /api reverse proxy to console-api
docker/newrelic-mcp.Dockerfile                # packaged MCP image with CA trust
vendor/newrelic-mcp-server/...                # fixed MCP source currently used by the PoC
scripts/preflight_proxy.sh                    # validate env/CA before build
scripts/verify_proxy_ca.sh                    # safe runtime proxy/CA checks
scripts/verify_stack.sh                       # Splunk/MCP/API/UI smoke verification
scripts/bootstrap_splunk_readonly.sh          # reproducible Splunk search-only RBAC bootstrap
docs/SETUP_PROXY_ZSCALER.md                   # complete fresh install procedure
docs/TESTING_PROXY_ZSCALER.md                    # test matrix and commands
tests/unit/test_proxy_env_contract.py         # .env/NO_PROXY/secret contract
tests/unit/test_proxy_compose.py              # overlay structure/internal URLs
console/src/app/proxyConfig.node.test.ts      # Vite development proxy contract
tests/unit/test_proxy_dockerfiles.py          # CA-aware Docker image contracts
tests/unit/test_proxy_scripts.py              # preflight and safe-output contract
```

Existing service Dockerfiles modified by this plan:

```text
docker/python-service-proxy.Dockerfile            # Proxy-only shared image for Demo Commerce/load-generator
# Existing services/*/Dockerfile and load_generator/Dockerfile remain unchanged for the normal workflow
```

---

### Task 1: Define the Proxy/CA Configuration Contract

**Files:**
- Modify: `.env.example`
- Modify: `.gitignore`
- Create: `certs/README.md`
- Create: `certs/.gitkeep`
- Create: `tests/unit/test_proxy_env_contract.py`

**Interfaces:**
- Consumes: existing `.env.example` values for New Relic, Splunk, MCP, and LLM providers.
- Produces: canonical environment variable names used by every later task: `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, lowercase proxy aliases, `ZSCALER_CA_PATH`, `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `NODE_EXTRA_CA_CERTS`, `NEW_RELIC_PROXY_HOST`, `NEW_RELIC_CA_BUNDLE_PATH`.

- [ ] **Step 1: Write the failing environment contract tests**

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_env_example_declares_proxy_and_ca_contract():
    text = (ROOT / ".env.example").read_text()
    for item in [
        "HTTP_PROXY=",
        "HTTPS_PROXY=",
        "NO_PROXY=",
        "http_proxy=",
        "https_proxy=",
        "no_proxy=",
        "ZSCALER_CA_PATH=/usr/local/share/ca-certificates/zscaler-ca.crt",
        "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt",
        "REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt",
        "NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/zscaler-ca.crt",
        "npm_config_cafile=/etc/ssl/certs/ca-certificates.crt",
        "NEW_RELIC_PROXY_HOST=",
        "NEW_RELIC_CA_BUNDLE_PATH=/etc/ssl/certs/ca-certificates.crt",
        "NEW_RELIC_MCP_USER_KEY=",
        "NEW_RELIC_REGION=US",
        "NEW_RELIC_GRAPHQL_URL=",
        "NEW_RELIC_ALLOWED_ACCOUNT_IDS=",
    ]:
        assert item in text


def test_no_proxy_contains_private_ranges_and_compose_service_names():
    text = (ROOT / ".env.example").read_text()
    required = [
        "localhost", "127.0.0.1", "::1",
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        "host.docker.internal", "splunk", "postgres", "scenario-controller", "payment-mock",
        "payment-service", "inventory-service", "order-service", "api-gateway",
        "load-generator", "newrelic-mcp", "console-api", "console-ui",
    ]
    for item in required:
        assert item in text
    assert "NO_PROXY_EXTRA=" in text


def test_real_ca_is_ignored_but_documented():
    ignore = (ROOT / ".gitignore").read_text()
    assert "certs/zscaler-ca.crt" in ignore
    assert (ROOT / "certs/README.md").exists()


def test_env_example_contains_no_obvious_real_secret_prefixes():
    text = (ROOT / ".env.example").read_text()
    for forbidden in ["NRAK-", "sk-proj-", "Bearer ", "Splunk "]:
        assert forbidden not in text
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_proxy_env_contract.py -v
```

Expected: failures because proxy/CA variables and `certs/` documentation do not yet exist.

- [ ] **Step 3: Add the canonical `.env.example` block**

Add a single block with this exact contract, leaving credentials blank:

```dotenv
# Proxy + Zscaler CA
HTTP_PROXY=
HTTPS_PROXY=
http_proxy=${HTTP_PROXY}
https_proxy=${HTTPS_PROXY}
NO_PROXY=localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,host.docker.internal,splunk,postgres,scenario-controller,payment-mock,payment-service,inventory-service,order-service,api-gateway,load-generator,newrelic-mcp,console-api,console-ui
NO_PROXY_EXTRA=
no_proxy=${NO_PROXY}
ZSCALER_CA_PATH=/usr/local/share/ca-certificates/zscaler-ca.crt
SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/zscaler-ca.crt
npm_config_cafile=/etc/ssl/certs/ca-certificates.crt
NEW_RELIC_PROXY_HOST=${HTTPS_PROXY}
NEW_RELIC_CA_BUNDLE_PATH=/etc/ssl/certs/ca-certificates.crt
NEW_RELIC_MCP_USER_KEY=
NEW_RELIC_REGION=US
NEW_RELIC_GRAPHQL_URL=
NEW_RELIC_ALLOWED_ACCOUNT_IDS=
```

Preserve the existing host/manual defaults in `.env.example`:

```dotenv
SPLUNK_SEARCH_URL=https://localhost:8089
NEW_RELIC_MCP_URL=http://localhost:8000/mcp
```

`compose.proxy.yml` must override only the `console-api` container to the Compose-internal URLs:

```text
https://splunk:8089
http://newrelic-mcp:8000/mcp
```

This keeps the v11.4 non-Proxy/manual workflow unchanged while the Proxy overlay uses service DNS. For Ollama the same rule applies: preserve `OLLAMA_BASE_URL=http://localhost:11434` for host/manual tests, add `OLLAMA_CONTAINER_BASE_URL=http://host.docker.internal:11434`, and have the overlay pass the latter as `OLLAMA_BASE_URL` inside `console-api`.

- [ ] **Step 4: Ignore the real CA and document its placement**

`.gitignore` must contain:

```text
.env
certs/zscaler-ca.crt
```

`certs/README.md` must state that the operator copies the PEM/CRT CA to `certs/zscaler-ca.crt`, and that the repository/ZIP intentionally does not contain the certificate.

- [ ] **Step 5: Run the tests and verify GREEN**

```bash
python -m pytest tests/unit/test_proxy_env_contract.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit if the execution workspace has Git metadata**

```bash
git add .env.example .gitignore certs tests/unit/test_proxy_env_contract.py
git commit -m "feat: define proxy and zscaler environment contract"
```

If `.git` is absent because execution started from the distributed ZIP, record `commit skipped: source ZIP has no Git metadata` in the execution log and continue.

---

### Task 2: Add a Proxy-Only Shared Python Image Without Breaking the Base Workflow

**Files:**
- Create: `docker/python-ca-install.sh`
- Create: `docker/python-service-proxy.Dockerfile`
- Keep unchanged: `services/*/Dockerfile`, `load_generator/Dockerfile`
- Modify: `compose.proxy.yml`
- Create/Modify: `tests/unit/test_proxy_dockerfiles.py`

**Interfaces:**
- Consumes: `certs/zscaler-ca.crt`, Docker build args `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`.
- Produces: a Proxy/Zscaler-only Python image used by Demo Commerce and load-generator through the Compose overlay. The normal `docker-compose.yml` remains CA-independent and buildable without `certs/zscaler-ca.crt`.

- [x] **Step 1: RED - assert base Dockerfiles are CA-agnostic and the proxy-only image/overlay exist**

The contract tests require the seven base Dockerfiles to contain no Proxy/CA COPY instructions, require `docker/python-service-proxy.Dockerfile` to install the CA before `pip install`, and require every Proxy-overlay application service to switch to that Dockerfile with an explicit command.

- [x] **Step 2: Verify RED**

Observed: 3 expected failures before implementation (base Dockerfiles were Proxy-coupled, shared proxy Dockerfile missing, overlay not switched).

- [x] **Step 3: Restore the seven base Dockerfiles from the v11.4 baseline**

This preserves the existing non-Proxy workflow and prevents a missing operator CA from breaking normal builds.

- [x] **Step 4: Create `docker/python-service-proxy.Dockerfile`**

The image uses Python 3.11, receives Docker predefined Proxy build arguments from Compose without declaring `ARG HTTP_PROXY`/`ARG HTTPS_PROXY`/`ARG NO_PROXY`, copies `certs/zscaler-ca.crt`, runs `docker/python-ca-install.sh`, copies the PoC Python packages, and runs `pip install --no-cache-dir .`. Runtime Proxy values come only from the Compose environment so credentials are not baked into image ENV metadata.

- [x] **Step 5: Switch the Proxy overlay only**

For `scenario-controller`, `payment-mock`, `payment-service`, `inventory-service`, `order-service`, `api-gateway`, and `load-generator`, `compose.proxy.yml` overrides `build.dockerfile` with `docker/python-service-proxy.Dockerfile` and preserves each original runtime command explicitly.

- [x] **Step 6: Verify GREEN**

`tests/unit/test_proxy_dockerfiles.py`: 5 passed in the execution environment.

- [ ] **Step 7: Target environment build verification**

```bash
docker compose -f docker-compose.yml -f compose.proxy.yml --profile splunk build api-gateway
```

Expected: `pip install` completes through the Proxy with the operator-provided Zscaler CA.


---

### Task 3: Vendor and Package the Existing New Relic MCP Server

**Files:**
- Create: `vendor/newrelic-mcp-server/` from `newrelic-mcp-server-fixed.zip`
- Create: `docker/newrelic-mcp.Dockerfile`
- Extend: `tests/unit/test_proxy_dockerfiles.py`
- Modify: `.gitignore` only if needed to exclude MCP-local `.env`

**Interfaces:**
- Consumes: existing fixed MCP source whose CLI is `newrelic-mcp`, Python requirement `>=3.11`, and runtime env `NEW_RELIC_MCP_USER_KEY`, `NEW_RELIC_REGION`, `NEW_RELIC_GRAPHQL_URL`, `NEW_RELIC_ALLOWED_ACCOUNT_IDS`.
- Produces: Compose-ready Streamable HTTP service at `http://newrelic-mcp:8000/mcp` and the same tool contract already validated by `tests/integration/test_newrelic_mcp_transport_integration.py`.

- [ ] **Step 1: Add a failing test for the vendored MCP image contract**

Append:

```python
def test_newrelic_mcp_image_uses_vendored_fixed_server_and_zscaler_ca():
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "docker/newrelic-mcp.Dockerfile").read_text()
    assert "vendor/newrelic-mcp-server" in dockerfile
    assert "COPY certs/zscaler-ca.crt" in dockerfile
    assert "MCP_TRANSPORT=streamable-http" in dockerfile
    assert 'CMD ["newrelic-mcp"]' in dockerfile
```

- [ ] **Step 2: Run and verify RED**

```bash
python -m pytest tests/unit/test_proxy_dockerfiles.py::test_newrelic_mcp_image_uses_vendored_fixed_server_and_zscaler_ca -v
```

Expected: FAIL because the image does not exist.

- [ ] **Step 3: Copy the fixed MCP source without its local secrets/cache**

Copy the contents of the validated `newrelic-mcp-server-fixed.zip` into:

```text
vendor/newrelic-mcp-server/
```

Exclude `.env`, `.venv`, `__pycache__`, `.pytest_cache`, and build artifacts. Keep its `LICENSE`, `pyproject.toml`, source, tests, README, and validation docs.

- [ ] **Step 4: Create the MCP Dockerfile**

```dockerfile
FROM python:3.12-slim
# HTTP_PROXY/HTTPS_PROXY/NO_PROXY are Docker predefined build args supplied by Compose.
# Do not redeclare or persist them in ENV because Proxy URLs may contain credentials.
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000
WORKDIR /app
COPY certs/zscaler-ca.crt /usr/local/share/ca-certificates/zscaler-ca.crt
COPY docker/python-ca-install.sh /usr/local/bin/python-ca-install.sh
RUN chmod 0755 /usr/local/bin/python-ca-install.sh && /usr/local/bin/python-ca-install.sh
COPY vendor/newrelic-mcp-server/pyproject.toml vendor/newrelic-mcp-server/README.md ./
COPY vendor/newrelic-mcp-server/src ./src
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["newrelic-mcp"]
```

- [ ] **Step 5: Run vendored MCP unit tests in Python 3.12**

```bash
docker run --rm \
  -v "$PWD/vendor/newrelic-mcp-server:/workspace" \
  -w /workspace \
  python:3.12-slim \
  sh -lc "python -m pip install -q -e '.[dev]' && python -m pytest -q"
```

Expected: all MCP tests PASS.

- [ ] **Step 6: Run PoC-side Dockerfile contract test**

```bash
python -m pytest tests/unit/test_proxy_dockerfiles.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit if Git is available**

```bash
git add vendor/newrelic-mcp-server docker/newrelic-mcp.Dockerfile tests/unit/test_proxy_dockerfiles.py .gitignore
git commit -m "feat: package validated new relic mcp server"
```

---

### Task 4: Add Proxy Compose Overlay and Integrate MCP, Console API, and UI

**Files:**
- Create: `compose.proxy.yml`
- Create: `docker/console-api.Dockerfile`
- Create: `docker/console-ui.Dockerfile`
- Create: `docker/console-ui-nginx.conf`
- Modify: `console/vite.config.ts`
- Create: `console/src/app/proxyConfig.node.test.ts`
- Create: `tests/unit/test_proxy_compose.py`

**Interfaces:**
- Consumes: proxy/CA env from Task 1, Proxy-only shared image from Task 2 and MCP image from Task 3, existing Console API at port `8095`, existing UI source, Splunk management at `splunk:8089`.
- Produces: one Compose project with `newrelic-mcp`, `console-api`, `console-ui`; host ports remain `8000`, `8095`, `5173`; internal endpoints use service DNS and NO_PROXY.

- [ ] **Step 1: Write failing overlay contract tests**

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_proxy_overlay_adds_mcp_console_api_and_ui():
    text = (ROOT / "compose.proxy.yml").read_text()
    for service in ["newrelic-mcp:", "console-api:", "console-ui:"]:
        assert service in text
    assert "8000:8000" in text
    assert "8095:8095" in text
    assert "5173:80" in text


def test_console_api_uses_internal_service_urls():
    text = (ROOT / "compose.proxy.yml").read_text()
    assert "SPLUNK_SEARCH_URL: https://splunk:8089" in text
    assert "NEW_RELIC_MCP_URL: http://newrelic-mcp:8000/mcp" in text


def test_overlay_injects_proxy_env_and_build_args_without_proxying_internal_services():
    text = (ROOT / "compose.proxy.yml").read_text()
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"]:
        assert key in text
    assert text.count("<<: *proxy-env") >= 10
    assert text.count("args: *proxy-build-args") >= 10
    env_text = (ROOT / ".env.example").read_text()
    assert "10.0.0.0/8" in env_text
    assert "newrelic-mcp" in env_text
    assert "host.docker.internal" in env_text
```

- [ ] **Step 2: Run and verify RED**

```bash
python -m pytest tests/unit/test_proxy_compose.py -v
```

Expected: FAIL because `compose.proxy.yml` and packaged Console images do not exist.

- [ ] **Step 3: Create `docker/console-api.Dockerfile`**

Use Python 3.11, the same CA-install pattern as Task 2, install `.[dev]` only if runtime requires test extras (prefer plain `.`), copy `agent`, `console_api`, `shared`, `services`, `load_generator`, and run:

```dockerfile
CMD ["uvicorn", "console_api.app:app", "--host", "0.0.0.0", "--port", "8095"]
```

- [ ] **Step 4: Make the local Vite proxy target configurable**

Create `console/src/app/proxyConfig.node.test.ts` first:

```ts
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

test('vite dev proxy target is environment configurable with localhost default', () => {
  const source = readFileSync(new URL('../../../vite.config.ts', import.meta.url), 'utf8');
  assert.match(source, /VITE_DEV_API_PROXY_TARGET/);
  assert.match(source, /http:\/\/localhost:8095/);
});
```

Run:

```bash
cd console && npm test
```

Expected: the new test FAILS because `vite.config.ts` does not reference `VITE_DEV_API_PROXY_TARGET`.

Then update `console/vite.config.ts` to use:

```ts
const apiProxyTarget = process.env.VITE_DEV_API_PROXY_TARGET ?? 'http://localhost:8095';
```

and set `server.proxy['/api']` to `apiProxyTarget`. Re-run `npm test`; expected: PASS. The packaged Nginx runtime still uses `console-api:8095`; this Vite change exists so local/test behavior is explicit and testable.

- [ ] **Step 5: Create a multi-stage Console UI image**

Builder stage:

```dockerfile
FROM node:22-slim AS build
# Proxy values are supplied as Docker predefined build args and are not baked into ENV.
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/zscaler-ca.crt \
    npm_config_cafile=/etc/ssl/certs/ca-certificates.crt
WORKDIR /app
COPY certs/zscaler-ca.crt /usr/local/share/ca-certificates/zscaler-ca.crt
RUN cat /usr/local/share/ca-certificates/zscaler-ca.crt >> /etc/ssl/certs/ca-certificates.crt
COPY console/package*.json ./
RUN npm install
COPY console ./
RUN npm run build
```

Runtime stage:

```dockerfile
FROM nginx:1.27-alpine
COPY docker/console-ui-nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
```

Nginx config must proxy `/api/` to `http://console-api:8095/api/` and serve `index.html` for SPA routes.

- [ ] **Step 6: Create the proxy overlay with anchors**

Define reusable anchors:

```yaml
x-proxy-env: &proxy-env
  HTTP_PROXY: ${HTTP_PROXY}
  HTTPS_PROXY: ${HTTPS_PROXY}
  NO_PROXY: ${NO_PROXY}
  http_proxy: ${HTTP_PROXY}
  https_proxy: ${HTTPS_PROXY}
  no_proxy: ${NO_PROXY}
  SSL_CERT_FILE: ${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}
  REQUESTS_CA_BUNDLE: ${REQUESTS_CA_BUNDLE:-/etc/ssl/certs/ca-certificates.crt}

x-proxy-build-args: &proxy-build-args
  HTTP_PROXY: ${HTTP_PROXY}
  HTTPS_PROXY: ${HTTPS_PROXY}
  NO_PROXY: ${NO_PROXY}
```

For each buildable application service, merge `*proxy-env` into runtime environment and `*proxy-build-args` into `build.args`.

For New Relic-instrumented Demo Commerce services also set:

```yaml
NEW_RELIC_PROXY_HOST: ${NEW_RELIC_PROXY_HOST:-${HTTPS_PROXY}}
NEW_RELIC_CA_BUNDLE_PATH: ${NEW_RELIC_CA_BUNDLE_PATH:-/etc/ssl/certs/ca-certificates.crt}
```

Add:

```yaml
newrelic-mcp:
  build:
    context: .
    dockerfile: docker/newrelic-mcp.Dockerfile
    args: *proxy-build-args
  environment:
    <<: *proxy-env
    NEW_RELIC_USER_KEY: ${NEW_RELIC_MCP_USER_KEY}
    NEW_RELIC_REGION: ${NEW_RELIC_REGION:-US}
    NEW_RELIC_GRAPHQL_URL: ${NEW_RELIC_GRAPHQL_URL:-}
    NEW_RELIC_ALLOWED_ACCOUNT_IDS: ${NEW_RELIC_ALLOWED_ACCOUNT_IDS:-${NEW_RELIC_ACCOUNT_ID}}
    MCP_TRANSPORT: streamable-http
    MCP_HOST: 0.0.0.0
    MCP_PORT: "8000"
  ports:
    - "${NEW_RELIC_MCP_HOST_PORT:-8000}:8000"

console-api:
  build:
    context: .
    dockerfile: docker/console-api.Dockerfile
    args: *proxy-build-args
  environment:
    <<: *proxy-env
    SPLUNK_SEARCH_URL: https://splunk:8089
    SPLUNK_SEARCH_USERNAME: ${SPLUNK_SEARCH_USERNAME:-ai_search}
    SPLUNK_SEARCH_PASSWORD: ${SPLUNK_SEARCH_PASSWORD}
    SPLUNK_SEARCH_VERIFY_SSL: "false"
    NEW_RELIC_MCP_URL: http://newrelic-mcp:8000/mcp
    NEW_RELIC_ACCOUNT_ID: ${NEW_RELIC_ACCOUNT_ID}
    LLM_PROVIDER: ${LLM_PROVIDER:-ollama}
    OLLAMA_BASE_URL: ${OLLAMA_CONTAINER_BASE_URL:-http://host.docker.internal:11434}
  extra_hosts:
    - "host.docker.internal:host-gateway"
    OLLAMA_MODEL: ${OLLAMA_MODEL:-gpt-oss:20b}
    AZURE_OPENAI_API_KEY: ${AZURE_OPENAI_API_KEY:-}
    AZURE_OPENAI_BASE_URL: ${AZURE_OPENAI_BASE_URL:-}
    AZURE_OPENAI_MODEL: ${AZURE_OPENAI_MODEL:-}
    OPENAI_API_KEY: ${OPENAI_API_KEY:-}
    OPENAI_BASE_URL: ${OPENAI_BASE_URL:-https://api.openai.com/v1}
    OPENAI_MODEL: ${OPENAI_MODEL:-gpt-5.6-luna}
  ports:
    - "${CONSOLE_API_HOST_PORT:-8095}:8095"

console-ui:
  build:
    context: .
    dockerfile: docker/console-ui.Dockerfile
    args: *proxy-build-args
  ports:
    - "${CONSOLE_UI_HOST_PORT:-5173}:80"
```

Add dependencies/healthchecks so `console-api` waits for Splunk and MCP, and `console-ui` waits for Console API.

- [ ] **Step 7: Validate Compose rendering without exposing secrets**

Do not run `docker compose config` directly to terminal because it can interpolate credentials. Instead:

```bash
docker compose -f docker-compose.yml -f compose.proxy.yml config --quiet
docker compose -f docker-compose.yml -f compose.proxy.yml config --services
```

Expected: `config --quiet` exits 0, then the service list includes `splunk`, `postgres`, all Demo Commerce services, `newrelic-mcp`, `console-api`, and `console-ui`.

- [ ] **Step 8: Run unit tests and verify GREEN**

```bash
python -m pytest tests/unit/test_proxy_compose.py tests/unit/test_splunk_compose.py tests/unit/test_external_ports_config.py -v
```

Expected: PASS.

Also run:

```bash
sh -n scripts/preflight_proxy.sh scripts/verify_proxy_ca.sh scripts/verify_stack.sh scripts/bootstrap_splunk_readonly.sh
```

Expected: exit 0.

- [ ] **Step 9: Commit if Git is available**

```bash
git add compose.proxy.yml docker/console-api.Dockerfile docker/console-ui.Dockerfile docker/console-ui-nginx.conf console/vite.config.ts tests/unit/test_proxy_compose.py
git commit -m "feat: add proxy compose deployment stack"
```

---

### Task 5: Add Safe Preflight, Verification Scripts, and Make Targets

**Files:**
- Create: `scripts/preflight_proxy.sh`
- Create: `scripts/verify_proxy_ca.sh`
- Create: `scripts/verify_stack.sh`
- Create: `scripts/bootstrap_splunk_readonly.sh`
- Modify: `Makefile`
- Create: `tests/unit/test_proxy_scripts.py`

**Interfaces:**
- Consumes: `.env`, `certs/zscaler-ca.crt`, Compose overlay from Task 4.
- Produces: deterministic operator commands `make proxy-preflight`, `make proxy-up`, `make proxy-test`, `make proxy-down` that never echo secret values.

- [ ] **Step 1: Write failing script contract tests**

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_preflight_checks_required_files_and_proxy_names_without_printing_values():
    text = (ROOT / "scripts/preflight_proxy.sh").read_text()
    assert "certs/zscaler-ca.crt" in text
    assert "HTTP_PROXY" in text
    assert "HTTPS_PROXY" in text
    assert "NO_PROXY" in text
    assert "SPLUNK_PASSWORD" in text
    assert "NEW_RELIC_LICENSE_KEY" in text
    assert "NEW_RELIC_MCP_USER_KEY" in text
    assert "cat .env" not in text
    assert "env |" not in text
    assert "printenv" not in text


def test_makefile_exposes_proxy_workflow_targets():
    text = (ROOT / "Makefile").read_text()
    for target in ["proxy-preflight:", "proxy-up:", "proxy-test:", "proxy-down:"]:
        assert target in text


def _run_preflight(tmp_path, env_text: str, with_ca: bool = True):
    import os
    import subprocess

    (tmp_path / ".env").write_text(env_text)
    (tmp_path / "certs").mkdir()
    if with_ca:
        (tmp_path / "certs/zscaler-ca.crt").write_text(
            "-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----\n"
        )
    env = os.environ.copy()
    env["POC_ROOT"] = str(tmp_path)
    return subprocess.run(
        ["sh", str(ROOT / "scripts/preflight_proxy.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_preflight_rejects_missing_ca(tmp_path):
    result = _run_preflight(tmp_path, "HTTP_PROXY=http://p:1\nHTTPS_PROXY=http://p:1\nNO_PROXY=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,splunk,newrelic-mcp,console-api\n", with_ca=False)
    assert result.returncode != 0
    assert "ZSCALER_CA" in result.stderr


def test_preflight_rejects_missing_proxy(tmp_path):
    result = _run_preflight(tmp_path, "NO_PROXY=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,splunk,newrelic-mcp,console-api\n")
    assert result.returncode != 0
    assert "HTTP_PROXY" in result.stderr or "HTTPS_PROXY" in result.stderr


def test_preflight_rejects_incomplete_no_proxy(tmp_path):
    result = _run_preflight(
        tmp_path,
        "HTTP_PROXY=http://p:1\nHTTPS_PROXY=http://p:1\nNO_PROXY=localhost,splunk\n",
    )
    assert result.returncode != 0
    assert "NO_PROXY" in result.stderr


def test_preflight_accepts_complete_fixture(tmp_path):
    env_text = "\n".join([
        "HTTP_PROXY=http://p:1",
        "HTTPS_PROXY=http://p:1",
        "NO_PROXY=localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,host.docker.internal,splunk,postgres,scenario-controller,payment-mock,payment-service,inventory-service,order-service,api-gateway,load-generator,newrelic-mcp,console-api,console-ui",
        "SPLUNK_PASSWORD=x",
        "SPLUNK_HEC_TOKEN=x",
        "SPLUNK_SEARCH_PASSWORD=x",
        "NEW_RELIC_LICENSE_KEY=x",
        "NEW_RELIC_API_KEY=x",
        "NEW_RELIC_MCP_USER_KEY=x",
        "NEW_RELIC_ACCOUNT_ID=1",
        "LLM_PROVIDER=ollama",
    ]) + "\n"
    result = _run_preflight(tmp_path, env_text)
    assert result.returncode == 0, result.stderr
    assert "http://p:1" not in result.stdout + result.stderr
```

- [ ] **Step 2: Run and verify RED**

```bash
python -m pytest tests/unit/test_proxy_scripts.py -v
```

Expected: FAIL because the scripts and targets do not exist.

- [ ] **Step 3: Implement `preflight_proxy.sh` with name-only diagnostics**

Behavior:

```text
1. resolve `ROOT="${POC_ROOT:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"` so tests can use an isolated fixture directory
2. require `$ROOT/.env`
3. require non-empty `$ROOT/certs/zscaler-ca.crt` and require PEM markers `BEGIN CERTIFICATE` / `END CERTIFICATE`
4. source `$ROOT/.env` with `set -a` in a subshell
5. require non-empty HTTP_PROXY, HTTPS_PROXY, NO_PROXY
6. require NO_PROXY substrings for the three RFC1918 ranges and Compose DNS names
7. require SPLUNK_PASSWORD, SPLUNK_HEC_TOKEN, SPLUNK_SEARCH_PASSWORD, NEW_RELIC_LICENSE_KEY, NEW_RELIC_API_KEY, NEW_RELIC_ACCOUNT_ID
8. require NEW_RELIC_MCP_USER_KEY for the bundled MCP server
9. for azure_openai require AZURE_OPENAI_API_KEY/BASE_URL/MODEL
10. for openai require OPENAI_API_KEY/MODEL
11. print only variable names and PASS/FAIL; never values
```

- [ ] **Step 4: Implement `verify_proxy_ca.sh`**

Use safe checks only:

```sh
#!/bin/sh
set -eu

for svc in api-gateway newrelic-mcp console-api; do
  docker compose -f docker-compose.yml -f compose.proxy.yml exec -T "$svc" \
    sh -lc 'test -s /usr/local/share/ca-certificates/zscaler-ca.crt && test -s /etc/ssl/certs/ca-certificates.crt'
done

echo "proxy-ca-check: PASS"
```

Do not echo proxy values.

- [ ] **Step 5: Implement `verify_stack.sh`**

Checks, in this order:

```text
- docker compose ps services are running/healthy
- Splunk management endpoint responds with admin credentials from environment without printing them
- Splunk HEC smoke event returns code=0
- New Relic MCP Streamable HTTP endpoint is reachable
- Console API `/openapi.json` returns 200
- Console UI `/` returns 200
```

Use `curl -fsS` and print only endpoint labels/status.

- [ ] **Step 6: Implement `bootstrap_splunk_readonly.sh`**

The script must source `.env` without echoing values, wait for Splunk health, create or update `poc_ai_search`, create or update `ai_search`, write the local `authorize.conf` stanza that disables inherited capabilities, reload auth, and verify the effective capability set is exactly `search`. It must also verify `index=_internal` returns zero accessible results while `index=poc_observability` is searchable.

The local stanza applied inside the Splunk container must contain:

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

- [ ] **Step 7: Add Make targets**

```make
COMPOSE_PROXY = docker compose -f docker-compose.yml -f compose.proxy.yml --profile splunk

proxy-preflight:
	./scripts/preflight_proxy.sh

proxy-up: proxy-preflight
	$(COMPOSE_PROXY) up -d --build

proxy-ps:
	$(COMPOSE_PROXY) ps

proxy-test:
	./scripts/verify_proxy_ca.sh
	./scripts/verify_stack.sh

proxy-down:
	$(COMPOSE_PROXY) down
```

Keep the original `up`, `down`, `logs`, and `test` targets for the non-proxy workflow.

- [ ] **Step 8: Run and verify GREEN**

```bash
python -m pytest tests/unit/test_proxy_scripts.py -v
```

Expected: PASS.

Also run:

```bash
sh -n scripts/preflight_proxy.sh scripts/verify_proxy_ca.sh scripts/verify_stack.sh scripts/bootstrap_splunk_readonly.sh
```

Expected: exit 0.

- [ ] **Step 9: Commit if Git is available**

```bash
git add scripts Makefile tests/unit/test_proxy_scripts.py
git commit -m "feat: add proxy deployment preflight and smoke checks"
```

---

### Task 6: Write the Fresh-Environment Setup Guide and Test Guide

**Files:**
- Create: `docs/SETUP_PROXY_ZSCALER.md`
- Create: `docs/TESTING_PROXY_ZSCALER.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: completed deployment workflow from Tasks 1-5 and existing Splunk/New Relic/Phase 4/Phase 5 tests.
- Produces: an operator can rebuild the PoC from zero without referring to prior chat history.

- [ ] **Step 1: Write the setup guide with this exact section order**

```text
1. Scope and prerequisites
2. Files to prepare
3. Create .env from .env.example
4. Configure Proxy and NO_PROXY
5. Place Zscaler CA
6. Configure New Relic credentials
7. Configure Splunk credentials
8. Configure LLM provider
9. Run preflight
10. Build and start the stack
11. Run `scripts/bootstrap_splunk_readonly.sh`
12. Verify poc_ai_search and ai_search
13. Verify Splunk effective capability is search only
14. Verify Demo Commerce health
15. Verify New Relic APM entities
16. Verify New Relic MCP / tools/list
17. Verify Console API and UI
18. Run provider-only LLM test
19. Run Phase 4 complete live E2E
20. Run Phase 5 live Console E2E
21. Browser verification
22. Stop/restart procedures
23. Troubleshooting
24. Security notes
```

The Splunk section must preserve:

```text
index=poc_observability
sourcetype=poc:app:json
role=poc_ai_search
user=ai_search
effective capability=search only
search time limit=7200 seconds
```

It must include the existing Splunk 10.4.3 `authorize.conf` explicit disables for:

```text
edit_own_objects
list_all_objects
run_collect
run_mcollect
schedule_rtsearch
```

- [ ] **Step 2: Write the test guide as a matrix**

Include these exact command classes and expected outcomes:

```text
Static/unit:
- Python unit suite -> 0 failed
- Python offline integration subset -> 0 failed
- Console Node tests -> 0 failed
- Console production build -> exit 0
- Vendored MCP unit tests -> 0 failed

Proxy/CA:
- make proxy-preflight -> PASS
- scripts/verify_proxy_ca.sh -> PASS
- external HTTPS TLS probe through proxy -> PASS
- internal service URLs use NO_PROXY

Splunk:
- HEC -> code 0
- ai_search current-context -> only search capability
- test_splunk_search_integration.py -> PASS

New Relic MCP:
- test_newrelic_mcp_transport_integration.py -> PASS

LLM:
- selected provider-only live test -> PASS

E2E:
- test_phase4_live_end_to_end.py -> PASS
- test_phase5_live_console.py -> PASS
- browser investigation -> all five progress steps finish
```

- [ ] **Step 3: Add a README entry point**

Add a short section linking operators to:

```text
docs/SETUP_PROXY_ZSCALER.md
docs/TESTING_PROXY_ZSCALER.md
```

State clearly that Docker daemon/base-image pull proxy configuration is a prerequisite and is not configured by this project.

- [ ] **Step 4: Documentation self-check**

Run:

```bash
grep -R -n -E 'TBD|TODO|FIXME' docs/SETUP_PROXY_ZSCALER.md docs/TESTING_PROXY_ZSCALER.md README.md
```

Expected: no unresolved placeholders in executable instructions. Examples may use obvious non-secret sample values only when labeled as examples.

- [ ] **Step 5: Commit if Git is available**

```bash
git add docs/SETUP_PROXY_ZSCALER.md docs/TESTING_PROXY_ZSCALER.md README.md
git commit -m "docs: add proxy zscaler setup and verification guide"
```

---

### Task 7: Full Regression and Live Deployment Verification

**Files:**
- No new production files unless a failing verification reveals a bug.
- Update tests only through a new RED -> GREEN cycle if a bug is found.

**Interfaces:**
- Consumes: completed proxy deployment implementation.
- Produces: fresh evidence that the package is buildable, testable, and deployable in the target Proxy + Zscaler environment.

- [ ] **Step 1: Run all Python unit tests in the required Python 3.11 container**

```bash
docker run --rm \
  -v "$PWD:/workspace" \
  -w /workspace \
  python:3.11-slim \
  sh -lc "python -m pip install -q -e '.[dev]' && python -m pytest tests/unit -q"
```

Expected: exit 0, 0 failures.

- [ ] **Step 2: Run Console tests and production build**

```bash
docker run --rm \
  -v "$PWD/console:/app" \
  -w /app \
  node:22-slim \
  sh -lc "npm test && npm run build"
```

Expected: exit 0, 0 test failures, Vite build completes.

- [ ] **Step 3: Run vendored MCP tests**

```bash
docker run --rm \
  -v "$PWD/vendor/newrelic-mcp-server:/workspace" \
  -w /workspace \
  python:3.12-slim \
  sh -lc "python -m pip install -q -e '.[dev]' && python -m pytest -q"
```

Expected: exit 0.

- [ ] **Step 4: Run the offline integration subset**

```bash
docker run --rm \
  -v "$PWD:/workspace" \
  -w /workspace \
  python:3.11-slim \
  sh -lc "python -m pip install -q -e '.[dev]' && python -m pytest -q \
    tests/integration/test_payment_flow.py \
    tests/integration/test_phase5_console_api.py"
```

Expected: exit 0, 0 failures. `test_demo_app.py`, `test_trace_context.py`, and `test_phase4_evidence_integration.py` require live services/Splunk and belong to the later stack/live verification steps.

- [ ] **Step 5: Run static Compose validation**

```bash
docker compose -f docker-compose.yml -f compose.proxy.yml --profile splunk config --quiet
docker compose -f docker-compose.yml -f compose.proxy.yml --profile splunk config --services
```

Expected: `config --quiet` exits 0 and all application, Splunk, MCP, Console API, and Console UI services are listed.

- [ ] **Step 6: On the Proxy + Zscaler target environment, run preflight and start**

```bash
make proxy-preflight
make proxy-up
make proxy-test
```

Expected: all commands exit 0.

- [ ] **Step 7: Verify Splunk read-only live search**

Run the existing `tests/integration/test_splunk_search_integration.py` in `python:3.11-slim`, passing the `.env` search credentials and using `SPLUNK_SEARCH_URL=https://localhost:8089` from the host-side test container if `--network host` is used.

Expected: PASS.

- [ ] **Step 8: Verify MCP live contract**

Run:

```bash
export NEW_RELIC_MCP_URL=http://localhost:8000/mcp
```

Then execute `tests/integration/test_newrelic_mcp_transport_integration.py` in `python:3.11-slim --network host`.

Expected: required tools and schemas match the existing contract.

- [ ] **Step 9: Verify selected LLM provider**

For Azure OpenAI:

```bash
export RUN_AZURE_OPENAI_LIVE_TESTS=1
```

Run `tests/integration/test_phase4_live_azure_openai_analysis.py` with the existing Azure variables from `.env`. For OpenAI or Ollama, use the corresponding provider-only test instead.

Expected: PASS without TLS/proxy errors.

- [ ] **Step 10: Verify Phase 4 complete E2E**

Run `tests/integration/test_phase4_live_end_to_end.py` using real Splunk, real New Relic MCP, and the selected real LLM provider.

Expected: `IncidentAnalysis` passes Pydantic and evidence/query/contradiction reference validation.

- [ ] **Step 11: Verify Phase 5 Console E2E**

Set:

```bash
export RUN_PHASE5_LIVE_TESTS=1
```

Run `tests/integration/test_phase5_live_console.py`.

Expected: PASS with AG-UI progress and A2UI result events.

- [ ] **Step 12: Browser verification**

Open:

```text
http://TARGET_VM_IP_OR_DNS:5173
```

Submit a known request ID and a valid <=2 hour time window. Verify all five steps finish:

```text
Correlation
Splunk investigation
New Relic investigation
Evidence correlation
LLM analysis
```

Verify the Japanese Evidence-first analysis renders and the UI remains read-only.

- [ ] **Step 13: Security verification**

Check service logs for accidental secrets by searching only for variable names/prefixes, not values:

```bash
docker compose -f docker-compose.yml -f compose.proxy.yml logs --no-color > /tmp/poc-proxy-logs.txt
python - <<'PY'
from pathlib import Path
text = Path('/tmp/poc-proxy-logs.txt').read_text(errors='replace')
for marker in ['AZURE_OPENAI_API_KEY=', 'OPENAI_API_KEY=', 'NEW_RELIC_LICENSE_KEY=', 'SPLUNK_HEC_TOKEN=', 'SPLUNK_SEARCH_PASSWORD=']:
    assert marker not in text, marker
print('secret-marker-check: PASS')
PY
rm -f /tmp/poc-proxy-logs.txt
```

Expected: PASS.

---

### Task 8: Package the Final Distribution

**Files:**
- Create outside project: `observability-ai-poc-proxy-zscaler-v12.0.zip`
- Create outside project: `newrelic_splunk_a2ui_agui_poc_handoff_20260904_v12_0.md`

**Interfaces:**
- Consumes: verified project tree from Tasks 1-7.
- Produces: clean ZIP with `observability-ai-poc/` as the top-level directory and no secrets/real CA/cache/build artifacts.

- [ ] **Step 1: Remove generated/private files from the packaging copy**

Exclude at minimum:

```text
.env
certs/zscaler-ca.crt
.venv/
node_modules/
dist/
__pycache__/
.pytest_cache/
*.pyc
*.log
```

- [ ] **Step 2: Create the ZIP with the project parent directory**

From the packaging parent:

```bash
zip -qr observability-ai-poc-proxy-zscaler-v12.0.zip observability-ai-poc \
  -x '*/.env' '*/certs/zscaler-ca.crt' '*/.venv/*' '*/node_modules/*' '*/dist/*' '*/__pycache__/*' '*/.pytest_cache/*' '*.pyc' '*.log'
```

- [ ] **Step 3: Inspect ZIP contents for forbidden files**

```bash
unzip -l observability-ai-poc-proxy-zscaler-v12.0.zip > /tmp/proxy-zip-list.txt
! grep -E '(^|/)(\.env|zscaler-ca\.crt)$|node_modules/|__pycache__/|\.pytest_cache/' /tmp/proxy-zip-list.txt
```

Expected: exit 0.

- [ ] **Step 4: Verify key deployment artifacts are present**

```bash
for item in \
  observability-ai-poc/compose.proxy.yml \
  observability-ai-poc/.env.example \
  observability-ai-poc/certs/README.md \
  observability-ai-poc/docs/SETUP_PROXY_ZSCALER.md \
  observability-ai-poc/docs/TESTING_PROXY_ZSCALER.md \
  observability-ai-poc/vendor/newrelic-mcp-server/pyproject.toml; do
  grep -F "$item" /tmp/proxy-zip-list.txt >/dev/null
done
```

Expected: exit 0.

- [ ] **Step 5: Write the v12.0 handoff**

The handoff must record:

```text
- v11.4 Phase 5 complete baseline
- Proxy/Zscaler architecture
- exact NO_PROXY ranges and service names
- CA placement path
- host/daemon proxy out-of-scope prerequisite
- vendored New Relic MCP source/version
- Splunk read-only setup
- Make targets
- full test commands and observed results
- any target-environment-only verification that remains pending
```

Do not include secrets, proxy credentials, account-private URLs containing credentials, or the real CA.

- [ ] **Step 6: Final verification-before-completion gate**

Re-run the static/unit/build checks from Task 7 after the final packaging copy is created. Only report completion if every runnable check exits 0; clearly label live Proxy/Zscaler checks as pending if they cannot be executed in the current environment.

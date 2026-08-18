# A2UI / AG-UI 調査・比較資料

> 作成日: 2026-08-18  
> 対象: A2UI / AG-UI 公式ドキュメント  
> 目的: 技術調査・PoC報告、および後続のPowerPoint資料化のためのベース資料

---

# 1. エグゼクティブサマリー

## 1.1 結論

A2UIとAG-UIは、名称が似ているものの役割が異なる。

- **A2UI (Agent to UI)** は、AIエージェントが「どのUIを表示するか」を宣言的なデータとして表現するための **Generative UI / UI記述プロトコル**。
- **AG-UI (Agent User Interaction Protocol)** は、フロントエンドとAIエージェントの間で、メッセージ、状態、ツール呼び出し、実行状態などをリアルタイムに交換するための **双方向・イベントベース通信プロトコル**。
- したがって両者は競合ではなく、**AG-UIを通信路（pipe / runtime connection）、A2UIをUI内容（payload / UI format）として併用できる**。

### 一言で表すと

| 技術 | 一言で表現 | 主な責務 |
|---|---|---|
| A2UI | Agentが生成する「画面の設計図」 | UI構造、データバインディング、Action、Catalog、Renderer連携 |
| AG-UI | AgentとFrontendをつなぐ「イベント通信路」 | Streaming、State、Messages、Tools、Interrupts、Lifecycle |

---

# 2. 全体アーキテクチャ

## 2.1 A2UI単体の基本イメージ

```text
User
  ↓
Frontend / Host App
  ↓ request
Agent / LLM
  ↓
A2UI JSON / JSONL
  ↓
Transport
  ↓
A2UI Renderer
  ↓
Native UI
(Card / Button / Form / List / etc.)
```

A2UIでは、AgentはHTMLやJavaScriptを直接送るのではなく、許可されたコンポーネントを使った宣言的なUIデータを生成する。クライアント側Rendererが、そのデータをReact / Angular / Lit / Flutterなどのネイティブコンポーネントへ変換する。

## 2.2 AG-UI単体の基本イメージ

```text
User
  ↓
Frontend / AG-UI Client
  ↓ RunAgentInput
AG-UI Endpoint / Agent
  ↑↓
Event Stream
  ├─ RUN_STARTED
  ├─ TEXT_MESSAGE_CONTENT
  ├─ TOOL_CALL_*
  ├─ STATE_*
  ├─ CUSTOM
  └─ RUN_FINISHED
```

AG-UIでは、Agent実行を単発のRESTレスポンスとして扱うのではなく、Agentの実行中に発生する情報をイベントストリームとしてFrontendへ渡す。

## 2.3 A2UI + AG-UI 統合イメージ

```text
┌──────────────────────────┐
│ Frontend / Host App      │
│                          │
│ AG-UI Client             │
│      ↓                   │
│ A2UI Renderer            │
└──────────┬───────────────┘
           │ AG-UI Events
           │ + A2UI payload
           │
┌──────────▼───────────────┐
│ Python Agent Backend     │
│                          │
│ ag-ui-protocol           │
│ a2ui-agent-sdk           │
│ LLM / Agent Framework    │
└──────────────────────────┘
```

この構成では、AG-UIがAgentとFrontendのセッション、イベント、状態同期を担当し、A2UIが「何を画面に描画するか」を担当する。

---

# 3. A2UI 公式ドキュメント整理

公式サイトの主要サイドバー構成に沿って整理する。

---

## 3.1 Introduction & FAQ

### What is A2UI?

A2UIは、Agent Driven UIのための宣言的UIプロトコル。

主な狙い:

- Textだけでは操作しにくいAgent UXを、フォームやカードなどのUIへ拡張する。
- Agentから任意JavaScriptを実行させず、安全なデータとしてUIを受け渡す。
- Agent側とFrontend側を特定フレームワークへ固定しない。
- UI構造とデータをストリーミングで段階的に生成する。

重要な設計思想:

- **Declarative**: UIをコードではなくデータとして記述する。
- **LLM-Friendly**: LLMが生成しやすいフラットな構造を採用する。
- **Framework-Agnostic**: React、Angular、Lit、Flutter等でRendererを実装可能。
- **Separation of Concerns**: UI構造、アプリケーション状態、描画を分離する。

A2UIが対象外としているもの:

- 静的Webサイトの代替
- HTMLそのものの代替
- 完全なCSS/デザイン言語
- Web専用仕様

### Who is it For?

主な利用者は3種類に分かれる。

1. **Host App Developers / Frontend Developers**
   - Agentが生成したUIを自社アプリへ表示したい。
   - 自社のDesign Systemやブランドを維持したい。
   - 複数Agentや外部Agentを安全に受け入れたい。

2. **Agent Developers / Backend Developers**
   - Agentにフォーム、ダッシュボード、リストなどを生成させたい。
   - Text応答だけではなく、操作可能なUIをAgent出力にしたい。

3. **Platform / SDK Builders**
   - Agent基盤やマルチAgentプラットフォームへGenerative UIを組み込みたい。
   - 複数のFrontend / Agent間で共通UI表現を使いたい。

### How Can I Use It?

大きく3つの導入方法がある。

- **FrontendにRendererを導入する**
  - React / Angular / Lit / Flutter等。
- **AgentからA2UIを生成する**
  - Python、Node.js、Agent FrameworkなどからJSON/JSONLを生成。
- **既存Framework経由で利用する**
  - AG-UIなど、A2UIを運べる既存のAgent通信基盤を利用する。

### How Does A2UI Compare?

A2UI公式ではAG-UIとの関係を明確に分けている。

- A2UI: UIフォーマット / Generative UI specification
- AG-UI: Agent backendとFrontendを接続するinteraction / transport protocol

特に重要なのは、**A2UI + AG-UIは併用前提の構成になり得る**という点。

---

## 3.2 Quickstart

公式Quickstartでは、Agentが動的UIを生成し、それをRendererで表示する一連の流れを体験できる。

確認ポイント:

- AgentがA2UIメッセージを生成する。
- UIを一括で返すのではなく、段階的に更新できる。
- RendererがA2UIを実際のUIコンポーネントへ変換する。
- ユーザー操作をAgent側へ返し、次のUI更新につなげられる。

調査・PoCではQuickstartそのものを再現する必要はなく、以下を確認できれば十分。

1. A2UI JSONを生成できる。
2. Schema validationできる。
3. RendererでCard / Text / Button等を描画できる。
4. ActionをAgent側へ返せる。

---

## 3.3 A2UI Composer

A2UI JSONを視覚的に作成・確認するためのツール。

用途:

- A2UIの構造理解
- Promptへ入れるサンプルUI作成
- Component / Catalogの動作確認
- Agentへ生成させたいUIのプロトタイプ作成

PoC時は、LLMにいきなりUIを生成させるより、Composerで期待するA2UIを作り、そのJSONを正解例としてPromptへ与えると検証しやすい。

---

# 4. A2UI Concepts

## 4.1 Overview

A2UIの中心概念は以下。

- Streaming Messages
- Declarative Components
- Data Binding
- Surface
- Catalog
- Actions
- Transport
- Renderer

A2UIは単なるJSON Schemaではなく、**UIのライフサイクルをストリームとして扱うプロトコル**と理解するのが重要。

---

## 4.2 Glossary

重要用語:

| 用語 | 意味 |
|---|---|
| Surface | UIを描画する論理的な領域。画面、パネル、ダイアログなど |
| Component | Text、Button、Card、InputなどのUI要素 |
| Data Model | Surfaceに紐づくアプリケーション状態 |
| Catalog | Agentが使用可能なComponent定義の集合 |
| Renderer | A2UIを実UIへ変換するFrontend側実装 |
| Action | Button clickなど、UIから発生する操作 |
| Transport | AgentとClient間でA2UIメッセージを運ぶ通信手段 |

---

## 4.3 Data Flow

基本的なデータフロー:

```text
Agent / LLM
   ↓
A2UI Generator
   ↓
Transport
   ↓
Stream Reader
   ↓
Message Parser
   ↓
Renderer
   ↓
Native UI
```

特徴:

- JSONオブジェクトを連続的に送信可能。
- JSONLのようなストリーム形式と相性が良い。
- UIを一度に完成させる必要がない。
- ComponentやData Modelを後から部分更新できる。

v0.9系では代表的に以下のメッセージを利用する。

- `createSurface`
- `updateComponents`
- `updateDataModel`
- `deleteSurface`

---

## 4.4 Components & Structure

A2UIの特徴的な設計が **Adjacency List Model**。

通常のUI JSONでは子要素をネストするが、A2UIではComponentをフラットな配列として管理し、ID参照で親子関係を表現する。

```text
root
 ├─ title
 └─ actions
      ├─ cancel
      └─ ok
```

概念的には以下のようなデータになる。

```json
[
  {"id": "root", "children": ["title", "actions"]},
  {"id": "title", "component": "Text"},
  {"id": "actions", "children": ["cancel", "ok"]},
  {"id": "cancel", "component": "Button"},
  {"id": "ok", "component": "Button"}
]
```

メリット:

- LLMが深いJSON nestingを生成しなくてよい。
- Component単位で差し替え可能。
- Streamingで後からComponentを追加しやすい。
- Agentが途中で生成ミスを修正しやすい。

---

## 4.5 Data Binding

UI構造と状態を分離し、JSON Pointer形式のpathでデータを参照する。

```text
Component
  text → /server/status

Data Model
  /server/status = "Warning"
```

これにより、Component自体を再生成せずにData Modelだけを変更できる。

利点:

- Reactive update
- 大量データの効率的更新
- Form入力値との連携
- Dynamic List
- UIテンプレートの再利用

---

## 4.6 Catalogs

Catalogは、Agentが利用可能なUIコンポーネントのホワイトリスト / 定義集合。

例:

```text
Basic Catalog
 ├─ Text
 ├─ Button
 ├─ Card
 ├─ Row
 ├─ Column
 ├─ TextField
 └─ DateTimeInput
```

重要な意味:

- Agentへ任意UIコードを生成させない。
- Host Appが許可したコンポーネントだけを使用させる。
- 自社Design Systemへマッピングできる。
- Security boundaryとして機能する。

A2UIのセキュリティ設計においてCatalogは非常に重要。

---

## 4.7 Transports

A2UIはTransport Agnostic。

つまり、A2UI自体は「JSONをどうネットワークで送るか」を固定しない。

候補:

- AG-UI
- A2A
- SSE
- WebSocket
- 独自Transport

A2UI公式ドキュメントでは、AG-UIをA2UIメッセージを運ぶ代表的なTransport / runtimeとして扱っている。

---

## 4.8 Actions

Actionは、ユーザー操作をAgentへ返す仕組み。

例:

```text
Agent
 ↓
A2UI Button
 ↓
User Click
 ↓
Action Event
 ↓
Agent
 ↓
UI / State Update
```

主な考慮点:

- Local functionとしてFrontendだけで処理するAction
- Agentへ送信するEvent
- Form Submission
- Data Model Sync
- Validation Error
- Security / Surface Ownership

A2UIを単なる表示仕様ではなく、**双方向のAgent UI**として成立させる部分。

---

# 5. A2UI Guides

## 5.1 Client Setup

Frontend側へA2UI Rendererを組み込むためのガイド。

検討事項:

- 使用Renderer
- AgentとのTransport
- Surface管理
- Action送信
- Theme / Styling
- Component Catalog

---

## 5.2 Agent Development

Python SDKを使う場合に特に重要なページ。

Agent側の基本手順:

1. User Intentを理解する。
2. 表示するUIを決める。
3. LLMにA2UI JSONを生成させる。
4. Schema validationする。
5. Clientへstreamする。
6. Actionへ応答する。

Pythonでは `a2ui-agent-sdk` が利用でき、`A2uiSchemaManager` とCatalog定義を使って、LLM用System PromptへA2UI SchemaやExampleを組み込める。

```python
from a2ui.strategies.schema import A2uiSchemaManager
from a2ui.basic_catalog.provider import BasicCatalog
```

PoCで確認すべき点:

- Prompt生成
- JSON生成
- Schema validation
- JSONL / Streaming
- Action handling

---

## 5.3 Use A2UI with Any Agent Framework & Harness

A2UIを既存Agent Frameworkへ追加する方法。

代表的な考え方:

```text
Existing Agent Framework
        ↓
      AG-UI
        ↓
  A2UI payload
        ↓
A2UI Renderer
```

AG-UI対応Agent Frameworkを使っている場合、Agent Framework固有通信を一から作らず、AG-UI runtime経由でA2UIをClientへ流せる。

---

## 5.4 Renderer Development

独自Rendererを作るためのガイド。

必要になるケース:

- 社内UI Frameworkへ組み込みたい。
- 標準RendererがないFrontend Frameworkを使う。
- 独自Componentを大量に利用する。

Rendererの責務:

- A2UI message解析
- Surface管理
- Component ID解決
- Data Binding
- Action生成
- Native Componentへのmapping

---

## 5.5 Defining Your Own Catalog

独自UIコンポーネント集合を定義する。

利用例:

```text
Security Dashboard Catalog
 ├─ SeverityBadge
 ├─ IncidentCard
 ├─ IPAddressTable
 ├─ Timeline
 ├─ ThreatScore
 └─ BlockButton
```

セキュリティ監視画面など、自社固有UIでは有効。

---

## 5.6 Authoring Custom Components

Basic Catalogに存在しないComponentを追加するための仕組み。

検討事項:

- Component Schema
- Renderer実装
- Catalog登録
- LLM PromptへのComponent仕様提供
- Input / Action設計

---

## 5.7 Theming & Styling

A2UIでは基本的にHost App側が描画・見た目を制御する。

つまりAgentは「Primary Buttonを表示したい」ことは指示できても、任意CSSを送り込む設計ではない。

メリット:

- ブランド統一
- Dark Mode対応
- Accessibility統一
- 任意コード実行の抑制

---

## 5.8 A2UI + MCP

MCPとの併用方法。

A2UIとMCPは責務が異なる。

```text
MCP  = Tool / Resource / Context
A2UI = Generative UI
```

MCP経由でA2UI resourceを渡す構成や、MCP AppsとA2UIを組み合わせる構成が検討できる。

---

# 6. A2UI Reference / Specifications / Ecosystem

## 6.1 Component Gallery

標準Componentの確認用。

PoCでは、まず以下を確認すれば十分。

- Text
- Button
- Card
- Row / Column
- Input
- List系

---

## 6.2 Message Reference

A2UI messageの正式なfield、型、制約を確認するためのReference。

実装フェーズではConceptsよりもこちらが重要になる。

---

## 6.3 Renderers (Clients)

利用可能なRenderer / Client実装を確認するページ。

PoCでは独自Rendererから始めず、既存Rendererを利用する方が短時間で評価できる。

---

## 6.4 Agents (Server-side)

A2UIを生成するAgent側の実装・SDKに関する情報。

Python PoCではこの領域が中心になる。

---

## 6.5 Specifications

2026-08-18時点の公式サイトでは、主に以下が掲載されている。

| Version | Status | 特徴 |
|---|---|---|
| v1.0 | Candidate | 新しいAction/RPC関連などを含む候補版 |
| v0.9.1 | Current | 現行production release |
| v0.9 | Previous Stable | Prompt-firstの方向性やCatalog等を導入 |
| v0.8 | Legacy | 初期のSurface / Data Bindingモデル |

今回の短期PoCでは、**v0.9.1を基準に調査し、v1.0との差分も確認する**のが安全。

---

## 6.6 Ecosystem / Roadmap

A2UIはRenderer、Agent SDK、AG-UI、A2A、MCPなど周辺ecosystemとの連携を拡大中。

PoC評価では、仕様の完成度だけでなく以下も確認する。

- Rendererの成熟度
- Python SDKの成熟度
- Framework Integration
- Breaking Changeの可能性
- v1.0 Candidateから正式版への移行影響

---

# 7. AG-UI 公式ドキュメント整理

---

## 7.1 Get Started - AG-UI Overview

AG-UIは、AI Agentとユーザー向けApplicationをつなぐ、open / lightweight / event-basedな双方向プロトコル。

通常のREST APIとの違い:

```text
REST
Client → Request → Server → Response → End

AG-UI
Client → Run
         ↓
       Events
         ↓
  Text / State / Tool / Progress / Interrupt
         ↓
       Finished
```

Agentic Applicationでは以下が必要になるため、単純なrequest/responseだけでは扱いにくい。

- 長時間実行
- Streaming
- Tool Call
- State同期
- Human-in-the-loop
- Multi-step processing
- Structured + unstructured output

---

## 7.2 MCP, A2A, and AG-UI

Agent protocolを3レイヤーで整理できる。

| 接続対象 | Protocol | 役割 |
|---|---|---|
| Agent ↔ Tools / Data | MCP | Tool、Resource、Context |
| Agent ↔ Agent | A2A | Agent間通信 |
| Agent ↔ User-facing App | AG-UI | AgentとFrontend間のInteraction |

このためMCP、A2A、AG-UIは排他的ではなく、1つのAgent Systemで同時利用可能。

---

# 8. AG-UI Concepts

## 8.1 Core Architecture

AG-UIはevent-driven architectureを採用。

主な構成要素:

```text
Application
   ↓
AG-UI Client
   ↓
Transport
   ↓
Agent Endpoint
   ↓
Agent / LLM / Framework
```

主な特徴:

- Bidirectional interaction
- Typed event stream
- Transport agnostic
- Middleware対応
- Agent Framework非依存

AG-UIの基本抽象は、概念的には以下。

```text
run(input: RunAgentInput)
       ↓
stream<BaseEvent>
```

---

## 8.2 Events

AG-UIの最重要概念。

イベントは用途別に分類される。

### Lifecycle

- `RUN_STARTED`
- `RUN_FINISHED`
- `RUN_ERROR`
- `STEP_STARTED`
- `STEP_FINISHED`

### Text Message

- `TEXT_MESSAGE_START`
- `TEXT_MESSAGE_CONTENT`
- `TEXT_MESSAGE_END`

### Tool Call

- `TOOL_CALL_START`
- `TOOL_CALL_ARGS`
- `TOOL_CALL_END`
- Tool Result系

### State

- `STATE_SNAPSHOT`
- `STATE_DELTA`
- `MESSAGES_SNAPSHOT`

### Other

- Activity
- Reasoning
- `RAW`
- `CUSTOM`

FrontendはEvent typeを見て、表示や状態更新を行う。

---

## 8.3 Agents

AG-UIのAgentは、Frontendから見た統一的なAgent interface。

主な役割:

- Message / Contextを受け取る。
- Agent処理を実行する。
- Event Streamを返す。
- Stateを管理する。
- Toolを実行する。

内部実装は以下のどれでもよい。

- LLM直接呼び出し
- LangGraph等のAgent Framework
- RAG
- Custom Workflow
- Multi-Agent System

---

## 8.4 Middleware

Agent Event Streamの途中に処理を挟む仕組み。

用途:

- Event変換
- Filtering
- Metadata付与
- Authentication
- Logging
- Metrics
- Rate limiting
- Error handling

既存Agent FrameworkをAG-UI化するときにも重要。

---

## 8.5 Messages

Conversation Historyを表す標準データ構造。

代表的なRole:

- user
- assistant
- system
- developer
- tool
- reasoning

Vendor Neutralな形式を狙っており、Frontendが特定LLM providerへ密結合しにくい。

---

## 8.6 Reasoning

LLMのreasoningに関連するイベントや状態継続を扱う領域。

ポイント:

- Reasoning用Message / Event
- Visibility制御
- Encrypted contentによるcontinuity
- Privacy / compliance

実システムでは、モデル内部の生の思考過程をそのまま表示するという意味ではなく、ユーザー向けに提示可能なreasoning / progress情報をどのように扱うかという観点が重要。

---

## 8.7 State Management

FrontendとAgent間でStateを同期する仕組み。

```text
Agent State
   ↑↓
AG-UI State Events
   ↑↓
Frontend State
```

代表的な方式:

- Snapshot: 状態全体
- Delta: 差分更新

用途:

- Agentと画面で同じ作業状態を共有
- Human-in-the-loop
- Dashboard更新
- Form / workflow state

A2UIのData Modelと似て見えるが、A2UIは「UI Surfaceに紐づく表示データ」の概念が中心なのに対し、AG-UI Stateは「Agent Application全体の共有状態」の役割が強い。

---

## 8.8 Interrupts

Agent処理を途中で停止し、人間の承認や追加入力を待つ仕組み。

利用例:

```text
Agent
 ↓
"サーバを停止してよいか？"
 ↓
Interrupt
 ↓
User Approve / Reject
 ↓
Resume
 ↓
Agent continues
```

用途:

- 承認フロー
- 重要操作
- セキュリティ操作
- Structured Input
- Policy Check

---

## 8.9 Serialization

Agentとのevent streamを保存・復元する仕組み。

用途:

- Browser reload後の復元
- Session reconnect
- Conversation history保存
- Event compaction
- Branch / Time travel

Agentic applicationをproduction化する際に重要。

---

## 8.10 Tools

Agentから利用するToolを標準化する。

AG-UIでは特に、**Frontend-defined Tool** をAgentへ渡せる点が重要。

例:

```text
Frontend Tool
  openDialog()
  selectServer()
  requestApproval()
      ↑
      │ AG-UI
      │
    Agent
```

これにより、AgentがFrontend固有の処理をToolとして呼び出せる。

---

## 8.11 Capabilities

Agentが何をサポートしているかをClient側が動的に確認する仕組み。

例:

- Tools対応
- Reasoning対応
- State対応
- Multimodal対応
- Human-in-the-loop対応

Client側はCapabilityを見てUIや機能を切り替えられる。

---

## 8.12 Generative UI

AG-UI自身はGenerative UI仕様ではない。

AG-UI公式ではA2UI、Open JSON UI、MCP-UI等をGenerative UI specificationとして扱い、AG-UIはそれらを運ぶInteraction Protocolという位置付け。

この区別はA2UIとの比較で最重要ポイント。

---

# 9. AG-UI Draft Proposals

## 9.1 Overview

まだ正式仕様へ確定していないProposal群。

例:

- Reasoning
- Interrupt-aware lifecycle
- Generative User Interfaces
- Meta Events

採用評価時は、StableなConceptとDraftを混同しないことが重要。

## 9.2 Generative User Interfaces

AG-UI自身でもGenerative UI関連のproposalがあるが、現状ではA2UI等のGenerative UI specificationと組み合わせる考え方が分かりやすい。

## 9.3 Meta Events

Agent Runそのものとは独立したAnnotation / Signal等を扱うためのProposal。

---

# 10. AG-UI Tutorials / Development

## 10.1 Developing with Cursor

AG-UI実装をcoding assistantと進めるための開発支援情報。

## 10.2 Debugging

Event Stream、Agent Endpoint、Client integration等のトラブルシュート。

PoCでは以下の観測が重要。

- 実際にどのEventが流れたか
- Event順序
- threadId / runId
- Tool Call payload
- State delta
- Error event

## 10.3 What's New / Roadmap

AG-UIは活発に更新されているため、本番採用時はversion / breaking change / draft statusを継続確認する必要がある。

---

# 11. AG-UI Python SDK

Pythonでは以下を中心に利用する。

```bash
pip install ag-ui-protocol
```

主な型:

- `RunAgentInput`
- `Message`
- `Context`
- `Tool`
- `State`

主なEvent:

- Lifecycle Events
- Text Message Events
- Tool Call Events
- State Management Events
- Special Events

Python + FastAPIで実装する場合の概念:

```text
POST /agent
   ↓
RunAgentInput
   ↓
Agent execution
   ↓
EventEncoder
   ↓
StreamingResponse
   ↓
AG-UI Client
```

今回のPoCでは、この方式が最もProtocolそのものを理解しやすい。

---

# 12. A2UI vs AG-UI 詳細比較

| 観点 | A2UI | AG-UI |
|---|---|---|
| 正式名称 | Agent to UI | Agent User Interaction Protocol |
| 主目的 | AgentがUIを宣言する | AgentとFrontendを接続する |
| レイヤー | Generative UI / Presentation | Interaction / Communication |
| 主なデータ | Component、Surface、Data Model、Action | Event、Message、State、Tool、Lifecycle |
| Streaming | 対応 | 中核機能 |
| 双方向 | Action等で対応 | 中核機能 |
| UI描画仕様 | あり | 原則なし |
| Renderer | 必要 | 特定Rendererを要求しない |
| State | Surface Data Model | Agent / App Shared State |
| Tool Call | 主目的ではない | 標準機能 |
| Human in the Loop | Actionで実現可能 | Interruptとして明示的に対応 |
| Transport | Transport Agnostic | Event transportを標準化 |
| Frontend Framework | Renderer次第 | Framework Agnostic |
| Python Backend | A2UI Agent SDK | AG-UI Python SDK |
| Securityの中心 | Declarative UI + Catalog | Client/backend接続・event contract |
| 主なユースケース | Form、Card、Dashboard、Generated UI | Chat streaming、Tool、State、Agent UX全般 |

---

# 13. 「似ている部分」と「違う部分」

## 13.1 似ている部分

- AgentとFrontendの連携を目的としている。
- Streamingを前提に設計されている。
- Frameworkへの依存を減らそうとしている。
- JSON / typed dataを中心に扱う。
- Interactive Agent Applicationを想定している。

## 13.2 明確に違う部分

### A2UI

```text
"画面に何を表示するか"
```

を標準化する。

### AG-UI

```text
"Agentと画面の間で何が起きたかをどう伝えるか"
```

を標準化する。

この違いが最も重要。

---

# 14. REST / AG-UI / A2UI の違い

| 方式 | 得意なこと | 不得意なこと |
|---|---|---|
| REST API | 単純なrequest/response | 長時間Agent、Streaming、State同期 |
| AG-UI | AgentとのリアルタイムInteraction | UIの具体的なComponent定義 |
| A2UI | Agent-generated UI | Agent session全体の通信管理 |

したがって、実システムでは以下の組み合わせが考えられる。

```text
REST + A2UI
AG-UI + A2UI
A2A + A2UI
AG-UI + Custom UI
```

---

# 15. Python SDK観点での比較

## 15.1 A2UI Python側

主な責務:

- Schema管理
- Catalog管理
- Prompt生成
- A2UI output validation
- A2UI message生成

代表Package:

```text
a2ui-agent-sdk
```

## 15.2 AG-UI Python側

主な責務:

- RunAgentInput受信
- Event生成
- Event encoding
- Text streaming
- Tool event
- State event
- Lifecycle管理

代表Package:

```text
ag-ui-protocol
```

## 15.3 両方使う場合

```text
FastAPI
  │
  ├─ ag-ui-protocol
  │      └─ Agent Event Stream
  │
  ├─ a2ui-agent-sdk
  │      └─ UI Schema / Validation
  │
  └─ LLM / Agent Framework
```

---

# 16. PoCで検証すべき項目

## 16.1 A2UI

- [ ] A2UI SchemaをPromptへ組み込めるか
- [ ] LLMからvalidなA2UIを生成できるか
- [ ] Validation Errorを検出できるか
- [ ] Surfaceを生成できるか
- [ ] Componentを追加・更新できるか
- [ ] Data Modelだけ更新できるか
- [ ] Button ActionをAgentへ返せるか
- [ ] Rendererで想定通り描画できるか
- [ ] Custom Catalogを作成できるか

## 16.2 AG-UI

- [ ] RunAgentInputを受信できるか
- [ ] RUN_STARTED / FINISHEDを送れるか
- [ ] Textをstreamingできるか
- [ ] Tool Call eventを扱えるか
- [ ] STATE_SNAPSHOT / DELTAを扱えるか
- [ ] CUSTOM Eventを扱えるか
- [ ] Error eventをFrontendへ通知できるか
- [ ] Interrupt / Resumeを検証できるか

## 16.3 A2UI + AG-UI

- [ ] AG-UI上でA2UI payloadを運べるか
- [ ] A2UI Rendererへeventを渡せるか
- [ ] Agent → UI progressive renderingができるか
- [ ] Button Action → Agent → UI updateの往復ができるか
- [ ] Shared StateとA2UI Data Modelをどう分担するか整理できるか

---

# 17. メリット・デメリット

## 17.1 A2UI

### メリット

- Agent Generated UIに特化。
- 任意コード実行を避けやすい。
- CatalogでUIを制限できる。
- 自社Design Systemを維持できる。
- Cross-platformを狙える。
- LLMが生成しやすい設計。

### 注意点

- Rendererが必要。
- UI SchemaをLLMへ理解させる必要がある。
- 複雑なUIではPrompt / Catalogが大きくなる。
- Version evolutionが進行中。

## 17.2 AG-UI

### メリット

- Agent ↔ Frontend通信を統一できる。
- Streamingに強い。
- Tool / State / Interruptを標準化できる。
- Agent Frameworkを交換しやすい。
- Custom Eventで拡張可能。

### 注意点

- UI Component仕様そのものは別途必要。
- Event-driven architectureへの理解が必要。
- Frontend / Backend双方でevent handlingが必要。
- Draft機能とStable機能を区別する必要がある。

---

# 18. 採用判断

## A2UIを単体で検討すべきケース

- AgentにForm / Card / Dashboardを生成させたい。
- 複数のFrontendで同じAgent UIを再利用したい。
- 外部Agentに任意HTML / JavaScriptを実行させたくない。
- 自社Design Systemを保持したい。

## AG-UIを単体で検討すべきケース

- Agent ChatをStreamingしたい。
- AgentとFrontendでState共有したい。
- Tool Call / Human Approvalを統一したい。
- Agent FrameworkをFrontendから隠蔽したい。

## A2UI + AG-UIを検討すべきケース

- Agentとのリアルタイム対話に加えて、Agent自身にUIを生成させたい。
- Chat + Form + Dashboard + Actionを同じAgent体験へ統合したい。
- Agent Frameworkを交換可能にしつつ、Frontend UIも疎結合にしたい。

---

# 19. 今回の検証タスクに対する推奨結論

8〜16時間の検証であれば、以下の順序が効率的。

```text
1. AG-UI Event Stream
        ↓
2. A2UI JSON生成
        ↓
3. A2UI Renderer
        ↓
4. AG-UI + A2UI統合
        ↓
5. Button Action往復
```

成果として最低限示すべきもの:

1. AG-UIでText Streamingできる。
2. PythonからA2UI JSONを生成できる。
3. A2UI RendererでUI描画できる。
4. AG-UI経由でA2UIを運べる。
5. 両技術は競合ではなく補完関係である。

---

# 20. PowerPoint化する場合のスライド構成案

このMarkdownをPowerPointへ変換する場合、以下の14〜16枚程度にまとめやすい。

| Slide | タイトル | 内容 |
|---|---|---|
| 1 | A2UI / AG-UI 調査・検証 | タイトル |
| 2 | 調査目的 | 背景、調査範囲 |
| 3 | 結論 | A2UIとAG-UIの一言比較 |
| 4 | Agentic Protocol全体像 | MCP / A2A / AG-UI / A2UI |
| 5 | A2UIとは | 目的、特徴 |
| 6 | A2UIの主要Concept | Surface / Component / Data Model / Catalog |
| 7 | A2UI Data Flow | Agent→Renderer |
| 8 | AG-UIとは | Event-driven architecture |
| 9 | AG-UI Events | Lifecycle / Text / Tool / State |
| 10 | AG-UIの主要Concept | State / Tool / Interrupt / Middleware |
| 11 | A2UI vs AG-UI | 比較表 |
| 12 | A2UI + AG-UI | 統合Architecture |
| 13 | Python SDK | 使用Package / 実装構成 |
| 14 | PoC内容 | 実施項目 |
| 15 | 評価 | メリット / 課題 |
| 16 | 結論・今後 | 採用可能性 / Next Step |

---

# 21. 報告用まとめ文

A2UIとAG-UIは、ともにAgentic ApplicationのFrontend連携に関連する技術であるが、対象とするレイヤーが異なる。A2UIはAgentが生成するUIを宣言的なComponent構造として表現するGenerative UI Protocolであり、AG-UIはAgentとFrontend間のMessage、State、Tool Call、Lifecycle等をイベントストリームとして交換するInteraction Protocolである。

A2UIはUIの構造とData Modelを分離し、Catalogで利用可能Componentを制限することで、Agentから任意コードを実行させずに動的UIを生成できる点が特徴である。一方AG-UIは、長時間実行されるAgent、Streaming、Tool Call、State同期、Human-in-the-loopといったAgentic Application固有の通信要件を標準化する。

そのため両者は競合技術ではなく、AG-UIをAgentとFrontend間の通信基盤として利用し、そのイベント上でA2UIのUI payloadを配送する構成が有効である。Python環境では、AG-UI側は `ag-ui-protocol`、A2UI側は `a2ui-agent-sdk` を利用することで、Agent / Backend部分のPoCを比較的少ない実装量で検証できる。

---

# 22. 参考資料

## A2UI Official

- A2UI Home: https://a2ui.org/
- What is A2UI?: https://a2ui.org/introduction/what-is-a2ui/
- Who is it For?: https://a2ui.org/introduction/who-is-it-for/
- How Can I Use It?: https://a2ui.org/introduction/how-to-use/
- A2UI Comparison: https://a2ui.org/introduction/agent-ui-ecosystem/
- Quickstart: https://a2ui.org/quickstart/
- Concepts Overview: https://a2ui.org/concepts/overview/
- Data Flow: https://a2ui.org/concepts/data-flow/
- Components: https://a2ui.org/concepts/components/
- Data Binding: https://a2ui.org/concepts/data-binding/
- Transports: https://a2ui.org/concepts/transports/
- Actions: https://a2ui.org/concepts/actions/
- Agent Development: https://a2ui.org/guides/agent-development/
- Any Agent Framework: https://a2ui.org/guides/a2ui-with-any-agent-framework/
- v1.0 Specification: https://a2ui.org/specification/v1.0-a2ui/

## AG-UI Official

- AG-UI Overview: https://docs.ag-ui.com/introduction
- MCP, A2A and AG-UI: https://docs.ag-ui.com/agentic-protocols
- Core Architecture: https://docs.ag-ui.com/concepts/architecture
- Events: https://docs.ag-ui.com/concepts/events
- Agents: https://docs.ag-ui.com/concepts/agents
- Middleware: https://docs.ag-ui.com/concepts/middleware
- Messages: https://docs.ag-ui.com/concepts/messages
- Reasoning: https://docs.ag-ui.com/concepts/reasoning
- State Management: https://docs.ag-ui.com/concepts/state
- Interrupts: https://docs.ag-ui.com/concepts/interrupts
- Serialization: https://docs.ag-ui.com/concepts/serialization
- Tools: https://docs.ag-ui.com/concepts/tools
- Capabilities: https://docs.ag-ui.com/concepts/capabilities
- Generative UI: https://docs.ag-ui.com/concepts/generative-ui-specs
- Python SDK: https://docs.ag-ui.com/sdk/python/core/overview

---

# 23. 調査時点に関する注意

本資料は **2026-08-18時点** の公式ドキュメントを基に整理している。

A2UI / AG-UIはいずれも更新が活発なプロジェクトであるため、本番採用時には以下を再確認すること。

- Current / Candidate / Draftの状態
- Python SDK version
- Breaking Changes
- Renderer対応状況
- Agent Framework Integration状況
- A2UI v1.0正式化状況


---

# Appendix A. A2UI サイドバー対応表

| Sidebar | Page / Topic | 本資料での要約 |
|---|---|---|
| Introduction & FAQ | What is A2UI? | 宣言的Generative UI Protocol、設計思想、安全性 |
| Introduction & FAQ | Who is it For? | Frontend / Agent / Platform Builderの3利用者 |
| Introduction & FAQ | How Can I Use It? | Renderer導入、Agent生成、Framework経由の3経路 |
| Introduction & FAQ | How Does A2UI Compare? | AG-UI、MCP Apps等との違い |
| Quickstart | Quickstart | Agent→A2UI→Renderer→Actionの基本体験 |
| A2UI Composer | Composer | A2UI JSONを視覚的に作成・確認 |
| A2UI Composer | Composer Integration | Composerで作ったUIをPrompt / Agent実装へ利用 |
| Concepts | Overview | Streaming / Components / Data Bindingの全体像 |
| Concepts | Glossary | Surface、Component、Catalog、Renderer等の用語 |
| Concepts | Data Flow | AgentからRendererまでのMessage Flow |
| Concepts | Components & Structure | Adjacency List、ID参照、incremental update |
| Concepts | Data Binding | JSON Pointer、Data Model、Reactive Update |
| Concepts | Catalogs | 使用可能Componentの定義・制限 |
| Concepts | Transports | AG-UI / A2A / SSE / WebSocket等 |
| Concepts | Actions | UI操作、Form、Agent Event、Data Model Sync |
| Guides | Client Setup | RendererをHost Appへ組み込む |
| Guides | Agent Development | AgentからA2UIを生成・validate・stream |
| Guides | Any Agent Framework & Harness | AG-UI等を経由して既存AgentへA2UI追加 |
| Guides | Renderer Development | 独自Rendererの責務と実装 |
| Guides | Defining Your Own Catalog | 自社Component Catalogを定義 |
| Guides | Authoring Custom Components | Basic Catalog外のComponent追加 |
| Guides | Theming & Styling | Host App主導のTheme / Styling |
| Guides / MCP | A2UI over MCP | MCP Resource等でA2UIを配送 |
| Guides / MCP | MCP Apps in A2UI | MCP AppsとA2UIの組み合わせ |
| Guides / MCP | A2UI in MCP Apps | MCP Apps側でA2UIを利用する方向性 |
| Reference | Component Gallery | 標準Componentの確認 |
| Reference | Message Reference | Message field / type / constraintの正式Reference |
| Reference | Renderers (Clients) | Client / Renderer実装一覧 |
| Reference | Agents (Server-side) | Agent側SDK / Implementation |
| Specifications | v1.0 Candidate | 次期仕様候補 |
| Specifications | v0.9.1 Current | 現行production release |
| Specifications | v0.9 Previous Stable | 前安定版 |
| Specifications | v0.8 Legacy | Legacy仕様 |
| Ecosystem | A2UI in the World | 採用例・連携先 |
| Ecosystem | Community Renderers | Community Renderer実装 |
| Ecosystem | Community | OSS Community情報 |
| Roadmap | Roadmap | 今後の仕様・SDK・Integration計画 |

---

# Appendix B. AG-UI サイドバー対応表

| Sidebar | Page / Topic | 本資料での要約 |
|---|---|---|
| Get Started | AG-UI Overview | Agent↔Frontendのevent-based interaction protocol |
| Get Started | MCP, A2A, and AG-UI | Agentic Protocol Stack上の位置付け |
| Get Started | Quickstart | AG-UI Application / Integration / Clientの開始経路 |
| Get Started | Production support | Production導入支援・実装時の確認領域 |
| Concepts | Core architecture | Client / Agent / Transport / Event Stream |
| Concepts | Events | Lifecycle / Text / Tool / State / Custom等 |
| Concepts | Agents | 統一Agent interfaceと実装方式 |
| Concepts | Middleware | Eventのtransform / filter / logging等 |
| Concepts | Messages | Vendor-neutral conversation model |
| Concepts | Reasoning | Reasoning visibility / continuity / privacy |
| Concepts | State Management | Snapshot / Deltaによるshared state |
| Concepts | Interrupts | Human-in-the-loop pause / resume |
| Concepts | Serialization | Event history保存・復元・branch |
| Concepts | Tools | Backend / Frontend-defined tools |
| Concepts | Capabilities | Agent機能のdynamic discovery |
| Concepts | Generative UI | A2UI等Generative UI仕様との関係 |
| Draft Proposals | Overview | Draft変更のstatus管理 |
| Draft Proposals | Generative User Interfaces | Generative UI拡張Proposal |
| Draft Proposals | Meta Events | Run外のannotation / signal Proposal |
| Tutorials | Developing with Cursor | Coding Assistantを用いた実装支援 |
| Tutorials | Debugging | Event Stream / endpointのdebug |
| Development | What's New | 更新情報 |
| Development | Roadmap | 今後の計画 |
| Development | Contributing | OSS contribution |
| Development | Support for AG-UI | Community / production support |
| Python SDK | ag_ui.core Overview | Python SDK全体像 |
| Python SDK | Types | RunAgentInput / Message / Context / Tool / State |
| Python SDK | Multimodal Inputs | multimodal input data |
| Python SDK | Events | Pythonのtyped Event classes |
| Python SDK | ag_ui.encoder | Event stream encoding |

---

# Appendix C. PowerPointへ落とす際の編集ルール

PowerPoint化する際は、本資料をそのまま全文貼り付けるのではなく、以下のルールで圧縮すると説明しやすい。

1. **1スライド1メッセージ**にする。
2. A2UI / AG-UIそれぞれのConceptは、一覧スライド + 重要Concept詳細スライドへ分ける。
3. 文章は3〜5 bulletへ圧縮する。
4. Architectureは表より図を優先する。
5. 比較表では「どちらが優れているか」ではなく「責務が違う」ことを強調する。
6. PoC結果には、実際のEvent JSON、A2UI JSON、Renderer画面のスクリーンショットを入れる。
7. 最終結論では、`AG-UI = interaction layer`、`A2UI = generative UI layer` を再掲する。


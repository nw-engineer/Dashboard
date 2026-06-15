import asyncio
import json
import os
from typing import Literal, Any

from agents import Agent, Runner, function_tool
from pydantic import BaseModel, Field


MODEL = os.getenv("OPENAI_MODEL", "gpt-5.5")


# =============================================================================
# デモの世界: ECサイトの注文処理
# =============================================================================
# これは安全なシミュレーションです。
# 実際のECサイト、顧客、在庫、配送システム、外部APIは変更しません。
WORLD = {
    "orders_waiting": 230,                 # 未処理注文数
    "avg_delay_minutes": 95,               # 平均出荷遅延
    "shipping_error_risk": 0.08,           # 誤出荷リスク
    "flash_sale_enabled": True,            # フラッシュセール中か
    "shipping_mode": "normal",             # normal / safe_review
    "customer_notice_published": False,    # お客様向け案内が公開済みか
    "internal_notes": [],
    "human_decisions": [],
}


TOOL_INFO = {
    "check_shop_status": {
        "label": "店舗状況を確認する",
        "approval": "不要",
        "what": "現在の未処理注文数、遅延、誤出荷リスク、セール状態、案内文公開状況を読み取ります。",
        "changes": "状態は変更しません。見るだけのツールです。",
        "risk": "なし",
    },
    "pause_flash_sale": {
        "label": "フラッシュセールを一時停止する",
        "approval": "必要",
        "what": "注文流入を抑えるため、実施中のフラッシュセールを一時停止します。",
        "changes": "新規注文の勢いが下がり、未処理注文数と平均遅延が改善する想定です。",
        "risk": "売上機会を一時的に減らす可能性があります。",
    },
    "switch_shipping_to_safe_mode": {
        "label": "配送を安全確認モードに切り替える",
        "approval": "必要",
        "what": "誤出荷を防ぐため、配送前の確認を強めるモードに切り替えます。",
        "changes": "誤出荷リスクが下がります。一方で、確認作業により少し遅くなる可能性があります。",
        "risk": "配送スピードがやや落ちる可能性があります。",
    },
    "publish_customer_notice": {
        "label": "お客様向け案内文を公開する",
        "approval": "必要",
        "what": "配送遅延について、お客様に見える案内文を公開します。",
        "changes": "顧客への説明責任を果たし、問い合わせ増加を抑える想定です。",
        "risk": "外部向け文面のため、表現やタイミングの確認が必要です。",
    },
    "write_internal_note": {
        "label": "社内メモを残す",
        "approval": "不要",
        "what": "対応内容や判断理由を社内向けに記録します。",
        "changes": "社内メモだけを追加します。顧客や外部システムには影響しません。",
        "risk": "なし",
    },
}


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def status_summary() -> str:
    sale = "ON" if WORLD["flash_sale_enabled"] else "OFF"
    notice = "公開済み" if WORLD["customer_notice_published"] else "未公開"
    mode = "通常" if WORLD["shipping_mode"] == "normal" else "安全確認モード"

    return (
        f"未処理注文数        : {WORLD['orders_waiting']} 件\n"
        f"平均出荷遅延        : {WORLD['avg_delay_minutes']} 分\n"
        f"誤出荷リスク        : {pct(WORLD['shipping_error_risk'])}\n"
        f"フラッシュセール    : {sale}\n"
        f"配送モード          : {mode}\n"
        f"お客様向け案内      : {notice}\n"
    )


def print_header(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def print_status(title: str) -> None:
    print_header(title)
    print(status_summary())


def print_tool_catalog() -> None:
    print_header("このデモで使うツール一覧")
    for name, info in TOOL_INFO.items():
        print(f"\n[{name}] {info['label']}")
        print(f"  承認        : {info['approval']}")
        print(f"  何をするか  : {info['what']}")
        print(f"  何が変わるか: {info['changes']}")
        print(f"  注意点      : {info['risk']}")


def parse_arguments(arguments: Any) -> dict[str, Any]:
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            return {"raw": arguments}
    return {"raw": str(arguments)}


def format_arguments(arguments: Any) -> str:
    parsed = parse_arguments(arguments)
    return json.dumps(parsed, ensure_ascii=False, indent=2)


# =============================================================================
# ツール
# =============================================================================

@function_tool
async def check_shop_status(
    topic: Literal["all", "orders", "shipping", "notice"] = "all",
) -> str:
    """
    ECサイトの現在状況を確認する読み取り専用ツールです。
    状態は一切変更しません。

    Args:
        topic: 見たい領域。all, orders, shipping, notice のいずれか。
    """
    if topic == "orders":
        return (
            "【店舗状況】注文処理\n"
            f"- 未処理注文数: {WORLD['orders_waiting']} 件\n"
            f"- 平均出荷遅延: {WORLD['avg_delay_minutes']} 分\n"
            f"- フラッシュセール: {'ON' if WORLD['flash_sale_enabled'] else 'OFF'}"
        )

    if topic == "shipping":
        return (
            "【店舗状況】配送\n"
            f"- 配送モード: {WORLD['shipping_mode']}\n"
            f"- 誤出荷リスク: {pct(WORLD['shipping_error_risk'])}"
        )

    if topic == "notice":
        return (
            "【店舗状況】お客様向け案内\n"
            f"- 公開状況: {'公開済み' if WORLD['customer_notice_published'] else '未公開'}"
        )

    return "【店舗状況】\n" + status_summary()


@function_tool(needs_approval=True)
async def pause_flash_sale(
    campaign_name: str,
    reason: str,
) -> str:
    """
    フラッシュセールを一時停止する状態変更ツールです。
    売上に影響する可能性があるため、人間の承認が必要です。

    Args:
        campaign_name: 一時停止するキャンペーン名。
        reason: Agent がこの操作を必要と判断した理由。
    """
    before = status_summary()

    if not WORLD["flash_sale_enabled"]:
        return "【実行結果】フラッシュセールはすでに OFF です。状態は変更していません。"

    WORLD["flash_sale_enabled"] = False
    WORLD["orders_waiting"] = max(0, WORLD["orders_waiting"] - 120)
    WORLD["avg_delay_minutes"] = max(0, WORLD["avg_delay_minutes"] - 40)

    after = status_summary()
    return (
        "【実行結果】フラッシュセールを一時停止しました。\n"
        f"- 対象キャンペーン: {campaign_name}\n"
        f"- 理由: {reason}\n\n"
        "変更前:\n"
        f"{before}\n"
        "変更後:\n"
        f"{after}"
    )


@function_tool(needs_approval=True)
async def switch_shipping_to_safe_mode(
    reason: str,
) -> str:
    """
    配送を安全確認モードに切り替える状態変更ツールです。
    出荷速度に影響する可能性があるため、人間の承認が必要です。

    Args:
        reason: Agent がこの操作を必要と判断した理由。
    """
    before = status_summary()

    if WORLD["shipping_mode"] == "safe_review":
        return "【実行結果】配送はすでに安全確認モードです。状態は変更していません。"

    WORLD["shipping_mode"] = "safe_review"
    WORLD["shipping_error_risk"] = max(0.01, WORLD["shipping_error_risk"] - 0.05)
    WORLD["orders_waiting"] = max(0, WORLD["orders_waiting"] - 20)
    WORLD["avg_delay_minutes"] = WORLD["avg_delay_minutes"] + 5

    after = status_summary()
    return (
        "【実行結果】配送を安全確認モードに切り替えました。\n"
        f"- 理由: {reason}\n\n"
        "変更前:\n"
        f"{before}\n"
        "変更後:\n"
        f"{after}"
    )


@function_tool(needs_approval=True)
async def publish_customer_notice(
    message: str,
    reason: str,
) -> str:
    """
    お客様向け案内文を公開する状態変更ツールです。
    外部向けの文面公開なので、人間の承認が必要です。

    Args:
        message: 公開したいお客様向け案内文。
        reason: Agent がこの案内を必要と判断した理由。
    """
    before = status_summary()

    if len(message) > 280:
        return "【実行結果】案内文が長すぎます。280文字以内にしてください。状態は変更していません。"

    WORLD["customer_notice_published"] = True
    WORLD["published_message"] = message

    after = status_summary()
    return (
        "【実行結果】お客様向け案内文を公開しました。\n"
        f"- 理由: {reason}\n"
        f"- 公開文面: {message}\n\n"
        "変更前:\n"
        f"{before}\n"
        "変更後:\n"
        f"{after}"
    )


@function_tool
async def write_internal_note(note: str) -> str:
    """
    社内向けの対応メモを残します。
    外部影響がないため承認不要です。

    Args:
        note: 社内向けの短いメモ。
    """
    if len(note) > 500:
        return "【実行結果】メモが長すぎます。500文字以内にしてください。状態は変更していません。"

    WORLD["internal_notes"].append(note)
    return f"【実行結果】社内メモを記録しました。\n- メモ: {note}"


# =============================================================================
# 評価エージェント
# =============================================================================

class Verdict(BaseModel):
    passed: bool = Field(description="目標を達成したかどうか")
    reason: str = Field(description="判定理由")
    next_goal: str | None = Field(default=None, description="未達の場合の次ラウンド目標")


operator_agent = Agent(
    name="EC注文対応エージェント",
    model=MODEL,
    instructions=(
        "あなたは EC サイトの注文混雑に対応する自律エージェントです。"
        "表示と最終回答は必ず日本語にしてください。"
        "まず check_shop_status で状況を確認してください。"
        "目標は、未処理注文数を100件以下、誤出荷リスクを5%未満にし、"
        "お客様向け案内を公開済みにすることです。"
        "状態を変えるツールは人間の承認が必要です。"
        "ツールを呼ぶときは、reason に『なぜこの操作が必要か』を具体的に書いてください。"
        "最大3つまで状態変更ツールを使ってよいです。"
        "人間が拒否した場合は、その判断を尊重し、より安全な代替策があれば提案してください。"
        "最後に、実行したこと、承認されたこと、拒否されたこと、現在の状態を日本語で要約してください。"
    ),
    tools=[
        check_shop_status,
        pause_flash_sale,
        switch_shipping_to_safe_mode,
        publish_customer_notice,
        write_internal_note,
    ],
)

evaluator_agent = Agent(
    name="EC対応評価エージェント",
    model=MODEL,
    instructions=(
        "あなたは対応完了判定を行う評価エージェントです。"
        "次の3条件をすべて満たす場合だけ passed=true にしてください。"
        "1. 未処理注文数が100件以下。"
        "2. 誤出荷リスクが5%未満。"
        "3. お客様向け案内が公開済み。"
        "判定理由は日本語で書いてください。"
    ),
    output_type=Verdict,
)


# =============================================================================
# 人間の承認フロー
# =============================================================================

def get_interruption_name(interruption: Any) -> str:
    return getattr(interruption, "name", None) or getattr(interruption, "tool_name", None) or "unknown_tool"


def get_interruption_arguments(interruption: Any) -> Any:
    return getattr(interruption, "arguments", None) or getattr(interruption, "tool_input", None)


def print_approval_screen(interruption: Any) -> tuple[str, str | None]:
    tool_name = get_interruption_name(interruption)
    arguments = get_interruption_arguments(interruption)
    info = TOOL_INFO.get(tool_name, {
        "label": "不明なツール",
        "approval": "必要",
        "what": "詳細不明",
        "changes": "詳細不明",
        "risk": "詳細不明",
    })

    print_header("人間の承認が必要です")
    print(f"AIが使いたいツール: {tool_name}")
    print(f"日本語名          : {info['label']}")
    print(f"承認              : {info['approval']}")
    print()
    print("このツールは何をするか:")
    print(f"  {info['what']}")
    print()
    print("実行すると何が変わるか:")
    print(f"  {info['changes']}")
    print()
    print("注意点:")
    print(f"  {info['risk']}")
    print()
    print("AIが指定した引数:")
    print(format_arguments(arguments))
    print()
    print("現在の状態:")
    print(status_summary())
    print("判断を入力してください:")
    print("  y  : 今回だけ承認する")
    print("  n  : 今回だけ拒否する")
    print("  ya : この実行中、このツールを今後すべて承認する")
    print("  na : この実行中、このツールを今後すべて拒否する")
    print("  q  : 拒否して、これ以上の状態変更を止めるようAIに伝える")

    while True:
        answer = input("判断 [y/n/ya/na/q]: ").strip().lower()

        if answer in {"y", "yes"}:
            return "approve", None

        if answer in {"n", "no"}:
            message = input(
                "拒否理由を入力してください "
                "[未入力なら: 人間の確認者がこの操作を拒否しました。]: "
            ).strip()
            return "reject", message or "人間の確認者がこの操作を拒否しました。"

        if answer == "ya":
            return "always_approve", None

        if answer == "na":
            return "always_reject", "人間の確認者が、このツールを今回の実行では使わないよう指示しました。"

        if answer == "q":
            return "reject", "人間の確認者が停止を指示しました。これ以上、状態変更ツールを呼ばないでください。"

        print("y / n / ya / na / q のいずれかを入力してください。")


async def resolve_interruptions(result: Any):
    state = result.to_state()

    for interruption in result.interruptions:
        tool_name = get_interruption_name(interruption)
        arguments = parse_arguments(get_interruption_arguments(interruption))

        decision, rejection_message = await asyncio.get_running_loop().run_in_executor(
            None,
            print_approval_screen,
            interruption,
        )

        WORLD["human_decisions"].append({
            "tool": tool_name,
            "arguments": arguments,
            "decision": decision,
            "rejection_message": rejection_message,
        })

        if decision == "approve":
            state.approve(interruption, always_approve=False)
        elif decision == "always_approve":
            state.approve(interruption, always_approve=True)
        elif decision == "reject":
            state.reject(interruption, rejection_message=rejection_message)
        elif decision == "always_reject":
            state.reject(
                interruption,
                always_reject=True,
                rejection_message=rejection_message,
            )

    return state


async def run_operator_with_human_review(prompt: str):
    result = await Runner.run(
        operator_agent,
        prompt,
        max_turns=12,
    )

    while result.interruptions:
        state = await resolve_interruptions(result)
        result = await Runner.run(
            operator_agent,
            state,
            max_turns=12,
        )

    return result


async def main(max_rounds: int = 3) -> None:
    print_header("OpenAI Agents SDK: Human-in-the-loop デモ")
    print("題材: ECサイトで注文が急増し、出荷遅延と誤出荷リスクが高まっている状況")
    print("目的: AIが状況を見て対策を提案し、状態変更は人間が承認してから実行することを見せます。")
    print_tool_catalog()
    print_status("初期状態")

    goal = (
        "ECサイトの注文混雑を安全に収束させてください。"
        "成功条件は、未処理注文数100件以下、誤出荷リスク5%未満、"
        "お客様向け案内が公開済み、の3つです。"
    )

    for round_no in range(1, max_rounds + 1):
        print_header(f"ラウンド {round_no} / {max_rounds}")

        operator_prompt = f"""
あなたはEC注文対応エージェントです。

ゴール:
{goal}

現在の状態:
{status_summary()}

進め方:
- まず check_shop_status で状況を確認する。
- 状態変更ツールを使う場合は、reason に判断理由を書く。
- 状態変更ツールは人間の承認が必要。
- 状態変更は最大3つまで。
- 最後に、日本語で短く対応結果をまとめる。
"""

        op_result = await run_operator_with_human_review(operator_prompt)

        print_header("AIの対応結果")
        print(op_result.final_output)

        eval_prompt = f"""
ゴール:
{goal}

現在の状態:
{status_summary()}

AIの対応結果:
{op_result.final_output}
"""

        eval_result = await Runner.run(
            evaluator_agent,
            eval_prompt,
            max_turns=4,
        )

        verdict = eval_result.final_output
        if not isinstance(verdict, Verdict):
            verdict = Verdict.model_validate_json(str(verdict))

        print_header("評価結果")
        print(f"達成したか: {'はい' if verdict.passed else 'いいえ'}")
        print(f"理由      : {verdict.reason}")

        if verdict.passed:
            print_status("最終状態")
            print_header("デモ終了")
            print("成功条件を満たしたため、AIの自律ループを停止しました。")
            return

        if verdict.next_goal:
            goal = verdict.next_goal

    print_status("最終状態")
    print_header("デモ終了")
    print("最大ラウンド数に達したため停止しました。")


if __name__ == "__main__":
    asyncio.run(main())

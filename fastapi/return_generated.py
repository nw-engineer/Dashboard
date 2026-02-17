# Promptfoo Python provider
# https://www.promptfoo.dev/docs/providers/python/  (概念: call_api を実装して output を返す)

def call_api(prompt, options, context):
    # context["vars"] に tests[].vars が入る
    vars_ = context.get("vars", {})
    return {
        "output": vars_.get("generated", "")
    }

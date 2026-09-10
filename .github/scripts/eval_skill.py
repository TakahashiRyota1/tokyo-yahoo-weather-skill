#!/usr/bin/env python3
"""Skill発火/非発火の自動評価。

各 evals/<skill-name>.yaml の positive_cases / negative_cases を、
対象skillのSKILL.md(name + description)だけをルーターに見せたClaude APIで
「このSkillを使うべきか」判定させ、期待値と突き合わせる。

実際のAIP/Claude Codeのスキル選択ロジックそのものではなく、
description の発火/非発火適合度を測るproxy評価(モック評価)。
"""
import json
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pyyaml"], check=True)
    import yaml

try:
    import anthropic
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "anthropic"], check=True)
    import anthropic

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "skills"
EVALS_DIR = REPO_ROOT / "evals"

DEFAULT_MODEL = "claude-opus-5"
MODEL = os.environ.get("EVAL_MODEL", DEFAULT_MODEL)

ROUTER_SYSTEM_PROMPT = """あなたはAIエージェントのSkillルーターです。
以下は呼び出し可能な、たった1つのSkillの定義です。

- name: {name}
- description: {description}

ユーザーの発言に対して、このSkillを呼び出すべきかどうかを判定してください。
出力は "true" または "false" のみを1行で返してください。それ以外の文字は一切出力しないでください。
"""


def load_frontmatter(skill_md: Path) -> dict:
    text = skill_md.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    if not m:
        raise ValueError(f"{skill_md}: frontmatterが見つかりません")
    return yaml.safe_load(m.group(1))


def judge(client: "anthropic.Anthropic", name: str, description: str, user_input: str) -> bool:
    response = client.messages.create(
        model=MODEL,
        max_tokens=256,
        output_config={"effort": "low"},
        system=ROUTER_SYSTEM_PROMPT.format(name=name, description=description),
        messages=[{"role": "user", "content": user_input}],
    )
    text = "".join(b.text for b in response.content if b.type == "text").strip().lower()
    if "true" in text:
        return True
    if "false" in text:
        return False
    raise ValueError(f"判定不能な応答: {text!r}")


def main():
    if not EVALS_DIR.is_dir():
        print(f"::warning::評価ケースディレクトリ '{EVALS_DIR}' がありません。evalをスキップします。")
        return

    eval_files = sorted(EVALS_DIR.glob("*.yaml"))
    if not eval_files:
        print("::warning::評価ケース(*.yaml)が1件もありません。evalをスキップします。")
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("::warning::ANTHROPIC_API_KEY が未設定のため、Skill評価をスキップしました。"
              "GitHub SecretsにANTHROPIC_API_KEYを登録すると有効になります。")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write("## Skill評価: スキップ\n\nANTHROPIC_API_KEY が未設定のため実行していません。\n")
        return

    client = anthropic.Anthropic(api_key=api_key)

    total = 0
    failed = []
    lines = [f"## Skill評価結果 (model: {MODEL})\n", "| skill | case | 種別 | 期待 | 判定 | 結果 |", "|---|---|---|---|---|---|"]

    for eval_file in eval_files:
        data = yaml.safe_load(eval_file.read_text(encoding="utf-8"))
        skill_name = data["skill"]
        skill_md = SKILLS_DIR / skill_name / "SKILL.md"
        if not skill_md.is_file():
            print(f"::error file={eval_file}::対応するSKILL.mdが見つかりません: {skill_md}")
            failed.append((skill_name, "(setup)", "SKILL.md not found"))
            continue

        fm = load_frontmatter(skill_md)
        name = fm.get("name", skill_name)
        description = fm.get("description", "")

        cases = [(c, "positive", True) for c in data.get("positive_cases", [])] + \
                [(c, "negative", False) for c in data.get("negative_cases", [])]

        for case, kind, expected in cases:
            total += 1
            case_id = case.get("id", "?")
            user_input = case["input"]
            try:
                actual = judge(client, name, description, user_input)
            except Exception as e:
                print(f"::error::{skill_name}/{case_id} 判定中にエラー: {e}")
                failed.append((skill_name, case_id, f"API error: {e}"))
                lines.append(f"| {skill_name} | {case_id} | {kind} | {expected} | ERROR | ❌ |")
                continue

            ok = actual == expected
            mark = "✅" if ok else "❌"
            lines.append(f"| {skill_name} | {case_id} | {kind} | {expected} | {actual} | {mark} |")
            if not ok:
                failed.append((skill_name, case_id, f"expected={expected} actual={actual} input={user_input!r}"))

    lines.append(f"\n合計: {total}件 / 失敗: {len(failed)}件\n")
    report = "\n".join(lines)
    print(report)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(report + "\n")

    if failed:
        print(f"\n::error::Skill評価が{len(failed)}件失敗しました")
        for skill_name, case_id, detail in failed:
            print(f"  - {skill_name}/{case_id}: {detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()

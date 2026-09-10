#!/usr/bin/env python3
"""AIP向けSkillリポジトリのCI Lint。
~/.claude/rules/aip-skill-authoring.md の作成規約(#8チェックリスト)を機械検証する。
"""
import re
import sys
import subprocess
from pathlib import Path

try:
    import yaml
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pyyaml"], check=True)
    import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "skills"

FORBIDDEN_JUNK = {".DS_Store", "__MACOSX", "Thumbs.db"}
# Claude Code固有でAIPでは意味を持たない(無視される想定/混入させない)frontmatterキー
CLAUDE_CODE_ONLY_KEYS = {"allowed-tools", "model"}

errors = []
warnings = []


def err(path, msg):
    errors.append(f"::error file={path}::{msg}")


def warn(path, msg):
    warnings.append(f"::warning file={path}::{msg}")


def check_frontmatter(skill_md: Path, skill_name: str):
    text = skill_md.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    if not m:
        err(skill_md, "YAML frontmatter (--- ... ---) が見つかりません")
        return
    raw = m.group(1)

    # 未クオートの description 内 "キー: " 混入によるScannerError検出
    # (aip-skill-authoring.md #2: 厳密YAMLでないと AIP登録時に「SKILL.mdファイルを読み取れません」になる実例あり)
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        err(skill_md, f"frontmatterが厳密YAMLとして解析できません(AIP登録時に失敗する既知パターン): {e}")
        return

    if not isinstance(data, dict):
        err(skill_md, "frontmatterがマッピング(dict)ではありません")
        return

    if "name" not in data:
        err(skill_md, "frontmatterに必須キー 'name' がありません")
    elif data["name"] != skill_name:
        err(skill_md, f"name: '{data['name']}' がディレクトリ名 '{skill_name}' と一致しません")

    if "description" not in data:
        err(skill_md, "frontmatterに必須キー 'description' がありません")
    elif not isinstance(data["description"], str) or not data["description"].strip():
        err(skill_md, "'description' が空、または文字列ではありません")

    present_cc_keys = CLAUDE_CODE_ONLY_KEYS & set(data.keys())
    if present_cc_keys:
        warn(skill_md, f"Claude Code固有のfrontmatterキーが混入しています: {sorted(present_cc_keys)}")


def check_junk_files(skill_dir: Path):
    for p in skill_dir.rglob("*"):
        if p.name in FORBIDDEN_JUNK:
            err(p, f"不要ファイル '{p.name}' が同梱されています(zip化前に除外してください)")


def check_dev_null_redirect(skill_dir: Path):
    # AIP実行環境は /dev/null が書き込み不可のため "2>/dev/null" を含むシェルコマンドは失敗する
    pattern = re.compile(r"2\s*>\s*/dev/null")
    for p in skill_dir.rglob("*"):
        if p.is_file() and p.suffix in {".md", ".sh", ".py"}:
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if pattern.search(content):
                err(p, "'2>/dev/null' を含んでいます。AIP実行環境は /dev/null が書き込み不可で失敗します")


def check_python_syntax(skill_dir: Path):
    for p in skill_dir.rglob("*.py"):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(p)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            err(p, f"Python構文エラー: {result.stderr.strip()}")


def check_absolute_path_refs(skill_md: Path, skill_name: str):
    text = skill_md.read_text(encoding="utf-8")
    for m in re.finditer(r"/skills/([A-Za-z0-9_-]+)/", text):
        ref_name = m.group(1)
        if ref_name != skill_name:
            warn(skill_md, f"本文中の絶対パス参照 '/skills/{ref_name}/' がスキル名 '{skill_name}' と一致しません")


def main():
    if not SKILLS_DIR.is_dir():
        err(SKILLS_DIR, "'skills/' ディレクトリが存在しません")
        print("\n".join(errors))
        sys.exit(1)

    skill_dirs = sorted(d for d in SKILLS_DIR.iterdir() if d.is_dir())
    if not skill_dirs:
        err(SKILLS_DIR, "'skills/' 配下にスキルフォルダがありません")

    for skill_dir in skill_dirs:
        skill_name = skill_dir.name
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            err(skill_dir, f"SKILL.md がありません (期待パス: {skill_md.relative_to(REPO_ROOT)})")
            continue
        check_frontmatter(skill_md, skill_name)
        check_absolute_path_refs(skill_md, skill_name)
        check_junk_files(skill_dir)
        check_dev_null_redirect(skill_dir)
        check_python_syntax(skill_dir)

    for w in warnings:
        print(w)
    for e in errors:
        print(e)

    print(f"\n検査対象スキル: {len(skill_dirs)}件 / エラー: {len(errors)}件 / 警告: {len(warnings)}件")

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()

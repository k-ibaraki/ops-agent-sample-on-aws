"""skills/ 配下のスキル定義のテスト。

スキル本文はモデルが読み込んで初めて効くため、退行（誤削除・要素の欠落）を
ここで検知する。書式スキルの検証も test_agent.py からこちらへ集約した。
"""

from ops_agent.agent import SKILLS_DIR


def _read_skill(skill_name: str) -> str:
    skill_md = SKILLS_DIR / skill_name / "SKILL.md"
    assert skill_md.is_file()
    return skill_md.read_text(encoding="utf-8")


def test_採点基準スキルの定義が存在する() -> None:
    content = _read_skill("scoring-rubric")

    assert "name: scoring-rubric" in content
    assert "description:" in content


def test_採点基準スキルに3つの観点が含まれる() -> None:
    content = _read_skill("scoring-rubric")

    assert "影響範囲" in content
    assert "緊急度" in content
    assert "継続性" in content


def test_採点基準スキルにスコア帯の目安が含まれる() -> None:
    content = _read_skill("scoring-rubric")

    assert "100" in content
    assert "90" in content
    assert "70" in content
    assert "50" in content
    assert "25" in content


def test_調査方針スキルの定義が存在する() -> None:
    content = _read_skill("investigation-policy")

    assert "name: investigation-policy" in content
    assert "description:" in content


def test_調査方針スキルに日次チェックの手順が含まれる() -> None:
    content = _read_skill("investigation-policy")

    # アラームで全体を把握し、ログとメトリクスで深掘りする流れ
    assert "describe_alarms" in content
    assert "query_logs" in content
    assert "get_metric_statistics" in content


def test_調査方針スキルに事実に基づく判断の方針が含まれる() -> None:
    content = _read_skill("investigation-policy")

    # 推測で問題をでっち上げず、なければ「異常なし」と報告する
    assert "推測" in content
    assert "異常なし" in content


def test_書式スキルの定義が存在する() -> None:
    content = _read_skill("slack-report-style")

    assert "name: slack-report-style" in content
    assert "description:" in content
    # 報告文中の時刻は JST で書くルールが定義されている
    assert "JST" in content


def test_アドホック回答の書式スキルの定義が存在する() -> None:
    content = _read_skill("adhoc-report-style")

    assert "name: adhoc-report-style" in content
    assert "description:" in content
    assert "JST" in content

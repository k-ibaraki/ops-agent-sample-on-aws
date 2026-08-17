"""採点結果のスキーマ。エージェントの構造化出力として使う。"""

from pydantic import BaseModel, Field


class Finding(BaseModel):
    """調査で見つけた個々の問題と、その採点結果。"""

    title: str = Field(description="問題の短いタイトル")
    score: int = Field(
        ge=0,
        le=100,
        description="深刻度スコア（0〜100）。影響範囲・緊急度・継続性を総合して採点する",
    )
    region: str = Field(description="問題が発生しているリージョン")
    resource: str = Field(description="関係するリソース名（ロググループ・アラーム・関数名など）")
    summary: str = Field(description="何が起きているかの説明")
    recommendation: str = Field(description="推奨される対応アクション")


class DailyReport(BaseModel):
    """日次チェックの最終レポート。"""

    overall_summary: str = Field(description="アカウント全体の状態の要約（2〜3文）")
    findings: list[Finding] = Field(default_factory=list, description="見つけた問題の一覧")

    def notable_findings(self, threshold: int) -> list[Finding]:
        """閾値以上のスコアの問題を、スコアの高い順に返す。"""
        return sorted(
            (f for f in self.findings if f.score >= threshold),
            key=lambda f: f.score,
            reverse=True,
        )

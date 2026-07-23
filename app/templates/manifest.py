"""M9b 模板 registry：登錄可用的 Typst 研報模板。

每款模板實作 M9a 的「模板契約」——匯出 `report`、`section-heading`、`kpi-strip`、
`chart-figure` 四個函式，接收同一組固定區塊（見 app/templates/ib-classic.typ）。
「選模板」只換渲染層、不動任何內容生成邏輯（markdown 為真相）。

未知 template_id 一律安全退回預設（fail-safe）——舊請求不帶 template_id 走預設，
typo/退役 id 不讓研報生不出來。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TemplateSpec:
    """一款模板的登錄項。filename 相對 app/templates/；thumbnail 相對 web/static/。"""

    id: str
    name: str
    description: str
    filename: str
    is_default: bool = False
    thumbnail: str | None = None


# 登錄表（插入序＝前端呈現序）。M9b-1 先只有 M9a 的 ib-classic；broker-modern /
# privatebank-dark 於後續 PR 連同各自 .typ 加入，plumbing 不變。
_TEMPLATES: tuple[TemplateSpec, ...] = (
    TemplateSpec(
        id="ib-classic",
        name="國際投行經典",
        description="密集雙欄、封面/評等框/KPI 卡（M9a 首款）",
        filename="ib-classic.typ",
        is_default=True,
    ),
)

_BY_ID = {t.id: t for t in _TEMPLATES}
_DEFAULT = next(t for t in _TEMPLATES if t.is_default)


def list_templates() -> list[TemplateSpec]:
    """所有可選模板（前端模板選擇器用）。"""
    return list(_TEMPLATES)


def default_template() -> TemplateSpec:
    return _DEFAULT


def resolve(template_id: str | None) -> TemplateSpec:
    """template_id → TemplateSpec。None／未知 id → 預設（fail-safe，不拋）。"""
    if not template_id:
        return _DEFAULT
    return _BY_ID.get(template_id, _DEFAULT)

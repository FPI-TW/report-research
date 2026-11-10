# web/routers/branding.py
"""品牌（免登入）：/api/branding（顯示用的名稱、logo／favicon URL、色票）與 /api/branding/assets/{name}。

兩支都在 web/server.py 的免登入白名單：登入頁與 SPA 在登入前就要畫出品牌。所以這裡**只交顯示用的東西**——
名稱（含英文介面用的名稱）、asset URL、強調色與介面語系——不碰 profile 的其他 section（prompt 參數、市場子集、
TOTP 發行者都不出現在回應裡），新增欄位前先想清楚它能不能給未登入的人看。

asset 端點只交出 profile 引用的檔（`Branding.assets()`，以檔名查表、不拼路徑），目錄裡其他檔一律 404。
URL 帶內容雜湊 `?v=`：雜湊相符時長快取（immutable），不符或沒帶時 no-cache＋ETag，換了 logo 重啟後不會卡舊圖。
SVG 可以帶 script，直接開 asset URL 時會以本站 origin 執行：回應一律加 CSP sandbox 與 nosniff。
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.profile import Asset, get_profile
from web.errors import AppError

router = APIRouter()

ASSET_PATH = "/api/branding/assets/"
_VERSION_LEN = 16
_ASSET_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"


def asset_url(asset: Asset | None) -> str | None:
    if asset is None:
        return None
    return f"{ASSET_PATH}{asset.name}?v={asset.sha256[:_VERSION_LEN]}"


def branding_payload() -> dict:
    profile = get_profile()
    inst, brand = profile.institution, profile.branding
    return {
        "institution_name": inst.name,
        "product_name": inst.product_name,
        "short_name": inst.short_name,
        # 英文介面用的名稱（profile 沒給就沿用原名）；前端以 optional() 宣告，舊版前端讀不到也照常。
        "institution_name_en": inst.localized("en")[0],
        "product_name_en": inst.localized("en")[1],
        "short_name_en": inst.localized("en")[2],
        "locale": {"default": profile.locale.default, "available": list(profile.locale.available)},
        "logo_light_url": asset_url(brand.logo_light),
        "logo_dark_url": asset_url(brand.logo_dark),
        "favicon_url": asset_url(brand.favicon),
        "colors": {"light": dict(brand.colors_light), "dark": dict(brand.colors_dark)},
    }


@router.get("/api/branding")
async def branding():
    return JSONResponse(branding_payload(), headers={"Cache-Control": "no-cache"})


@router.get("/api/branding/assets/{name}")
async def branding_asset(name: str, request: Request):
    asset = get_profile().branding.assets().get(name)
    if asset is None:
        raise AppError(404, "asset_not_found", "找不到這個品牌檔案")
    etag = f'"{asset.sha256}"'
    versioned = request.query_params.get("v") == asset.sha256[:_VERSION_LEN]
    headers = {
        "ETag": etag,
        "Cache-Control": "public, max-age=31536000, immutable" if versioned else "no-cache",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": _ASSET_CSP,
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(asset.path.read_bytes(), media_type=asset.content_type, headers=headers)

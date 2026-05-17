"""アイキャッチ画像自動生成 (note 投稿用)

公式ブランド「東京【アロマモア】」のビジュアル基調 (黒×ゴールド・明朝体) を踏襲。
- ヴィネット + ラジアルグローでキャンドル/間接照明風の艶
- ヘアラインゴールドの上下ラインと装飾区切り
- 明朝体タイトル + ブランドキャッチ「ひと時の安らぎを」
- 配色は reference_date から決定的に選ばれる (同じ週なら同じ画像)

使い方:
  python3 src/generate_eyecatch.py             # 最新基準日で生成
  python3 src/generate_eyecatch.py --date=2026-05-17

依存: Pillow, NOTO_CJK_SANS / NOTO_CJK_SERIF (環境変数で日本語フォントパス指定可)
"""

import argparse
import math
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from db import get_conn

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output"

WIDTH, HEIGHT = 1280, 720

# 配色テーマ: 黒〜深色をベースに、金/シャンパン/ローズの淡いハイライト。
# 公式ブランドのトーン (高級メンズエステ・夜の静謐) を尊重し、3 種に厳選。
THEMES = [
    {
        # 漆黒 × シャンパンゴールド (王道のラグジュアリー)
        "name": "noir-champagne",
        "bg_outer": (8, 6, 10),
        "bg_inner": (40, 30, 25),
        "glow":     (180, 140, 90),
        "accent":   (210, 170, 110),
        "text":     (245, 235, 220),
        "muted":    (180, 165, 140),
    },
    {
        # ワインボルドー × ローズゴールド (官能的)
        "name": "bordeaux-rose",
        "bg_outer": (12, 5, 10),
        "bg_inner": (75, 25, 40),
        "glow":     (190, 110, 100),
        "accent":   (220, 170, 145),
        "text":     (250, 235, 230),
        "muted":    (200, 165, 160),
    },
    {
        # ミッドナイトネイビー × ペールゴールド (上品で凛とした夜)
        "name": "midnight-pale-gold",
        "bg_outer": (5, 8, 18),
        "bg_inner": (25, 40, 70),
        "glow":     (140, 150, 180),
        "accent":   (220, 200, 140),
        "text":     (240, 240, 250),
        "muted":    (185, 195, 215),
    },
]


# ────────────────────────────────────────────────────────────
# Font 解決
# ────────────────────────────────────────────────────────────
def _find(env_keys: list[str], fallbacks: list[str]) -> str:
    for key in env_keys:
        p = os.environ.get(key)
        if p and Path(p).exists():
            return p
    for p in fallbacks:
        if Path(p).exists():
            return p
    raise FileNotFoundError(
        f"日本語フォントが見つかりません (env: {env_keys})。"
        " NOTO_CJK_SANS / NOTO_CJK_SERIF を設定するか、"
        "fonts-noto-cjk / fonts-noto-cjk-extra をインストールしてください。"
    )


def find_serif_font() -> str:
    return _find(
        ["NOTO_CJK_SERIF"],
        [
            "/usr/share/fonts/opentype/noto-cjk/NotoSerifCJK-VF.otf.ttc",
            "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
            "/System/Library/Fonts/Hiragino Mincho ProN.ttc",
            "/System/Library/Fonts/Hiragino Mincho ProN.otf",
        ],
    )


def find_sans_font() -> str:
    return _find(
        ["NOTO_CJK_SANS", "NOTO_CJK_FONT"],
        [
            "/usr/share/fonts/opentype/noto-cjk/NotoSansCJK-VF.otf.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
        ],
    )


# ────────────────────────────────────────────────────────────
# 背景レイヤ
# ────────────────────────────────────────────────────────────
def _radial_background(theme: dict) -> Image.Image:
    """中心が ほのかに灯る ようなラジアルグラデーション。間接照明風。"""
    img = Image.new("RGB", (WIDTH, HEIGHT))
    pixels = img.load()
    cx, cy = WIDTH / 2, HEIGHT / 2 + 30  # わずかに下寄せで重心が安定
    max_d = math.hypot(WIDTH / 2, HEIGHT / 2)
    inner = theme["bg_inner"]
    outer = theme["bg_outer"]
    for y in range(HEIGHT):
        for x in range(WIDTH):
            d = math.hypot(x - cx, y - cy) / max_d
            # 中心は inner、外周は outer。easing で滑らかに。
            t = min(1.0, d ** 1.4)
            r = int(inner[0] * (1 - t) + outer[0] * t)
            g = int(inner[1] * (1 - t) + outer[1] * t)
            b = int(inner[2] * (1 - t) + outer[2] * t)
            pixels[x, y] = (r, g, b)
    return img


def _add_glow_spots(img: Image.Image, theme: dict, rng: random.Random) -> Image.Image:
    """淡い金色のボケ光。シャンデリアや間接照明の反射を意識。"""
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for _ in range(14):
        x = rng.randint(-60, WIDTH + 60)
        y = rng.randint(-60, HEIGHT + 60)
        radius = rng.randint(60, 180)
        opacity = rng.randint(18, 48)
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=(*theme["glow"], opacity),
        )
    overlay = overlay.filter(ImageFilter.GaussianBlur(50))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def _add_sparkles(img: Image.Image, theme: dict, rng: random.Random) -> Image.Image:
    """金の微粒子。光が舞うイメージ。"""
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for _ in range(80):
        x = rng.randint(0, WIDTH)
        y = rng.randint(0, HEIGHT)
        r = rng.choice([1, 1, 1, 2, 2, 3])
        alpha = rng.randint(120, 220)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(*theme["accent"], alpha))
    overlay = overlay.filter(ImageFilter.GaussianBlur(0.6))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def _add_vignette(img: Image.Image) -> Image.Image:
    """四隅を落として没入感を出す。"""
    overlay = Image.new("L", (WIDTH, HEIGHT), 0)
    draw = ImageDraw.Draw(overlay)
    cx, cy = WIDTH / 2, HEIGHT / 2
    max_d = math.hypot(WIDTH / 2, HEIGHT / 2)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            d = math.hypot(x - cx, y - cy) / max_d
            v = int(min(255, max(0, (d - 0.55) * 320)))
            overlay.putpixel((x, y), v)
    overlay = overlay.filter(ImageFilter.GaussianBlur(60))
    dark = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    return Image.composite(dark, img, overlay)


# ────────────────────────────────────────────────────────────
# 装飾
# ────────────────────────────────────────────────────────────
def _hairlines(img: Image.Image, theme: dict) -> Image.Image:
    """上下の極細ゴールドラインで額縁感。"""
    draw = ImageDraw.Draw(img)
    pad = 56
    # 外枠の極細ライン
    draw.rectangle((pad, pad, WIDTH - pad, HEIGHT - pad), outline=theme["accent"], width=1)
    # 上下にダブルライン (ホテルサイネージ風)
    draw.line((pad + 12, pad - 6, WIDTH - pad - 12, pad - 6), fill=theme["accent"], width=1)
    draw.line((pad + 12, HEIGHT - pad + 6, WIDTH - pad - 12, HEIGHT - pad + 6),
              fill=theme["accent"], width=1)
    return img


def _decoration_divider(draw: ImageDraw.ImageDraw, y: int, theme: dict, width: int = 320) -> None:
    """ ◆ を中央に置いた装飾区切り線。"""
    cx = WIDTH // 2
    # 左右の細線
    draw.line((cx - width // 2, y, cx - 20, y), fill=theme["accent"], width=1)
    draw.line((cx + 20, y, cx + width // 2, y), fill=theme["accent"], width=1)
    # 中央のひし形
    draw.polygon(
        [(cx, y - 6), (cx + 7, y), (cx, y + 6), (cx - 7, y)],
        fill=theme["accent"],
    )


# ────────────────────────────────────────────────────────────
# テキスト描画
# ────────────────────────────────────────────────────────────
def _text_centered(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: int,
    font: ImageFont.FreeTypeFont,
    fill,
    spacing: int = 0,
) -> None:
    """spacing>0 で letter-spacing 風に1文字ずつ描画。"""
    if spacing == 0:
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        draw.text(((WIDTH - w) // 2, y), text, font=font, fill=fill)
        return
    # 簡易 letter-spacing
    widths = [draw.textbbox((0, 0), ch, font=font)[2] for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = (WIDTH - total) // 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill)
        x += w + spacing


def _draw_title(img: Image.Image, ref: date, theme: dict, serif: str, sans: str) -> None:
    draw = ImageDraw.Draw(img)
    f_brand_mark = ImageFont.truetype(sans, 26)   # — AROMA more —
    f_brand_jp   = ImageFont.truetype(serif, 78)  # 東京【アロマモア】
    f_main       = ImageFont.truetype(serif, 64)  # セラピスト人気ランキング
    f_date       = ImageFont.truetype(serif, 64)  # 2026.05.17 (大きく目立たせる)
    f_latest     = ImageFont.truetype(sans, 28)   # 最新 (装飾的バッジ風)

    # ブランドマーク (上、letter-spaced)。公式綴りは "AROMA more"
    _text_centered(draw, "—   AROMA   more   —", 100, f_brand_mark, theme["muted"], spacing=6)

    # ブランド和名 (中央上)
    _text_centered(draw, "東京【アロマモア】", 160, f_brand_jp, theme["text"])

    # 装飾区切り
    _decoration_divider(draw, 290, theme, width=400)

    # メインタイトル
    _text_centered(draw, "セラピスト人気ランキング", 330, f_main, theme["text"], spacing=4)

    # 装飾区切り (下)
    _decoration_divider(draw, 470, theme, width=300)

    # 日付 (大きく・accent color で目立たせる)
    date_str = ref.strftime("%Y.%m.%d")
    bbox = draw.textbbox((0, 0), date_str, font=f_date)
    date_w = bbox[2] - bbox[0]
    latest_text = "  最新"
    latest_w = draw.textbbox((0, 0), latest_text, font=f_latest)[2]
    total_w = date_w + latest_w + 10
    x = (WIDTH - total_w) // 2
    y = 510
    # 日付本体 (大きめ・shadow付きで重厚に)
    draw.text((x + 2, y + 2), date_str, font=f_date, fill=(0, 0, 0, 180))
    draw.text((x, y), date_str, font=f_date, fill=theme["accent"])
    # 最新ラベル (右隣、垂直中央揃え)
    draw.text((x + date_w + 18, y + 26), "最新", font=f_latest, fill=theme["text"])


# ────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────
def generate_eyecatch(ref: date, out: Path) -> None:
    theme = THEMES[ref.toordinal() % len(THEMES)]
    rng = random.Random(ref.toordinal())

    serif = find_serif_font()
    sans = find_sans_font()

    img = _radial_background(theme)
    img = _add_glow_spots(img, theme, rng)
    img = _add_vignette(img)
    img = _add_sparkles(img, theme, rng)
    img = _hairlines(img, theme)
    _draw_title(img, ref, theme, serif, sans)

    OUTPUT_DIR.mkdir(exist_ok=True)
    img.save(out, "PNG", optimize=True)
    print(f"生成完了: {out}  (theme={theme['name']})")


def _default_reference_date() -> date:
    conn = get_conn()
    row = conn.execute("SELECT MAX(reference_date) FROM weekly_summaries").fetchone()
    conn.close()
    return date.fromisoformat(row[0]) if row and row[0] else date.today()


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="基準日 YYYY-MM-DD（省略時は最新 reference_date）")
    args = parser.parse_args()

    ref = date.fromisoformat(args.date) if args.date else _default_reference_date()
    monday = _week_start(ref)
    out = OUTPUT_DIR / f"eyecatch_{monday.strftime('%Y%m%d')}.png"
    generate_eyecatch(ref, out)


if __name__ == "__main__":
    main()

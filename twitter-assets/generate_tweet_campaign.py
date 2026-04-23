from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1600
HEIGHT = 900
OUT_DIR = Path(__file__).resolve().parent / "campaign-apr-2026"


def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
    ]
    for font_path in candidates:
        try:
            return ImageFont.truetype(font_path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def gradient_background(color_top: str, color_bottom: str) -> Image.Image:
    top = hex_to_rgb(color_top)
    bottom = hex_to_rgb(color_bottom)
    img = Image.new("RGB", (WIDTH, HEIGHT), color_top)
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        t = y / max(HEIGHT - 1, 1)
        r = int(top[0] * (1 - t) + bottom[0] * t)
        g = int(top[1] * (1 - t) + bottom[1] * t)
        b = int(top[2] * (1 - t) + bottom[2] * t)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))
    return img


def add_header(draw: ImageDraw.ImageDraw, title: str, subtitle: str) -> None:
    draw.text((100, 90), title, fill="white", font=get_font(72, bold=True))
    draw.text((100, 190), subtitle, fill="#B9D8FF", font=get_font(34))


def add_brand(draw: ImageDraw.ImageDraw) -> None:
    draw.rounded_rectangle((1280, 70, 1520, 140), radius=20, fill="#0A1E3B", outline="#2BD1FF", width=3)
    draw.text((1320, 90), "TradingBot", fill="#7BE7FF", font=get_font(30, bold=True))


def add_footer(draw: ImageDraw.ImageDraw, text: str) -> None:
    draw.text((100, 820), text, fill="#9BC1E5", font=get_font(28))


def add_cards(draw: ImageDraw.ImageDraw, cards: list[tuple[str, list[str]]]) -> None:
    x = 100
    y = 300
    card_w = 440
    card_h = 420
    gap = 40
    for title, bullets in cards:
        draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=24, fill="#07162B", outline="#2A7CC7", width=3)
        draw.text((x + 30, y + 28), title, fill="#7BE7FF", font=get_font(38, bold=True))
        by = y + 105
        for bullet in bullets:
            draw.text((x + 30, by), f"- {bullet}", fill="#E8F1FF", font=get_font(28))
            by += 62
        x += card_w + gap


def make_visual_1() -> None:
    img = gradient_background("#07162B", "#11396B")
    draw = ImageDraw.Draw(img)
    add_brand(draw)
    add_header(
        draw,
        "Trade Momentum with Intelligence",
        "Schwab-integrated automation for scanning, risk, and execution.",
    )
    add_cards(
        draw,
        [
            ("Scan", ["Stage 2 + VCP setups", "Parallel shortlist flow"]),
            ("Decide", ["Probabilistic overlays", "Quality-gated signals"]),
            ("Execute", ["Guardrailed orders", "Adaptive exits"]),
        ],
    )
    add_footer(draw, "Built for disciplined momentum systems, not random signals.")
    img.save(OUT_DIR / "tweet-visual-01-hero.png")


def make_visual_2() -> None:
    img = gradient_background("#061225", "#0B2E54")
    draw = ImageDraw.Draw(img)
    add_brand(draw)
    add_header(draw, "Two-Stage Signal Pipeline", "Fast filters first. Deep enrichment second.")
    add_cards(
        draw,
        [
            ("Stage A", ["Stage 2 trend structure", "VCP volume contraction", "Optional sector filter"]),
            ("Stage B", ["PEAD + forensic checks", "Advisory probability overlay", "Quality gates + ensemble"]),
            ("Output", ["Ranked shortlist", "Diagnostics for every miss", "Transparent signal reasons"]),
        ],
    )
    add_footer(draw, "A practical architecture for speed + depth in the same scan.")
    img.save(OUT_DIR / "tweet-visual-02-pipeline.png")


def make_visual_3() -> None:
    img = gradient_background("#140E1C", "#4D1E2E")
    draw = ImageDraw.Draw(img)
    add_brand(draw)
    add_header(draw, "Risk Guardrails Built In", "Downside-aware logic before upside chase.")
    add_cards(
        draw,
        [
            ("Regime Gate", ["Blocks scans when SPY", "is below 200 SMA", "unless explicitly allowed"]),
            ("Event Risk", ["Can block or downsize", "earnings-near candidates", "to reduce surprise risk"]),
            ("Adaptive Exits", ["Partial TP rules", "Breakeven transitions", "Time-stop protection"]),
        ],
    )
    add_footer(draw, "Guardrails are part of the strategy, not an afterthought.")
    img.save(OUT_DIR / "tweet-visual-03-risk.png")


def make_visual_4() -> None:
    img = gradient_background("#062035", "#114E6F")
    draw = ImageDraw.Draw(img)
    add_brand(draw)
    add_header(draw, "Backtest Intelligence Overlay", "Quantify plugin impact before promoting shadow features live.")
    add_cards(
        draw,
        [
            ("Baseline", ["Run with overlays off", "for clean comparisons", "across market eras"]),
            ("Treatment", ["Enable selected overlay", "then replay same window", "for apples-to-apples delta"]),
            ("Verdict", ["Win Rate", "Return / CAGR / PF", "Drawdown impact"]),
        ],
    )
    add_footer(draw, "Promotion decisions are evidence-driven, not gut-driven.")
    img.save(OUT_DIR / "tweet-visual-04-backtest-intelligence.png")


def make_visual_5() -> None:
    img = gradient_background("#081427", "#1E3A5A")
    draw = ImageDraw.Draw(img)
    add_brand(draw)
    add_header(draw, "From Local Bot to Multi-Tenant SaaS", "Same core strategy engine, different deployment scale.")
    add_cards(
        draw,
        [
            ("Local Mode", ["SQLite", "API key auth", "Single-user operations"]),
            ("SaaS Mode", ["Postgres", "Supabase JWT auth", "Celery + Redis workers"]),
            ("Ops Layer", ["Stripe billing path", "Dashboard workflows", "Validation artifacts"]),
        ],
    )
    add_footer(draw, "Ship local fast, then scale without rewriting the core logic.")
    img.save(OUT_DIR / "tweet-visual-05-local-to-saas.png")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    make_visual_1()
    make_visual_2()
    make_visual_3()
    make_visual_4()
    make_visual_5()
    print(f"Created 5 visuals in: {OUT_DIR}")


if __name__ == "__main__":
    main()

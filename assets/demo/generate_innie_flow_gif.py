from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "innie-flow.gif"

W, H = 960, 540
SCALE = 2

INK = "#05070A"
PANEL = "#0B111A"
PANEL_2 = "#101827"
ICE = "#EEF6FF"
MUTED = "#91A4BB"
BLUE = "#93C5FD"
CYAN = "#7DD3FC"
GREEN = "#7CE0B4"
LINE = "#263446"
WHITE = "#FFFFFF"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Helvetica.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return ImageFont.truetype(path, size * SCALE)
    return ImageFont.load_default()


FONT_TITLE = font(28, True)
FONT_H = font(20, True)
FONT = font(16)
FONT_SM = font(13)
FONT_CODE = font(14)


def xy(v: float) -> int:
    return round(v * SCALE)


def box(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], fill: str, outline: str = LINE, radius: int = 18, width: int = 2) -> None:
    draw.rounded_rectangle(tuple(xy(v) for v in rect), radius=xy(radius), fill=fill, outline=outline, width=xy(width))


def text(draw: ImageDraw.ImageDraw, pos: tuple[int, int], value: str, fill: str = ICE, fnt: ImageFont.ImageFont = FONT, anchor: str | None = None) -> None:
    draw.text((xy(pos[0]), xy(pos[1])), value, fill=fill, font=fnt, anchor=anchor)


def line(draw: ImageDraw.ImageDraw, points: list[tuple[int, int]], fill: str = LINE, width: int = 4) -> None:
    draw.line([(xy(x), xy(y)) for x, y in points], fill=fill, width=xy(width), joint="curve")


def arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], fill: str = LINE, width: int = 4) -> None:
    line(draw, [start, end], fill, width)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    size = 13
    pts = []
    for delta in (math.pi * 0.82, -math.pi * 0.82):
        pts.append((end[0] + math.cos(angle + delta) * size, end[1] + math.sin(angle + delta) * size))
    draw.polygon([(xy(end[0]), xy(end[1])), (xy(pts[0][0]), xy(pts[0][1])), (xy(pts[1][0]), xy(pts[1][1]))], fill=fill)


def double_arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], fill: str = LINE, width: int = 4) -> None:
    arrow(draw, start, end, fill, width)
    arrow(draw, end, start, fill, width)


def active(draw: ImageDraw.ImageDraw, center: tuple[int, int], t: float, color: str = CYAN) -> None:
    pulse = 8 + 5 * math.sin(t * math.pi)
    draw.ellipse((xy(center[0] - pulse), xy(center[1] - pulse), xy(center[0] + pulse), xy(center[1] + pulse)), outline=color, width=xy(3))
    draw.ellipse((xy(center[0] - 5), xy(center[1] - 5), xy(center[0] + 5), xy(center[1] + 5)), fill=color)


def phone(draw: ImageDraw.ImageDraw, on: bool) -> None:
    text(draw, (130, 136), "Outie", WHITE if on else MUTED, FONT_H, anchor="mm")
    box(draw, (54, 158, 206, 438), fill="#0E1724", outline=BLUE if on else LINE, radius=28, width=3 if on else 2)
    draw.rounded_rectangle((xy(100), xy(172), xy(160), xy(180)), radius=xy(4), fill="#1E2A3A")
    text(draw, (130, 210), "Slack", WHITE, FONT_H, anchor="mm")
    box(draw, (76, 242, 184, 303), fill="#162235", outline="#243247", radius=14)
    text(draw, (91, 258), "innie", BLUE, FONT_SM)
    text(draw, (91, 279), "triage issue #42", ICE, FONT_SM)
    box(draw, (70, 322, 190, 388), fill="#1C2D22" if on else "#162235", outline="#2B4332" if on else "#243247", radius=14)
    text(draw, (91, 341), "reply", GREEN if on else MUTED, FONT_SM)
    text(draw, (91, 364), "fix ready", ICE if on else MUTED, FONT_SM)


def dev_environment(draw: ImageDraw.ImageDraw, on: bool, resources_on: bool) -> None:
    box(draw, (304, 128, 892, 434), fill=PANEL_2 if on else PANEL, outline=CYAN if on else LINE, radius=24, width=3 if on else 2)
    text(draw, (330, 160), "Your dev environment", WHITE, FONT_H)
    text(draw, (330, 185), "local or cloud", MUTED, FONT_SM)

    box(draw, (330, 212, 562, 292), fill="#101C2D" if on else "#111923", outline="#25445F" if on else "#243247", radius=16)
    text(draw, (350, 236), "Innie", WHITE, FONT_H)
    text(draw, (350, 261), "durable session, queue, progress", MUTED, FONT_SM)

    box(draw, (596, 202, 864, 408), fill="#0E1724" if resources_on else "#0D141F", outline="#25445F" if resources_on else "#243247", radius=16, width=3 if resources_on else 2)
    text(draw, (616, 228), "Workspace access", WHITE if resources_on else MUTED, FONT_H)
    text(draw, (616, 252), "available to coding agents", MUTED, FONT_SM)

    items = [("repo", "code + tests"), ("skills", "runbooks"), ("MCP", "tools + data"), ("logs", "observability")]
    x0 = 616
    y = 270
    for i, (head, body) in enumerate(items):
        row_on = resources_on
        box(draw, (x0, y, 844, y + 28), fill="#132336" if row_on else "#111923", outline="#25445F" if row_on else "#243247", radius=8)
        text(draw, (x0 + 16, y + 16), head, CYAN if row_on else MUTED, FONT_SM)
        text(draw, (x0 + 86, y + 16), body, ICE if row_on else MUTED, FONT_SM)
        y += 32


def code_panel(draw: ImageDraw.ImageDraw, on: bool, progress: float) -> None:
    box(draw, (330, 318, 562, 408), fill="#0E1522", outline=BLUE if on else LINE, radius=16, width=3 if on else 2)
    text(draw, (350, 342), "Codex / Claude", WHITE, FONT_H)
    status = "triage -> code -> checks"
    text(draw, (350, 370), status, GREEN if on else MUTED, FONT_SM)
    if on:
        draw.rounded_rectangle((xy(350), xy(397), xy(542), xy(404)), radius=xy(4), fill="#172235")
        bar_w = 192 * min(1, progress)
        draw.rounded_rectangle((xy(350), xy(397), xy(350 + bar_w), xy(404)), radius=xy(4), fill=CYAN)


def draw_mark(draw: ImageDraw.ImageDraw, x: int, y: int, s: float) -> None:
    def sx(v: float) -> int:
        return xy(x + v * s)

    def sy(v: float) -> int:
        return xy(y + v * s)

    draw.rounded_rectangle((sx(34), sy(34), sx(156), sy(156)), radius=xy(18 * s), fill=INK)
    slash = [(88, 48), (102, 48), (102, 144), (88, 144)]
    cx, cy = 95, 96
    angle = math.radians(38)
    rotated = []
    for px, py in slash:
        dx, dy = px - cx, py - cy
        rotated.append((sx(cx + dx * math.cos(angle) - dy * math.sin(angle)), sy(cy + dx * math.sin(angle) + dy * math.cos(angle))))
    draw.polygon(rotated, fill=ICE)
    draw.ellipse((sx(50), sy(50), sx(82), sy(82)), fill=ICE)
    draw.ellipse((sx(106), sy(106), sx(142), sy(142)), fill=BLUE)
    draw.ellipse((sx(119), sy(119), sx(129), sy(129)), fill=INK)


def frame(idx: int, total: int) -> Image.Image:
    im = Image.new("RGB", (W * SCALE, H * SCALE), INK)
    draw = ImageDraw.Draw(im)
    draw.rounded_rectangle((xy(20), xy(20), xy(W - 20), xy(H - 20)), radius=xy(28), fill="#070B11", outline="#182131", width=xy(2))
    draw_mark(draw, 34, 32, 0.36)
    text(draw, (110, 55), "Innie runs work from Slack", WHITE, FONT_TITLE)
    text(draw, (110, 87), "same workspace access through skills, MCPs, tools, and repo resources", MUTED, FONT)

    phase = idx / max(1, total - 1)
    phone_on = phase < 0.24 or phase > 0.82
    env_on = 0.16 < phase < 0.88
    resources_on = 0.32 < phase < 0.82
    work_on = 0.60 < phase < 0.88

    phone(draw, phone_on)
    dev_environment(draw, env_on, resources_on)
    code_panel(draw, work_on, max(0, min(1, (phase - 0.64) / 0.22)))

    arrow(draw, (210, 290), (304, 290), CYAN if 0.12 < phase < 0.34 else LINE, 4)
    arrow(draw, (446, 292), (446, 318), GREEN if 0.30 < phase < 0.56 else LINE, 4)
    double_arrow(draw, (562, 364), (596, 330), GREEN if 0.46 < phase < 0.78 else LINE, 4)
    arrow(draw, (330, 382), (210, 360), BLUE if phase > 0.78 else LINE, 4)

    if 0.08 < phase < 0.30:
        p = (phase - 0.08) / 0.22
        active(draw, (210 + 94 * p, 290), p, CYAN)
    elif 0.32 < phase < 0.50:
        p = (phase - 0.32) / 0.18
        active(draw, (446, 292 + 26 * p), p, GREEN)
    elif 0.52 < phase < 0.70:
        p = (phase - 0.52) / 0.18
        active(draw, (562 + 34 * p, 364 - 34 * p), p, GREEN)
    elif phase > 0.80:
        p = min(1, (phase - 0.80) / 0.18)
        active(draw, (330 - 120 * p, 382 - 22 * p), p, BLUE)

    caption = "Slack trigger"
    if phase > 0.22:
        caption = "Innie starts in your dev environment"
    if phase > 0.40:
        caption = "Codex / Claude can access repo, skills, MCPs, and logs"
    if phase > 0.64:
        caption = "Coding agent triages the issue and writes code"
    if phase > 0.82:
        caption = "Result is returned to the Slack thread"
    text(draw, (480, 502), caption, ICE, FONT_H, anchor="mm")

    return im.resize((W, H), Image.Resampling.LANCZOS)


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    frames = [frame(i, 44) for i in range(44)]
    frames[0].save(
        OUT,
        save_all=True,
        append_images=frames[1:],
        duration=165,
        loop=0,
        optimize=True,
        disposal=2,
    )
    print(OUT)


if __name__ == "__main__":
    main()

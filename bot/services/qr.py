"""Shared GSA-branded QR-code generation.

Used by both the Discord `/qrcode` cog and the Telegram `/qrcode` command, so the
output is identical on both platforms. Pure: text in, PNG bytes out — no platform
or discord/telegram imports here.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_LOGO_PATH = Path(__file__).resolve().parent.parent / "data" / "white-logo-gsa.png"
NJIT_RED = (204, 0, 0)
BLACK = (0, 0, 0)
MAX_QR_INPUT = 500


def _crop_logo():
    """Load and crop the GSA logo to its non-transparent bounding box."""
    from PIL import Image
    img = Image.open(_LOGO_PATH).convert("RGBA")
    bbox = img.split()[3].getbbox()
    return img.crop(bbox) if bbox else img


def build_qr(data: str, *, transparent: bool, dot_color: tuple) -> bytes:
    """Render a branded QR PNG for ``data``. ``transparent`` drops the white
    background (for pasting onto designs); otherwise it's flattened onto white."""
    import qrcode
    from qrcode.image.styledpil import StyledPilImage
    from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer
    from qrcode.image.styles.colormasks import SolidFillColorMask
    from PIL import Image, ImageDraw

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)

    img: Image.Image = qr.make_image(
        image_factory=StyledPilImage,
        module_drawer=RoundedModuleDrawer(),
        color_mask=SolidFillColorMask(front_color=dot_color, back_color=(255, 255, 255)),
    ).convert("RGBA")

    qr_w, qr_h = img.size

    # Embed the GSA logo in the center.
    try:
        logo = _crop_logo()
        logo_max = int(qr_w * 0.22)
        logo.thumbnail((logo_max, logo_max), Image.LANCZOS)
        logo_w, logo_h = logo.size
        cx, cy = qr_w // 2, qr_h // 2
        r = max(logo_w, logo_h) // 2 + 10
        circle = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(circle).ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*dot_color, 255))
        img = Image.alpha_composite(img, circle)
        img.paste(logo, (cx - logo_w // 2, cy - logo_h // 2), logo)
    except Exception:  # noqa: BLE001 - logo is decorative; never fail the QR for it
        logger.warning("Failed to embed GSA logo in QR code — continuing without it")

    if transparent:
        img.putdata([
            (r, g, b, 0) if r > 240 and g > 240 and b > 240 else (r, g, b, a)
            for r, g, b, a in img.getdata()
        ])
    else:
        canvas = Image.new("RGBA", (qr_w, qr_h), (255, 255, 255, 255))
        canvas.paste(img, (0, 0), img)
        img = canvas

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


def build_pair(content: str, *, black: bool = False) -> tuple[bytes, bytes]:
    """Return ``(branded_png, transparent_png)`` for ``content``."""
    color = BLACK if black else NJIT_RED
    return (build_qr(content, transparent=False, dot_color=color),
            build_qr(content, transparent=True, dot_color=color))

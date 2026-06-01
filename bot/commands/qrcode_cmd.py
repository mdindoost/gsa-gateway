"""Slash command: /qrcode — generate a branded GSA NJIT QR code."""

import io
import logging
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

_LOGO_PATH = Path(__file__).parent.parent / "data" / "white-logo-gsa.png"
_NJIT_RED = (204, 0, 0)
_BLACK = (0, 0, 0)
_MAX_QR_INPUT = 500


def _crop_logo():
    """Load and crop the GSA logo to its non-transparent bounding box."""
    from PIL import Image
    img = Image.open(_LOGO_PATH).convert("RGBA")
    bbox = img.split()[3].getbbox()
    return img.crop(bbox) if bbox else img



def _build_qr(data: str, transparent: bool, dot_color: tuple) -> bytes:
    import qrcode
    from qrcode.image.styledpil import StyledPilImage
    from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer
    from qrcode.image.styles.colormasks import SolidFillColorMask
    from PIL import Image, ImageDraw

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img: Image.Image = qr.make_image(
        image_factory=StyledPilImage,
        module_drawer=RoundedModuleDrawer(),
        color_mask=SolidFillColorMask(
            front_color=dot_color,
            back_color=(255, 255, 255),
        ),
    ).convert("RGBA")

    qr_w, qr_h = img.size

    # ── Embed GSA logo in center ─────────────────────────────────────────────
    try:
        logo = _crop_logo()
        logo_max = int(qr_w * 0.22)
        logo.thumbnail((logo_max, logo_max), Image.LANCZOS)
        logo_w, logo_h = logo.size

        cx, cy = qr_w // 2, qr_h // 2
        r = max(logo_w, logo_h) // 2 + 10

        circle_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(circle_layer).ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            fill=(*dot_color, 255),
        )
        img = Image.alpha_composite(img, circle_layer)
        img.paste(logo, (cx - logo_w // 2, cy - logo_h // 2), logo)
    except Exception:
        logger.warning("Failed to embed GSA logo in QR code — continuing without it")

    # ── Transparent variant ──────────────────────────────────────────────────
    if transparent:
        pixels = list(img.getdata())
        pixels = [
            (r, g, b, 0) if r > 240 and g > 240 and b > 240 else (r, g, b, a)
            for r, g, b, a in pixels
        ]
        img.putdata(pixels)
    else:
        # ── Branded variant: flatten onto white background ────────────────────
        canvas = Image.new("RGBA", (qr_w, qr_h), (255, 255, 255, 255))
        canvas.paste(img, (0, 0), img)
        img = canvas

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


class QRCodeCog(commands.Cog, name="QRCode"):
    """Handles /qrcode — generates branded GSA NJIT QR codes."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="qrcode",
        description="Generate a branded GSA NJIT QR code for any URL or text.",
    )
    @app_commands.describe(
        content="The URL or text to encode (max 500 characters).",
        style="Color style — Red & White (default) or Black & White.",
    )
    @app_commands.choices(style=[
        app_commands.Choice(name="Red & White (default)", value="red"),
        app_commands.Choice(name="Black & White", value="black"),
    ])
    async def qrcode(
        self,
        interaction: discord.Interaction,
        content: str,
        style: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        content = content.strip()

        if not content:
            await interaction.response.send_message(
                "Please provide a URL or text to encode.", ephemeral=True
            )
            return

        if len(content) > _MAX_QR_INPUT:
            await interaction.response.send_message(
                f"Input too long — QR codes work best under {_MAX_QR_INPUT} characters "
                f"(yours is {len(content)}).",
                ephemeral=True,
            )
            return

        if not self.bot.rate_limiter.is_allowed(interaction.user.id):  # type: ignore[attr-defined]
            retry = self.bot.rate_limiter.get_retry_after(interaction.user.id)  # type: ignore[attr-defined]
            await interaction.response.send_message(
                f"Slow down a bit! Try again in **{retry:.0f}s**.", ephemeral=True
            )
            return

        dot_color = _BLACK if (style and style.value == "black") else _NJIT_RED
        style_label = "Black & White" if dot_color == _BLACK else "Red & White"
        embed_color = discord.Color.dark_gray() if dot_color == _BLACK else discord.Color.from_str("#CC0000")

        await interaction.response.defer()

        try:
            branded_bytes = _build_qr(content, transparent=False, dot_color=dot_color)
            transparent_bytes = _build_qr(content, transparent=True, dot_color=dot_color)
        except Exception:
            logger.exception("QR generation failed for content=%r", content[:60])
            await interaction.followup.send(
                "Something went wrong generating the QR code. Please try again.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"QR Code Generated — {style_label}",
            description=f"Encoded: `{content[:80]}{'...' if len(content) > 80 else ''}`",
            color=embed_color,
        )
        embed.add_field(
            name="Files included",
            value="**qr_branded.png** — white background, GSA branding\n"
                  "**qr_transparent.png** — no background, paste onto any design",
            inline=False,
        )
        embed.set_footer(text="GSA Gateway · paste the transparent version onto flyers & slides")

        await interaction.followup.send(
            embed=embed,
            files=[
                discord.File(io.BytesIO(branded_bytes), filename="qr_branded.png"),
                discord.File(io.BytesIO(transparent_bytes), filename="qr_transparent.png"),
            ],
        )
        logger.info(
            "QR code generated by %s (ID=%d) style=%s content=%r",
            interaction.user.name,
            interaction.user.id,
            style_label,
            content[:60],
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QRCodeCog(bot))

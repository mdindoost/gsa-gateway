"""Asset layer — copy the shared stylesheet + self-hosted fonts into the output tree."""
import os
import shutil

_SRC = os.path.join(os.path.dirname(__file__), "assets")


def copy_assets(out_root: str) -> None:
    dst = os.path.join(out_root, "assets")
    os.makedirs(os.path.join(dst, "fonts"), exist_ok=True)
    shutil.copy2(os.path.join(_SRC, "style.css"), os.path.join(dst, "style.css"))
    src_fonts = os.path.join(_SRC, "fonts")
    for fn in sorted(os.listdir(src_fonts)):
        if fn.endswith(".woff2"):
            shutil.copy2(os.path.join(src_fonts, fn), os.path.join(dst, "fonts", fn))

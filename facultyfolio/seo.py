"""SEO artifacts — sitemap.xml + robots.txt. All URLs absolute (config.SITE_ORIGIN)."""
from xml.sax.saxutils import escape

from . import config


def sitemap_xml(abs_urls: list) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in abs_urls:
        lines.append(f"  <url><loc>{escape(u)}</loc></url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def robots_txt() -> str:
    return ("User-agent: *\n"
            "Allow: /\n"
            f"Sitemap: {config.SITE_ORIGIN}/sitemap.xml\n")

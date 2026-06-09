"""Signature rendering.

Every post carries a signature. ``NULL`` on the post means "use the org default"
(``signature.default`` setting); a non-null value is used verbatim. Either way the
template's ``{var}`` placeholders are filled from the ``signature.variables``
setting (e.g. ``{org_name}``, ``{website}``, ``{short_name}``). Unknown
placeholders are left intact rather than raising.
"""

from __future__ import annotations

import re

from v2.core.database.queries import get_setting, get_setting_typed

_VAR = re.compile(r"\{(\w+)\}")


class SignatureService:
    def __init__(self, conn):
        self.conn = conn

    def variables(self, org_id: int) -> dict:
        return get_setting_typed(self.conn, org_id, "signature.variables", {}) or {}

    def default_template(self, org_id: int) -> str:
        return get_setting(self.conn, org_id, "signature.default", "") or ""

    def render(self, org_id: int, template: str | None = None) -> str:
        """Render ``template`` (or the org default if None) with the org's vars."""
        tmpl = self.default_template(org_id) if template is None else template
        if not tmpl:
            return ""
        variables = self.variables(org_id)
        return _VAR.sub(lambda m: str(variables.get(m.group(1), m.group(0))), tmpl)

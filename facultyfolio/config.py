"""FacultyFolio configuration — paths, ids, fixed copy, suppression hook.

Everything the generator needs to know that is not derivable from the KG.
Read-only against the DB; nothing here writes.
"""
import os

# --- repo / DB ---------------------------------------------------------------
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("FACULTYFOLIO_DB", os.path.join(_REPO, "gsa_gateway.db"))

# --- output tree (separate from repo code; becomes the public Pages repo) ----
OUT_ROOT = os.environ.get(
    "FACULTYFOLIO_OUT", os.path.join(os.path.dirname(_REPO), "Faculty-Folio")
)

# --- knowledge-graph anchors -------------------------------------------------
CS_ORG_ID = 16          # Computer Science Org node
KOUTIS_NODE = 33        # golden test faculty

# --- assistant brand/version (single source of truth: bot/core/identity.py) --
# So the footer's "GSA Gateway · Kavosh vX" tracks the one place the version lives — a version
# bump there re-renders here, no hardcoded copy to drift (was frozen at "v2.1").
from bot.core import identity as _identity
ASSISTANT_VERSION = _identity.version_label()          # e.g. "Kavosh v2.5"

# --- fixed copy (never data-driven — trust boundary, spec §3.2) --------------
FIXED_HEADING = "Impact & trajectory"          # Scholarly-activity section heading, all faculty
ACTIVE_SINCE_LABEL = "Active since"            # 4th stat (NOT "Publishing since" — honesty, spec §4)
ABOUT_SOURCE = "Crawled from the NJIT department profile · not written or generated"
NJIT_PROFILE_TMPL = "https://people.njit.edu/profile/{slug}"

# --- college display names (identity proper-nouns, NOT content curation) ------
# The KG stores colleges by acronym ("YWCC"); the reference design shows the full
# name. This is a closed set of ~6 NJIT college proper-nouns (identity display),
# distinct from the forbidden venue/course meaning-dictionaries. Fallback = node name.
# FLAG: confirm with owner at the Koutis-review checkpoint.
COLLEGE_NAMES = {
    "YWCC": "Ying Wu College of Computing",
    "NCE": "Newark College of Engineering",
    "CSLA": "College of Science and Liberal Arts",
    "MTSM": "Martin Tuchman School of Management",
    "HCAD": "Hillier College of Architecture and Design",
}

# --- visibility hook (default publish; a slug here is never emitted) ----------
SUPPRESSED: set = set()


def sync_label(updated_at: str) -> str:
    """'2026-06-30' -> 'Synced 30 Jun 2026'. Empty/None -> '' (no Scholar)."""
    if not updated_at:
        return ""
    import datetime
    try:
        d = datetime.date.fromisoformat(updated_at[:10])
    except ValueError:
        return ""
    return f"Synced {d.day} {d.strftime('%b')} {d.year}"

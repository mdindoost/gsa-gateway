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

# --- fixed copy (never data-driven — trust boundary, spec §3.2) --------------
FIXED_HEADING = "Impact & trajectory"          # Scholarly-activity section heading, all faculty
ACTIVE_SINCE_LABEL = "Active since"            # 4th stat (NOT "Publishing since" — honesty, spec §4)
ABOUT_SOURCE = "Crawled from the NJIT department profile · not written or generated"
NJIT_PROFILE_TMPL = "https://people.njit.edu/profile/{slug}"

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

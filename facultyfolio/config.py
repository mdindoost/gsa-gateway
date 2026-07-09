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
CS_ORG_ID = 16          # Computer Science Org node (named convenience anchor; build discovers depts)
KOUTIS_NODE = 33        # golden test faculty

# YWCC college anchored by SLUG (node ids renumber on `run_explore.py --reset`; slugs don't).
COLLEGE_SLUG = "ywcc"
# Old URL segment -> new segment. Preserves a previously-shared URL after the slug move
# (URL-migration continuity, NOT per-dept vocabulary). A stub redirect is written per entry.
LEGACY_REDIRECTS = {"cs": "computer-science"}

# --- assistant brand/version (single source of truth: bot/core/identity.py) --
# So the footer's "GSA Gateway · Kavosh vX" tracks the one place the version lives — a version
# bump there re-renders here, no hardcoded copy to drift (was frozen at "v2.1").
from bot.core import identity as _identity
ASSISTANT_VERSION = _identity.version_label()          # e.g. "Kavosh v2.5"

# --- fixed copy (never data-driven — trust boundary, spec §3.2) --------------
FIXED_HEADING = "Impact & trajectory"          # Scholarly-activity section heading, all faculty
ACTIVE_SINCE_LABEL = "Active since"            # 4th stat (NOT "Publishing since" — honesty, spec §4)
ABOUT_SOURCE = "Crawled from the NJIT department profile · not written or generated"
ABOUT_EMPTY_LABEL = "Not listed"               # Fixed-mode placeholder for a missing about-row
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

# --- display-mode flags ------------------------------------------------------
# Each configurable page component renders in one of two modes:
#   "Adaptive" = the original behavior — show a thing only when its data exists.
#   "Fixed"    = show the FULL set on every page; gray the ones the person lacks.
# The current code is always the "Adaptive" option (never deleted). Defaults are
# decided one component at a time; only SOCIAL_ICONS is Fixed so far — the rest
# default Adaptive so adding a flag is a zero-visual-change commit until it's flipped.
# Override any flag at build time with FACULTYFOLIO_<NAME> (e.g. FACULTYFOLIO_NAV=Fixed).
# (LEADERBOARD_ROSTER retired — the multi-view leaderboard always shows the full roster,
#  so the old "gray the missing" toggle no longer has meaning.)
_FLAG_DEFAULTS = {
    "SOCIAL_ICONS": "Fixed",
    "ABOUT_ROWS": "Fixed",
    "SCHOLAR_METRICS": "Adaptive",
    "PUBLICATIONS": "Adaptive",
    "NAV": "Adaptive",
}
_FLAG_VALUES = ("Fixed", "Adaptive")


def flag(name: str) -> str:
    """Resolve a display-mode flag: env override FACULTYFOLIO_<NAME> else default.
    Raises KeyError for an unknown flag, ValueError for an invalid value."""
    if name not in _FLAG_DEFAULTS:
        raise KeyError(f"unknown display flag {name!r}")
    val = os.environ.get(f"FACULTYFOLIO_{name}", _FLAG_DEFAULTS[name])
    if val not in _FLAG_VALUES:
        raise ValueError(f"flag {name}={val!r} must be one of {_FLAG_VALUES}")
    return val


# --- academic rank ladder (closed ordinal scale, like COLLEGE_NAMES — not curation) --
# Seniority order for the leaderboard's "By rank/title" view. Chair heads the unit; a Dean maps
# to Professor (see rank.rank_of); rank-less titles fall to a "Faculty" catch-all (index past the
# ladder). The substring-safe professorial match order is DERIVED in rank.py (longest-first).
RANK_LADDER = [
    "Department Chair",
    "Distinguished Professor",
    "Professor",
    "Associate Professor",
    "Assistant Professor",
    "Senior University Lecturer",
    "University Lecturer",
]
LEADERBOARD_VIEWS = ("rank", "citations", "az")
LEADERBOARD_DEFAULT_VIEW = "rank"              # must be one of LEADERBOARD_VIEWS

# --- per-person photo overrides (mechanism (c); drop-in file also supported) --
# Pin a specific photo for a person. Value is one of: "njit" (force the NJIT card),
# "scholar" (force Google Scholar), a URL (fetch exactly that), or a local file path.
# A drop-in image at assets/photos_manual/<slug>.<ext> beats this map. Empty = pure
# NJIT-first auto. Override wins over cache + auto order.
PHOTO_OVERRIDES: dict = {}

# --- visibility hook (default publish; a slug here is never emitted) ----------
SUPPRESSED: set = set()

# --- citation-momentum (★ Rising view) — named constants, no magic numbers -----
# Window = the MOMENTUM_WINDOW most recent COMPLETE years (the person's Scholar sync
# year is partial and excluded). A person passes the data gate iff all window years are
# present AND the window median citations/yr clears MOMENTUM_FLOOR. A latest-year count
# below MOMENTUM_TINY_BASE renders a "▲ growing" glyph instead of a precise % (a huge %
# on a tiny base looks like a magnitude claim it isn't). See the 2026-07-08 design spec.
MOMENTUM_WINDOW = 5
MOMENTUM_FLOOR = 10
MOMENTUM_TINY_BASE = 25


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

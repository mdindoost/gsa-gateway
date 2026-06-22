import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
import bot.config as botcfg


def test_office_threshold_present_and_defaults_to_live_threshold():
    assert isinstance(botcfg.OFFICE_THRESHOLD, float)
    assert botcfg.OFFICE_THRESHOLD == botcfg.LIVE_THRESHOLD   # default: same floor as live

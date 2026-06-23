import sys
import asyncio
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.connectors.base import DeliveryResult
from v2.core.publishing.scheduler import Scheduler


def _run(c):
    return asyncio.get_event_loop().run_until_complete(c)


class _Pub:
    async def publish_due(self, now):
        return {"published": 0, "sent": 0, "failed": 0}


class _Reg:
    async def delete_delivery(self, platform, channel, message_id):
        return DeliveryResult(True, platform, message_id=message_id)


def test_tick_runs_delete_due():
    conn = create_all(":memory:")
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'N','njit','university')")
    conn.execute("INSERT INTO posts(id,org_id,type,content,channels,status,delete_at) "
                 "VALUES(1,1,'worldcup','x','[\"discord\"]','sent','2000-01-01 00:00:00')")
    conn.execute("INSERT INTO post_deliveries(id,post_id,platform,channel,message_id,status) "
                 "VALUES(1,1,'discord','c','999','success')")
    conn.commit()
    sch = Scheduler(conn, _Pub(), registry=_Reg())
    out = _run(sch.tick())
    assert out["deleted"] == 1
    conn.close()


def test_tick_without_registry_still_works():
    conn = create_all(":memory:")
    out = _run(Scheduler(conn, _Pub()).tick())   # no registry → no deletion, no crash
    assert out.get("deleted", 0) == 0
    conn.close()

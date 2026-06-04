import pytest
from bot.connectors.base import BasePlatform


def test_base_platform_cannot_be_instantiated():
    with pytest.raises(TypeError):
        BasePlatform()


def test_subclass_without_all_methods_raises():
    class Incomplete(BasePlatform):
        async def start(self): pass
        # missing stop() and setup_services()

    with pytest.raises(TypeError):
        Incomplete()


def test_complete_subclass_can_be_instantiated():
    class Complete(BasePlatform):
        async def start(self): pass
        async def stop(self): pass
        async def setup_services(self): pass

    obj = Complete()
    assert obj is not None

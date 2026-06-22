import numpy as np
from v2.eval.router.encode import FakeEncoder, ROUTER_PREFIX


def test_fake_encoder_normalized_and_deterministic():
    enc = FakeEncoder(dim=8)
    a = enc(["who is the dean"]); b = enc(["who is the dean"])
    assert a.shape == (1, 8)
    assert np.allclose(np.linalg.norm(a, axis=1), 1.0)
    assert np.allclose(a, b)                    # deterministic


def test_prefix_constant():
    assert ROUTER_PREFIX.endswith(": ")

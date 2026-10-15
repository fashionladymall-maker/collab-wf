"""Temporary registered, approved projects for legacy direct-handler tests."""
from functools import wraps
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def register(inst):
    from . import core, policy
    path = inst.wf / "policy.json"
    path.write_text("{}", encoding="utf-8")
    policy.approve(inst, "policy", "synthetic direct-handler fixture")
    registry = json.loads(core.REGISTRY.read_text(encoding="utf-8"))
    registry["projects"].append({"name": inst.name, "root": str(inst.root)})
    core.REGISTRY.write_text(json.dumps(registry), encoding="utf-8")
    return inst


def send_context(inst, pol, deadline):
    from . import driver, policy
    path = inst.wf / "policy.json"
    content = json.dumps(pol)
    if path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")
        policy.approve(inst, "policy", "synthetic handler policy")
    return driver.Ctx(inst, pol, deadline)


def send_host(factory=None):
    """Scope only fixture dependencies; never mock the permission decision."""
    def decorate(fn):
        @wraps(fn)
        def run(*args, **kwargs):
            from . import core
            with tempfile.TemporaryDirectory(prefix="wf-send-fixture-host-") as td:
                registry = Path(td) / "registry.json"
                registry.write_text('{"projects":[]}', encoding="utf-8")
                with patch.object(core, "REGISTRY", registry):
                    if factory is None:
                        return fn(*args, **kwargs)
                    module = sys.modules[fn.__module__]
                    original = getattr(module, factory)
                    def create(*a, **kw):
                        return register(original(*a, **kw))
                    with patch.object(module, factory, create):
                        return fn(*args, **kwargs)
        return run
    return decorate

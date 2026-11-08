"""The DB session must be committed before the response leaves (no stale reads right after a write)."""

from fastapi.routing import APIRoute

from app.db import get_session
from app.main import app


def _walk(dep):
    yield dep
    for d in dep.dependencies:
        yield from _walk(d)


def _routes(routes):
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        elif (sub := getattr(r, "original_router", None)) is not None:  # FastAPI >= 0.14x wraps included routers
            yield from _routes(sub.routes)


def test_db_session_is_function_scoped() -> None:
    seen = 0
    for route in _routes(app.routes):
        for d in _walk(route.dependant):
            if d.call is get_session:
                seen += 1
                assert d.scope == "function", f"{route.path}: Depends(get_session) without scope='function'"
    assert seen > 10

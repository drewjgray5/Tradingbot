from __future__ import annotations

from webapp.tasks import celery_app


def test_celery_task_routes_isolate_phase2_queue() -> None:
    routes = dict(celery_app.conf.task_routes or {})
    assert routes.get("webapp.scan_for_user", {}).get("queue") == "scan"
    assert routes.get("webapp.execute_order_for_user", {}).get("queue") == "orders"
    assert routes.get("webapp.phase2_stage1_for_user", {}).get("queue") == "phase2"


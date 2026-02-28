"""
test_routes.py
──────────────
集成测试：api/routes.py 的 HTTP 端点

使用 FastAPI 的 TestClient（基于 httpx），在内存里运行 ASGI app，
不需要真实启动服务器，也不需要 Redis。

Celery task 调用被 mock 掉，我们只测试：
  - HTTP 状态码是否正确
  - 请求/响应的数据结构是否符合 schema
  - 边界条件（query 太短、job_id 不存在等）
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from deep_research.main import app

# TestClient 在测试期间同步运行 FastAPI，不需要 uvicorn
client = TestClient(app, raise_server_exceptions=True)

VALID_QUERY = "How are AI coding tools expected to reshape junior developer hiring in North America by 2030?"


# ── POST /api/v1/generate ──────────────────────────────────────────────────────

class TestGenerateEndpoint:

    def _mock_task(self, job_id: str = "test-job-abc123"):
        """Helper: 创建一个假的 Celery AsyncResult。"""
        mock = MagicMock()
        mock.id = job_id
        return mock

    def test_returns_202_with_job_id(self):
        with patch("deep_research.api.routes.run_research_task.delay",
                   return_value=self._mock_task("job-001")):
            r = client.post("/api/v1/generate", json={"query": VALID_QUERY})

        assert r.status_code == 202
        body = r.json()
        assert body["job_id"] == "job-001"
        assert "message" in body

    def test_rejects_query_too_short(self):
        r = client.post("/api/v1/generate", json={"query": "too short"})
        assert r.status_code == 422  # Pydantic validation error

    def test_rejects_blank_query(self):
        r = client.post("/api/v1/generate", json={"query": "   "})
        assert r.status_code == 422

    def test_rejects_missing_query(self):
        r = client.post("/api/v1/generate", json={})
        assert r.status_code == 422

    def test_accepts_optional_email(self):
        with patch("deep_research.api.routes.run_research_task.delay",
                   return_value=self._mock_task()) as mock_delay:
            r = client.post("/api/v1/generate", json={
                "query": VALID_QUERY,
                "email": "user@example.com",
            })

        assert r.status_code == 202
        # email 应该被传给 Celery task
        call_kwargs = mock_delay.call_args.kwargs
        assert call_kwargs.get("email") == "user@example.com"

    def test_rejects_invalid_email(self):
        r = client.post("/api/v1/generate", json={
            "query": VALID_QUERY,
            "email": "not-an-email",
        })
        assert r.status_code == 422

    def test_accepts_custom_threshold_and_max_iter(self):
        with patch("deep_research.api.routes.run_research_task.delay",
                   return_value=self._mock_task()) as mock_delay:
            r = client.post("/api/v1/generate", json={
                "query": VALID_QUERY,
                "threshold": 7,
                "max_iter": 3,
            })

        assert r.status_code == 202
        call_kwargs = mock_delay.call_args.kwargs
        assert call_kwargs["threshold"] == 7
        assert call_kwargs["max_iter"] == 3

    def test_rejects_threshold_out_of_range(self):
        r = client.post("/api/v1/generate", json={
            "query": VALID_QUERY,
            "threshold": 11,   # max is 10
        })
        assert r.status_code == 422


# ── GET /api/v1/status/{job_id} ───────────────────────────────────────────────

class TestStatusEndpoint:

    def _patch_result(self, state: str, info=None, result=None):
        """Helper: mock Celery AsyncResult 的 state/info/result 属性。"""
        mock = MagicMock()
        mock.state = state
        mock.info = info
        mock.result = result
        return mock

    def test_pending_state(self):
        with patch("deep_research.api.routes.AsyncResult",
                   return_value=self._patch_result("PENDING")):
            r = client.get("/api/v1/status/some-job-id")

        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "PENDING"
        assert body["report"] is None

    def test_progress_state_includes_log(self):
        log = ["Planning searches…", "→ 5 searches planned"]
        with patch("deep_research.api.routes.AsyncResult",
                   return_value=self._patch_result("PROGRESS", info={"log": log})):
            r = client.get("/api/v1/status/some-job-id")

        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "PROGRESS"
        assert body["log"] == log

    def test_success_state_includes_report(self):
        task_result = {
            "report": "# Final Report\n\nContent here.",
            "email_sent": True,
            "log": ["Done"],
        }
        with patch("deep_research.api.routes.AsyncResult",
                   return_value=self._patch_result("SUCCESS", result=task_result)):
            r = client.get("/api/v1/status/some-job-id")

        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "SUCCESS"
        assert "Final Report" in body["report"]
        assert body["email_sent"] is True

    def test_failure_state_includes_error(self):
        with patch("deep_research.api.routes.AsyncResult",
                   return_value=self._patch_result("FAILURE",
                                                   result=Exception("API key invalid"))):
            r = client.get("/api/v1/status/some-job-id")

        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "FAILURE"
        assert "API key invalid" in body["error"]

    def test_revoked_state(self):
        with patch("deep_research.api.routes.AsyncResult",
                   return_value=self._patch_result("REVOKED")):
            r = client.get("/api/v1/status/some-job-id")

        assert r.status_code == 200
        assert r.json()["state"] == "REVOKED"


# ── DELETE /api/v1/cancel/{job_id} ────────────────────────────────────────────

class TestCancelEndpoint:

    def _patch_result(self, state: str):
        mock = MagicMock()
        mock.state = state
        mock.revoke = MagicMock()
        return mock

    def test_cancels_pending_task(self):
        mock_result = self._patch_result("PENDING")
        with patch("deep_research.api.routes.AsyncResult", return_value=mock_result):
            r = client.delete("/api/v1/cancel/some-job-id")

        assert r.status_code == 200
        assert r.json()["cancelled"] is True
        mock_result.revoke.assert_called_once_with(terminate=True)

    def test_cancels_running_task(self):
        mock_result = self._patch_result("PROGRESS")
        with patch("deep_research.api.routes.AsyncResult", return_value=mock_result):
            r = client.delete("/api/v1/cancel/some-job-id")

        assert r.status_code == 200
        mock_result.revoke.assert_called_once_with(terminate=True)

    def test_rejects_cancel_of_completed_task(self):
        with patch("deep_research.api.routes.AsyncResult",
                   return_value=self._patch_result("SUCCESS")):
            r = client.delete("/api/v1/cancel/some-job-id")

        assert r.status_code == 409  # Conflict

    def test_rejects_cancel_of_already_revoked_task(self):
        with patch("deep_research.api.routes.AsyncResult",
                   return_value=self._patch_result("REVOKED")):
            r = client.delete("/api/v1/cancel/some-job-id")

        assert r.status_code == 409


# ── Health check ───────────────────────────────────────────────────────────────

def test_health_endpoint():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

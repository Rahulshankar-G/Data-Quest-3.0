import os
import unittest
from unittest.mock import patch

import api
from starlette.requests import Request
from starlette.responses import Response


class ProductionApiTests(unittest.IsolatedAsyncioTestCase):
    def request(self, path: str, api_key: str = "") -> Request:
        headers = [(b"host", b"profitpilot.test")]
        if api_key:
            headers.append((b"x-api-key", api_key.encode()))
        return Request({
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "server": ("profitpilot.test", 80),
        })

    async def test_production_api_requires_configured_api_key(self):
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "PROFITPILOT_API_KEY": "",
        }):
            response = await api.protect_api(
                self.request("/api/integrations/status"),
                lambda _request: None,
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("at least 32 characters", response.body.decode())

    async def test_api_key_protects_data_routes_and_root_health_remains_public(self):
        api_key = "test-key-that-is-at-least-32-characters-long"
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "PROFITPILOT_API_KEY": api_key,
        }):
            unauthorized = await api.protect_api(
                self.request("/api/integrations/status"),
                lambda _request: None,
            )
            next_called = False

            async def call_next(_request):
                nonlocal next_called
                next_called = True
                return Response(status_code=200)

            authorized = await api.protect_api(
                self.request("/api/integrations/status", api_key),
                call_next,
            )
            health = await api.protect_api(self.request("/"), call_next)

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(health.status_code, 200)
        self.assertTrue(next_called)


if __name__ == "__main__":
    unittest.main()

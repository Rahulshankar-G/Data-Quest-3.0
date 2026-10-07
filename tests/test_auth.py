import unittest

from fastapi.testclient import TestClient

import api


class AuthApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(api.app)

    def test_valid_login_returns_session(self):
        response = self.client.post(
            "/api/auth/login",
            json={"email_or_username": "admin@example.com", "password": "Admin@123"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["user"]["email"], "admin@example.com")
        self.assertIn("token", payload)
        self.assertIn("expires_at", payload)

    def test_invalid_password_returns_generic_error(self):
        response = self.client.post(
            "/api/auth/login",
            json={"email_or_username": "admin@example.com", "password": "bad-password"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid email or password.")

    def test_invalid_email_returns_generic_error(self):
        response = self.client.post(
            "/api/auth/login",
            json={"email_or_username": "missing@example.com", "password": "Admin@123"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid email or password.")

    def test_empty_fields_are_rejected(self):
        response = self.client.post(
            "/api/auth/login",
            json={"email_or_username": "", "password": ""},
        )

        self.assertEqual(response.status_code, 422)

    def test_logout_invalidates_session(self):
        login = self.client.post(
            "/api/auth/login",
            json={"email_or_username": "user@example.com", "password": "User@123"},
        )
        token = login.json()["token"]

        logout = self.client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(logout.status_code, 200)
        self.assertEqual(logout.json()["detail"], "Logged out successfully.")

        me = self.client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(me.status_code, 401)


if __name__ == "__main__":
    unittest.main()

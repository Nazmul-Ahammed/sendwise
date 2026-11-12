import json
import os
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import email_provider, models, sendclean_client
from app.db import Base


class _Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class SendCleanClientTests(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_account_uses_documented_auth_body(self, urlopen):
        urlopen.return_value = _Response({"status": "success", "user_data": {"name": "Account"}})
        result = sendclean_client.account("owner", "secret")
        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode())
        self.assertTrue(request.full_url.endswith("/account/viewUserDetail"))
        self.assertEqual(body, {"owner_id": "owner", "token": "secret"})
        self.assertEqual(result["user_data"]["name"], "Account")

    @patch("urllib.request.urlopen")
    def test_application_error_is_raised(self, urlopen):
        urlopen.return_value = _Response({"status": "error", "name": "AuthenticationError",
                                          "message": "Token MissMatch"})
        with self.assertRaises(sendclean_client.SendCleanError):
            sendclean_client.account("owner", "bad")

    def test_base_url_is_the_documented_endpoint(self):
        self.assertEqual(sendclean_client.DEFAULT_BASE_URL, "https://api.sendclean.net/v1.0")


class ProviderIsolationTests(unittest.TestCase):
    def setUp(self):
        self.old_secret = os.environ.get("SECRET_KEY")
        os.environ["SECRET_KEY"] = "provider-test-secret"
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        if self.old_secret is None:
            os.environ.pop("SECRET_KEY", None)
        else:
            os.environ["SECRET_KEY"] = self.old_secret

    def _setting(self, key, value):
        self.db.add(models.Setting(key=key, value=value))
        self.db.commit()

    def _configure(self):
        self._setting("sendclean_owner_id", "70710839")
        self._setting("sendclean_api_token", "plain-token")
        self._setting("sendclean_smtp_user_name", "smtp123")

    def test_sendclean_is_the_only_selectable_provider(self):
        self.assertEqual(email_provider.selected_provider(self.db), "sendclean")
        self.assertEqual(email_provider.selected_provider(), "sendclean")
        self.assertEqual(list(email_provider.PROVIDERS), ["sendclean"])

    def test_mailchimp_only_capabilities_fail_closed(self):
        self._configure()
        for capability in ("audiences", "segments", "campaign_send", "recipient_events",
                           "aggregate_reports", "contact_lifecycle", "automations"):
            with self.assertRaises(email_provider.ProviderCapabilityError):
                email_provider.require_capability(self.db, capability)

    def test_sendclean_capabilities_are_available(self):
        self._configure()
        for capability in ("transactional_send", "message_lookup", "sending_domains",
                           "tracking_domains", "webhooks", "smtp_users"):
            self.assertEqual(email_provider.require_capability(self.db, capability), "sendclean")

    def test_unconfigured_credentials_fail_closed(self):
        with self.assertRaises(email_provider.ProviderCapabilityError):
            email_provider.require_capability(self.db, "transactional_send")

    def test_encrypted_token_preferred_over_plaintext(self):
        from app.llm_providers import encrypt_secret
        self._setting("sendclean_owner_id", "owner")
        self._setting("sendclean_api_token", "plain-token")
        self._setting("sendclean_api_token_enc", encrypt_secret("encrypted-token"))
        self.assertEqual(email_provider.credentials(self.db)["token"], "encrypted-token")

    def test_status_reports_no_marketing_campaigns(self):
        self._configure()
        status = email_provider.status(self.db)
        self.assertEqual(status["active_provider"], "sendclean")
        self.assertTrue(status["configured"])
        self.assertFalse(status["marketing_campaigns_supported"])


if __name__ == "__main__":
    unittest.main()

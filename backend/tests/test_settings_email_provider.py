import os
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.db import Base
from app.routes import SettingPatch, patch_setting
from app.seed import SETTINGS


class EmailProviderSettingsTests(unittest.TestCase):
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

    def test_defaults_are_valid_settings(self):
        self.assertIn("email_provider", SETTINGS)
        self.assertIn("sendclean_api_token", SETTINGS)
        self.assertEqual(SETTINGS["email_provider"], "sendclean")
        self.assertEqual(SETTINGS["sendclean_api_token"], "")

    def test_patch_persists_provider_and_token(self):
        patch_setting(SettingPatch(key="email_provider", value="sendclean"), db=self.db)
        patch_setting(SettingPatch(key="sendclean_api_token", value="token-123"), db=self.db)

        stored = {s.key: s.value for s in self.db.query(models.Setting).all()}
        self.assertEqual(stored["email_provider"], "sendclean")
        self.assertEqual(stored["sendclean_api_token"], "")
        self.assertTrue(stored["sendclean_api_token_enc"])

    def test_patch_upserts_sendclean_only_provider(self):
        patch_setting(SettingPatch(key="email_provider", value="sendclean"), db=self.db)
        self.db.commit()
        self.db.expire_all()
        stored = {s.key: s.value for s in self.db.query(models.Setting).all()}
        self.assertEqual(stored["email_provider"], "sendclean")


if __name__ == "__main__":
    unittest.main()

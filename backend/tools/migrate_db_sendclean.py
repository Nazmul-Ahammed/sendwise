"""Database cleanup for the SendClean-only migration.

Removes every Mailchimp setting row, renames the campaign provider column and
normalises provider configuration. Safe to run repeatedly.

Usage (from the backend directory, with DATABASE_URL pointing at the target DB):

    python tools/migrate_db_sendclean.py

Prints a summary only. Never prints secret values.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text  # noqa: E402

from app.db import engine  # noqa: E402

MAILCHIMP_SETTING_KEYS = [
    "mailchimp_api_key",
    "mailchimp_api_key_enc",
    "mailchimp_snapshot",
    "mailchimp_synced_at",
    "audience_id",
    "audience_name",
    "audience_count",
    "audience_sender_map",
]


def column_exists(inspector, table, column):
    try:
        return column in {c["name"] for c in inspector.get_columns(table)}
    except Exception:
        return False


def main():
    summary = []
    inspector = inspect(engine)

    with engine.begin() as conn:
        # 1. rename the Mailchimp-specific campaign column
        if column_exists(inspector, "campaigns", "mailchimp_campaign_id"):
            if column_exists(inspector, "campaigns", "provider_message_id"):
                conn.execute(text("UPDATE campaigns SET provider_message_id = mailchimp_campaign_id "
                                  "WHERE (provider_message_id IS NULL OR provider_message_id = '') "
                                  "AND mailchimp_campaign_id IS NOT NULL"))
                conn.execute(text("ALTER TABLE campaigns DROP COLUMN mailchimp_campaign_id"))
                summary.append("dropped duplicate campaigns.mailchimp_campaign_id")
            else:
                conn.execute(text("ALTER TABLE campaigns RENAME COLUMN mailchimp_campaign_id TO provider_message_id"))
                summary.append("renamed campaigns.mailchimp_campaign_id -> provider_message_id")
        elif column_exists(inspector, "campaigns", "provider_message_id"):
            summary.append("campaigns.provider_message_id already present")
        else:
            conn.execute(text("ALTER TABLE campaigns ADD COLUMN provider_message_id VARCHAR DEFAULT ''"))
            summary.append("added campaigns.provider_message_id")

        # 2. purge Mailchimp settings
        deleted = 0
        for key in MAILCHIMP_SETTING_KEYS:
            result = conn.execute(text("DELETE FROM settings WHERE key = :k"), {"k": key})
            deleted += result.rowcount or 0
        summary.append(f"deleted {deleted} Mailchimp setting row(s)")

        # 3. force the provider and clear the legacy plaintext token
        conn.execute(text(
            "INSERT INTO settings (key, value) VALUES ('email_provider', 'sendclean') "
            "ON CONFLICT (key) DO UPDATE SET value = 'sendclean'"
        ))
        conn.execute(text(
            "UPDATE settings SET value = '' WHERE key = 'sendclean_api_token' AND value <> ''"
        ))
        summary.append("email_provider forced to sendclean; plaintext token cleared")

        # 4. never leave a Mailchimp key in the environment-backed settings
        conn.execute(text("DELETE FROM settings WHERE key LIKE 'mailchimp%'"))

        # 5. per-recipient delivery/tracking column
        if not column_exists(inspect(engine), "campaigns", "delivery_json"):
            conn.execute(text("ALTER TABLE campaigns ADD COLUMN delivery_json JSON"))
            summary.append("added campaigns.delivery_json")
        else:
            summary.append("campaigns.delivery_json already present")

    print("SendClean DB migration complete:")
    for line in summary:
        print("  -", line)


if __name__ == "__main__":
    main()

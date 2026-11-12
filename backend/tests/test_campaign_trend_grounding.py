import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.copy_engine import generate as generate_email_copy, product_key as copy_product_key
from app.db import Base
from app.routes import generate_copy
from app.trend_intelligence import campaign_product_key, campaign_trend_context


class CampaignTrendGroundingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def campaign(self, product="Turbo Add-ons", count=3):
        rows = [{"stage": i, "ref": f"E{i}", "email_name": f"Email {i}"} for i in range(1, count + 1)]
        campaign = models.Campaign(
            slug="trend-test", title="Trend test", product=product, offer="",
            sender_name="Sender", sender_email="sender@example.com", audience_name="Audience",
            sequence_json=[{"stage": i, "focus": f"Focus {i}"} for i in range(1, count + 1)],
            emails_json=rows,
        )
        self.db.add(campaign)
        self.db.commit()
        return campaign

    def enable(self):
        self.db.add(models.Setting(key="trend_intelligence_enabled", value="true"))
        self.db.commit()

    def add_opportunity(self, product_key="turbo_addons", url="https://example.com/turbo"):
        now = datetime.now(timezone.utc)
        run = models.TrendRun(status="completed", mode="draft_only", started_at=now, completed_at=now)
        self.db.add(run)
        self.db.flush()
        opportunity = models.TrendOpportunity(
            run_id=run.id, product_key=product_key, product_name=product_key,
            topic="Elementor workflow planning", normalized_topic="elementor workflow planning",
            score=72, confidence="High", selected=True, fatigue_blocked=False,
            qa_json=[{"name": "QA", "pass": True}],
            draft_json={"preheader": "Independent coverage supports a timely workflow angle."},
            created_at=now,
        )
        self.db.add(opportunity)
        self.db.flush()
        evidence = models.TrendEvidence(
            run_id=run.id, opportunity_id=opportunity.id, product_key=product_key,
            source="news", topic=opportunity.topic, title="Elementor workflow coverage",
            source_url=url, observed_at=now,
        )
        self.db.add(evidence)
        self.db.commit()
        return run, opportunity, evidence

    @staticmethod
    def generated(**kwargs):
        ref = kwargs["email_ref"]
        return {
            "subject": f"Subject {ref}", "preview_text": "Preview", "body": "Body",
            "body_html": "<p>Body</p>", "cta_url": "https://example.com/?utm_source=sendclean",
            "cta_label": "Learn more", "journey_phase": 1, "journey_name": "Awareness",
            "via_llm": False, "llm_provider": None, "llm_model": None, "md_source": "brand.md",
        }

    def test_product_aliases_are_exact_and_unsupported_products_do_not_cross_map(self):
        expected = {
            "Turbo Guards": "turbo_guard", "Turbo Guard": "turbo_guard",
            "turbo_guard": "turbo_guard", "turbo-guard": "turbo_guard",
            "turbo": "turbo_addons", "Turbo Add-ons": "turbo_addons",
            "Managerium ERP": "managerium", "PeopleDesk": "peopledesk",
            "PrimeVAT": "primevat", "TrackForce": "trackforce", "Travilo": "travilo",
            "AeroApps": "aeroapps", "AeroDrop": "aerodrop",
        }
        for alias, key in expected.items():
            self.assertEqual(campaign_product_key(alias), key)
        for unsupported in ("Family Guard", "iBOS Services", "AeroRank", "custom aero product"):
            self.assertIsNone(campaign_product_key(unsupported))
        self.assertEqual(copy_product_key("custom aero product"), "ibos")
        for alias in ("Turbo Guards", "Turbo Guard", "turbo_guard", "turbo-guard"):
            self.assertEqual(copy_product_key(alias), "turbo_guard")

    @patch("app.llm_providers.get_active_config", return_value=None)
    @patch("app.copy_engine.generate")
    @patch("app.trend_intelligence.run_trend_analysis")
    def test_multi_email_generation_runs_product_only_analysis_once_and_persists_provenance(
        self, analyze, copy_generate, _llm
    ):
        self.enable()
        self.campaign(count=3)

        def analysis(db, settings, scheduled, product_keys):
            self.assertFalse(scheduled)
            self.assertEqual(product_keys, ["turbo_addons"])
            run, _, _ = self.add_opportunity()
            return {"id": run.id, "status": "completed", "completed_at": run.completed_at.isoformat()}

        analyze.side_effect = analysis
        contexts = []

        def generate(**kwargs):
            contexts.append(kwargs["trend_context"])
            return self.generated(**kwargs)

        copy_generate.side_effect = generate
        result = generate_copy("trend-test", db=self.db)

        self.assertEqual(analyze.call_count, 1)
        self.assertEqual(copy_generate.call_count, 3)
        self.assertTrue(all(context is contexts[0] for context in contexts))
        self.assertEqual(contexts[0]["product_key"], "turbo_addons")
        self.assertEqual(contexts[0]["status"], "grounded")
        self.assertTrue(all(row["trend_status"] == "grounded" for row in result["emails"]))

        self.db.expire_all()
        stored = self.db.query(models.Campaign).filter_by(slug="trend-test").one().emails_json
        provenance = stored[0]["trend_provenance"]
        self.assertEqual(provenance["topic"], "Elementor workflow planning")
        self.assertEqual(provenance["score"], 72)
        self.assertEqual(provenance["confidence"], "High")
        self.assertEqual(provenance["evidence_urls"], ["https://example.com/turbo"])
        self.assertIsNotNone(provenance["analyzed_at"])

    def test_existing_opportunity_uses_only_exact_product_evidence(self):
        self.enable()
        run, opportunity, _ = self.add_opportunity("managerium", "https://example.com/erp")
        self.db.add(models.TrendEvidence(
            run_id=run.id, opportunity_id=opportunity.id, product_key="peopledesk",
            source="news", topic="Payroll", title="Payroll evidence",
            source_url="https://example.com/hr", observed_at=datetime.now(timezone.utc),
        ))
        self.db.commit()
        with patch("app.trend_intelligence.run_trend_analysis") as analyze:
            context = campaign_trend_context(
                self.db, "Managerium ERP", {"trend_intelligence_enabled": True}
            )
        analyze.assert_not_called()
        self.assertEqual([row["source_url"] for row in context["evidence"]], ["https://example.com/erp"])

    @patch("app.llm_providers.get_active_config", return_value=None)
    @patch("app.copy_engine.generate")
    @patch("app.trend_intelligence.run_trend_analysis")
    def test_disabled_and_unsupported_profiles_fall_back_without_analysis(self, analyze, copy_generate, _llm):
        copy_generate.side_effect = self.generated
        self.campaign(product="Family Guard", count=1)
        unsupported = generate_copy("trend-test", db=self.db)
        self.assertEqual(unsupported["emails"][0]["trend_status"], "unsupported_product")
        analyze.assert_not_called()

        self.db.query(models.Campaign).filter_by(slug="trend-test").delete()
        self.db.commit()
        self.campaign(product="TrackForce", count=1)
        disabled = generate_copy("trend-test", db=self.db)
        self.assertEqual(disabled["emails"][0]["trend_status"], "disabled")
        analyze.assert_not_called()

    @patch("app.llm_providers.get_active_config", return_value=None)
    @patch("app.copy_engine.generate")
    @patch("app.trend_intelligence.run_trend_analysis", return_value={"id": 8, "status": "completed", "completed_at": "2026-09-14T00:00:00+00:00"})
    def test_no_validated_opportunity_keeps_normal_copy_generation_functional(self, _analyze, copy_generate, _llm):
        self.enable()
        self.campaign(product="TrackForce", count=1)
        copy_generate.side_effect = self.generated
        result = generate_copy("trend-test", db=self.db)
        self.assertEqual(result["emails"][0]["subject"], "Subject E1")
        self.assertEqual(result["emails"][0]["trend_status"], "no_validated_opportunity")
        self.assertEqual(copy_generate.call_args.kwargs["trend_context"]["evidence"], ())

    def test_deterministic_and_llm_paths_treat_trend_as_an_angle_not_a_claim(self):
        context = {
            "status": "grounded", "topic": "2026 Elementor workflow trends",
            "rationale": "Independent coverage", "score": 75, "confidence": "High",
            "evidence": ({"id": 1, "title": "A source", "source_url": "https://example.com/source"},),
        }
        deterministic = generate_email_copy("Turbo Add-ons", trend_context=context)
        self.assertIn("Elementor workflow trends", deterministic["body"])
        self.assertNotIn("2026 Elementor", deterministic["body"])

        llm_result = {"subject": "Fresh subject", "preview_text": "Fresh preview", "body": "Hi there,\n\nUseful note."}
        with patch("app.llm_providers.generate_email", return_value=llm_result) as llm:
            generate_email_copy(
                "Turbo Add-ons", trend_context=context,
                llm_config={"provider": "test", "model": "test", "api_key": "key"},
            )
        prompt = llm.call_args.args[1]
        self.assertIn("editorial inspiration only", prompt)
        self.assertIn("Never present the trend", prompt)
        self.assertIn("https://example.com/source", prompt)


if __name__ == "__main__":
    unittest.main()

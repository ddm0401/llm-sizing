import importlib.util
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("model_probe", ROOT / "scripts" / "model_probe.py")
model_probe = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["model_probe"] = model_probe
SPEC.loader.exec_module(model_probe)


class ScenarioProfileTests(unittest.TestCase):
    def test_openclaw_profile_uses_minimum_normal_and_production_requires_inputs(self):
        scenario_name, profile = model_probe.scenario_profile("OpenClaw Agent")

        self.assertEqual(scenario_name, "agent")
        self.assertEqual(set(profile["tiers"]), {"minimum", "normal"})
        self.assertLess(profile["tiers"]["minimum"]["target_tps"], profile["tiers"]["normal"]["target_tps"])
        self.assertEqual(profile["tiers"]["minimum"]["context_tokens"], 16_384)
        self.assertEqual(profile["tiers"]["normal"]["context_tokens"], 32_768)
        self.assertIn("active_sessions", profile["production_required_inputs"])

    def test_production_tier_is_guidance_without_explicit_inputs(self):
        production = model_probe.production_guidance("agent", model_probe.DEFAULT_PROFILES["agent"], {})

        self.assertEqual(production["status"], "requires_inputs")
        self.assertIn("latency_or_target_tps_per_session", production["required_inputs"])
        self.assertIn("OpenClaw", production["guidance"])


if __name__ == "__main__":
    unittest.main()

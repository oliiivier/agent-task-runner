"""Tests unitaires du module routing."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_runner import routing
from agent_runner.config import DEFAULTS
from agent_runner.task import Task


class RoutingUnitTests(unittest.TestCase):
    def test_map_model(self):
        claude, agy = ["haiku", "sonnet", "opus", "fable"], ["flash", "pro", "ultra"]
        self.assertEqual([routing.map_model(m, claude, agy) for m in claude], ["flash", "pro", "pro", "ultra"])
        self.assertEqual([routing.map_model(m, agy, claude) for m in agy], ["haiku", "opus", "fable"])
        self.assertEqual(routing.map_model("sonnet", claude, agy, {"sonnet": "ultra"}), "ultra")

    def test_resolve_task_routing_pure(self):
        from agent_runner.adapters.base import WindowQuota

        cfg = json.loads(json.dumps(DEFAULTS))
        pref = ["agy", "claude"]
        healthy = {
            "agy": [WindowQuota("session (5h)", 15.0, reset_minutes=120.0)],
            "claude": [WindowQuota("session (5h)", 30.0, reset_minutes=120.0)],
        }

        def mk_task(**kw):
            defaults = {"path": Path("01-t.md"), "id": "01-t", "branch": "feat/t", "body": "",
                        "allowed_tools": ["Bash(git:*)"]}
            defaults.update(kw)
            return Task(**defaults)

        # 1. Préférence par niveau (N1->agy, N3->claude)
        self.assertEqual(routing.resolve_task_routing(mk_task(level="N1"), cfg, set(), healthy, set(), pref),
                         ("agy", "niveau N1"))
        self.assertEqual(routing.resolve_task_routing(mk_task(level="N3"), cfg, set(), healthy, set(), pref),
                         ("claude", "niveau N3"))

        # 2. Pin explicite respecté même si le seuil est dépassé
        over_claude = {"agy": healthy["agy"], "claude": [WindowQuota("session (5h)", 90.0, reset_minutes=120.0)]}
        self.assertEqual(
            routing.resolve_task_routing(mk_task(level="N1", runner="claude", runner_pinned=True),
                                         cfg, set(), over_claude, set(), pref),
            ("claude", "pin"),
        )

        # 3. strict_allowed_tools force claude même en N1
        strict_cfg = json.loads(json.dumps(cfg))
        strict_cfg["strict_allowed_tools"] = True
        self.assertEqual(routing.resolve_task_routing(mk_task(level="N1"), strict_cfg, set(), healthy, set(), pref),
                         ("claude", "contrainte allowed_tools"))

        # 4. max_budget_usd force claude même en N2
        self.assertEqual(
            routing.resolve_task_routing(mk_task(level="N2", max_budget_usd=1.5), cfg, set(), healthy, set(), pref),
            ("claude", "contrainte budget"),
        )

        # 5. Seuil dépassé -> bascule sur l'alternative saine
        runner, reason = routing.resolve_task_routing(mk_task(level="N3"), cfg, set(), over_claude, set(), pref)
        self.assertEqual(runner, "agy")
        self.assertIn("bascule (claude: session (5h) 90% >= 80%)", reason)

        # 6. Reset imminent (15m <= 20m et < 95%) relâche la contrainte
        imminent_claude = {"agy": healthy["agy"], "claude": [WindowQuota("session (5h)", 85.0, reset_minutes=15.0)]}
        self.assertEqual(routing.resolve_task_routing(mk_task(level="N3"), cfg, set(), imminent_claude, set(), pref),
                         ("claude", "niveau N3"))

        # 7. Usage indisponible -> dégradation gracieuse vers préférence par niveau
        unknown = {"agy": None, "claude": None}
        self.assertEqual(routing.resolve_task_routing(mk_task(level="N2"), cfg, set(), unknown, set(), pref),
                         ("agy", "niveau N2, usage inconnu"))

        # 8. Runner indisponible -> repli propre
        runner, reason = routing.resolve_task_routing(mk_task(level="N1"), cfg, {"agy"}, healthy, set(), pref)
        self.assertEqual(runner, "claude")
        self.assertIn("indisponible", reason)

        # 9. prefer_runner oriente le choix mais bascule librement sur quota
        self.assertEqual(
            routing.resolve_task_routing(mk_task(level="N2", prefer_runner="claude"), cfg, set(), healthy, set(), pref),
            ("claude", "préférence claude (N2)"),
        )
        runner, reason = routing.resolve_task_routing(
            mk_task(level="N2", prefer_runner="claude"), cfg, set(), over_claude, set(), pref
        )
        self.assertEqual(runner, "agy")
        self.assertIn("bascule (claude:", reason)

        # 10. argon (7j) élevé ne bloque pas agy globalement, mais filtre gemini-4-argon de la file
        high_argon = {
            "agy": [
                WindowQuota("session (5h)", 15.0, reset_minutes=120.0),
                WindowQuota("argon (7j)", 85.0, reset_minutes=1440.0),
            ],
            "claude": healthy["claude"],
        }
        self.assertEqual(routing.resolve_task_routing(mk_task(level="N2"), cfg, set(), high_argon, set(), pref),
                         ("agy", "niveau N2"))
        self.assertEqual(
            routing.candidate_models(mk_task(level="N2"), cfg, None, False, "agy", windows=high_argon["agy"]),
            ["gemini-4-hydrogen"],
        )

        # 11. allow_premium=False bride l'escalade vers les modèles premium (argon/neon/opus/fable)
        self.assertEqual(
            routing.candidate_models(mk_task(level="N2", allow_premium=False), cfg, None, False, "agy"),
            ["gemini-4-hydrogen"],
        )
        self.assertEqual(
            routing.candidate_models(mk_task(level="N2", allow_premium=False), cfg, None, False, "claude"),
            ["sonnet"],
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_runner.frontmatter import parse_frontmatter


class FrontmatterTests(unittest.TestCase):
    def test_parse(self):
        meta, body = parse_frontmatter(textwrap.dedent('''\
            ---
            id: 10-x
            # commentaire
            escalate: false
            max_turns: 30
            max_budget_usd: 1.5
            verify_cmd: "a: b && c"
            model: null
            allowed_tools:
              - "Bash(git:*)"
              - Bash(ls:*)
            ---

            ## Objectif
            texte
            '''))
        self.assertEqual(meta, {"id": "10-x", "escalate": False, "max_turns": 30, "max_budget_usd": 1.5,
                                "verify_cmd": "a: b && c", "model": None,
                                "allowed_tools": ["Bash(git:*)", "Bash(ls:*)"]})
        self.assertTrue(body.startswith("## Objectif"))
        with self.assertRaises(ValueError):
            parse_frontmatter("pas de frontmatter")


class ParsingRobustnessTests(unittest.TestCase):
    def test_numeric_id_and_trailing_comments(self):
        meta, _ = parse_frontmatter(textwrap.dedent('''\
            ---
            id: 01
            batch: 7
            max_turns: 30 # garde-fou
            verify_cmd: "echo '#' # fin"
            model: haiku   # le moins cher
            effort:  # vide
            allowed_tools: ["Bash(git:*)", "x #y"]  # liste
            ---
            corps
            '''))
        self.assertEqual(meta["id"], "01")
        self.assertEqual(meta["batch"], 7)
        self.assertEqual(meta["max_turns"], 30)
        self.assertEqual(meta["verify_cmd"], "echo '#' # fin")
        self.assertEqual(meta["model"], "haiku")
        self.assertIsNone(meta["effort"])
        self.assertEqual(meta["allowed_tools"], ["Bash(git:*)", "x #y"])


if __name__ == "__main__":
    unittest.main()

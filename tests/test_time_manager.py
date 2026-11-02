"""Time-budget tests (added 2026-10-27).

The site's clock is a ~60 s per-move clock (``info.step_timeout``, confirmed from
the logged games), so the search gets that clock minus the surrounding work minus
a safety margin - and a soft limit stops it from starting an iteration it cannot
finish.
"""

import unittest

from checkersbot.ai import CheckersAI
from checkersbot.board import Board
from checkersbot.bot import CheckersBot
from checkersbot.time_manager import (
    DEFAULT_BUDGET_S,
    MIN_BUDGET_S,
    plan_budget,
)


class PlanBudgetTests(unittest.TestCase):
    def test_no_clock_falls_back_to_the_safe_default(self):
        budget = plan_budget(None)
        self.assertEqual(budget.hard_s, DEFAULT_BUDGET_S)
        self.assertLess(budget.soft_s, budget.hard_s)
        self.assertIn("default", budget.source)

    def test_a_zero_or_negative_clock_is_treated_as_unknown(self):
        for value in (0, -3, 0.0):
            with self.subTest(value=value):
                self.assertEqual(plan_budget(value).hard_s, DEFAULT_BUDGET_S)

    def test_the_sixty_second_clock_keeps_a_margin(self):
        budget = plan_budget(60, overhead_s=6.0, safety=0.2)
        self.assertIsNotNone(budget.clock_s)
        self.assertAlmostEqual(budget.hard_s, 42.0, places=3)   # 60 - 6 - 12
        self.assertAlmostEqual(budget.soft_s, 31.5, places=3)   # 75 % of the hard
        self.assertIn("site clock", budget.source)

    def test_the_budget_never_exceeds_the_clock(self):
        for clock in (5, 15, 30, 60, 120):
            budget = plan_budget(clock)
            self.assertLess(budget.hard_s, clock)
            self.assertLessEqual(budget.soft_s, budget.hard_s)

    def test_a_tiny_clock_respects_the_floor(self):
        budget = plan_budget(2, overhead_s=6.0)
        self.assertEqual(budget.hard_s, MIN_BUDGET_S)
        self.assertLessEqual(budget.soft_s, budget.hard_s)

    def test_the_user_cap_wins(self):
        self.assertEqual(plan_budget(60, ceiling_s=5).hard_s, 5.0)
        self.assertEqual(plan_budget(None, ceiling_s=5).hard_s, 5.0)
        # a cap below the floor is raised to the floor, never to 0
        self.assertEqual(plan_budget(60, ceiling_s=0.1).hard_s, MIN_BUDGET_S)

    def test_the_helper_is_serialisable(self):
        data = plan_budget(60).to_dict()
        self.assertEqual(set(data), {"soft_s", "hard_s", "clock_s", "overhead_s", "source"})


class BotBudgetTests(unittest.TestCase):
    class FakeState:
        def __init__(self, clock):
            self.step_timeout = clock

    def make_bot(self, **kwargs):
        return CheckersBot(site=object(), verbose=False, **kwargs)

    def test_the_bot_takes_the_budget_from_the_site_clock(self):
        bot = self.make_bot()
        budget = bot._budget_for(self.FakeState(60))
        self.assertEqual(budget.clock_s, 60.0)
        self.assertLess(budget.hard_s, 60.0)
        self.assertGreater(budget.hard_s, 12.0)      # stronger than the old fixed 12 s

    def test_no_clock_keeps_the_old_default(self):
        bot = self.make_bot()
        self.assertEqual(bot._budget_for(self.FakeState(None)).hard_s, DEFAULT_BUDGET_S)
        self.assertEqual(bot._budget_for(None).hard_s, DEFAULT_BUDGET_S)
        self.assertEqual(bot._budget_for(self.FakeState("n/a")).hard_s, DEFAULT_BUDGET_S)

    def test_an_explicit_time_caps_the_budget(self):
        bot = self.make_bot(time_limit=5.0)
        self.assertEqual(bot._budget_for(self.FakeState(60)).hard_s, 5.0)

    def test_the_overhead_is_configurable(self):
        bot = self.make_bot(time_overhead=20.0)
        self.assertEqual(bot._budget_for(self.FakeState(60)).hard_s, 28.0)  # 60-20-12


class SoftLimitTests(unittest.TestCase):
    def test_no_new_iteration_starts_after_the_soft_limit(self):
        board = Board(rules="joker")
        ai = CheckersAI(max_depth=60, time_limit=None, soft_limit=0.0)
        move = ai.choose_move(board)
        self.assertIn(move, board.generate_moves())
        self.assertEqual(ai.stats.depth, 1)              # depth 1, then stop
        self.assertEqual(ai.stats.note, "soft time limit")

    def test_without_a_soft_limit_the_search_goes_deeper(self):
        board = Board(rules="joker")
        ai = CheckersAI(max_depth=4, time_limit=None)
        ai.choose_move(board)
        self.assertEqual(ai.stats.depth, 4)

    def test_a_timed_out_move_is_still_legal_and_sensible(self):
        board = Board(rules="joker")
        ai = CheckersAI(max_depth=60, time_limit=0.001)
        move = ai.choose_move(board)
        self.assertIn(move, board.generate_moves())
        # the clock cannot have finished an iteration at 1 ms, so the fallback ran
        self.assertIn(ai.stats.note, ("", "time limit"))
        self.assertGreaterEqual(ai.stats.depth, 0)


if __name__ == "__main__":
    unittest.main()

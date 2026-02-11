from __future__ import annotations

import unittest

from bot.ui_nav import get_state, pop_screen, push_screen, reset_stack, set_state


class UINavigationTests(unittest.TestCase):
    def test_state_roundtrip(self):
        user_data = {}
        self.assertEqual(get_state(user_data), "idle")
        set_state(user_data, "awaiting_case")
        self.assertEqual(get_state(user_data), "awaiting_case")

    def test_stack_push_pop_reset(self):
        user_data = {}
        push_screen(user_data, "idle")
        push_screen(user_data, "topic_select")
        self.assertEqual(pop_screen(user_data)["screen"], "topic_select")
        self.assertEqual(pop_screen(user_data)["screen"], "idle")
        self.assertIsNone(pop_screen(user_data))

        push_screen(user_data, "idle")
        reset_stack(user_data)
        self.assertIsNone(pop_screen(user_data))

    def test_push_idempotent_for_same_tail(self):
        user_data = {}
        push_screen(user_data, "idle")
        push_screen(user_data, "idle")
        self.assertEqual(len(user_data["nav_stack"]), 1)


if __name__ == "__main__":
    unittest.main()

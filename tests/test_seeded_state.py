"""Fresh seeds parameterize only the existing shared-capacity sandbox."""

import unittest

from apps.sandbox.fixtures import load_fixture, seeded_state, validate_fixture
from apps.sandbox.state import state_hash


class SeededStateTests(unittest.TestCase):
    def test_canonical_seed_is_unchanged_and_fresh_seeds_are_valid_and_reproducible(self):
        self.assertEqual(seeded_state(0), load_fixture())
        hashes = set()
        for seed in (0, 1, 2, 9, 31, 2**32 - 1):
            state, task = seeded_state(seed)
            self.assertEqual((state, task), seeded_state(seed))
            validate_fixture(state, task)
            hashes.add(state_hash(state))
        self.assertEqual(len(hashes), 6)

    def test_invalid_seed_cannot_construct_a_sandbox(self):
        for seed in (-1, 2**32, True, "1"):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                seeded_state(seed)


if __name__ == "__main__":
    unittest.main()

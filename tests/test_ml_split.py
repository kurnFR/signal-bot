import unittest

import pandas as pd

from ml.split import split_by_fractions, split_by_timestamps


class TestMLSplit(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame({"open_time": list(range(1, 11)), "value": list(range(10))})

    def test_fraction_split_is_chronological_and_disjoint(self):
        result = split_by_fractions(self.df, 0.6, 0.2)
        self.assertEqual(len(result.train), 6)
        self.assertEqual(len(result.validation), 2)
        self.assertEqual(len(result.test), 2)
        self.assertLess(result.train.open_time.max(), result.validation.open_time.min())
        self.assertLess(result.validation.open_time.max(), result.test.open_time.min())
        self.assertEqual(list(result.train.open_time), [1, 2, 3, 4, 5, 6])
        self.assertEqual(list(result.validation.open_time), [7, 8])
        self.assertEqual(list(result.test.open_time), [9, 10])

    def test_fraction_split_sorts_unsorted_input_without_shuffling_within_splits(self):
        result = split_by_fractions(self.df.iloc[::-1], 0.5, 0.2)
        self.assertEqual(list(result.train.open_time), [1, 2, 3, 4, 5])
        self.assertEqual(list(result.validation.open_time), [6, 7])
        self.assertEqual(list(result.test.open_time), [8, 9, 10])

    def test_timestamp_split_uses_half_open_boundaries(self):
        result = split_by_timestamps(self.df, 5, 8)
        self.assertEqual(list(result.train.open_time), [1, 2, 3, 4])
        self.assertEqual(list(result.validation.open_time), [5, 6, 7])
        self.assertEqual(list(result.test.open_time), [8, 9, 10])

    def test_invalid_fraction_is_rejected(self):
        with self.assertRaises(ValueError):
            split_by_fractions(self.df, 0.8, 0.3)

    def test_invalid_timestamp_boundaries_are_rejected(self):
        with self.assertRaises(ValueError):
            split_by_timestamps(self.df, 8, 5)


if __name__ == "__main__":
    unittest.main()

import unittest
from join_per_run import split_seed_tag

class SeedTagTest(unittest.TestCase):
    def test_c4_pattern(self):
        self.assertEqual(split_seed_tag("fixed4_s100_seed21"),
                         ("fixed4_s100", 21))
    def test_relay_storm_pattern(self):
        self.assertEqual(split_seed_tag("baseline_s100_ivd29_mcd100_seed21"),
                         ("baseline_s100_ivd29_mcd100", 21))
    def test_gen_pattern(self):
        self.assertEqual(split_seed_tag("sameL211_N16_v2v-highway_s140_seed10_mcd300"),
                         ("sameL211_N16_v2v-highway_s140", 10))
    def test_adapt_pattern(self):
        self.assertEqual(split_seed_tag("adapt_s100_seed1"),
                         ("adapt_s100", 1))
    def test_no_seed(self):
        self.assertEqual(split_seed_tag("foo"), ("foo", None))

if __name__ == "__main__":
    unittest.main()

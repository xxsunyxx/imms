import unittest

from imms import formulas


class FormulaTests(unittest.TestCase):
    def test_sigmoid_matches_appendix(self):
        self.assertAlmostEqual(formulas.sigmoid(1.2), 0.7685, places=4)

    def test_trust_asymmetry(self):
        # F02 includes decay toward baseline; the spec appendix ignored it.
        kappa_v = formulas.kappa(0.4)
        nu_v = formulas.nu(0.66)
        up = formulas.relation_update(0.30, 0.0, 0.95, 0.25, 1.0, kappa_v, nu_v)
        down = formulas.relation_update(0.66, 0.0, 0.95, -0.45, 1.2, kappa_v, nu_v, -1, 1)
        self.assertAlmostEqual(up, 0.6446, places=3)
        self.assertAlmostEqual(down, -0.1497, places=3)

    def test_vote_distribution_normalizes(self):
        dist = formulas.vote_distribution({"a": 1.2, "b": 0.4, "c": -0.3}, 1.0)
        self.assertAlmostEqual(sum(dist.values()), 1.0, places=6)

    def test_leak_grows_with_arousal(self):
        base = formulas.leak_sigma(0.05, 0.0, 0.0, 0.5)
        hot = formulas.leak_sigma(0.05, 0.9, 0.4, 0.5)
        self.assertGreater(hot, base)

    def test_heat_curve(self):
        self.assertAlmostEqual(formulas.heat_target(1, 2, 3.0), 0.9896, places=4)

    def test_lie_heat(self):
        self.assertAlmostEqual(formulas.lie_heat(0.3, 1, 1), 1.255, places=4)

    def test_contradiction_gate(self):
        self.assertEqual(formulas.contradiction_strength(0.6, 0.6, 0.9), 0.54)
        self.assertEqual(formulas.contradiction_strength(0.4, 0.6, 0.9), 0.0)


if __name__ == "__main__":
    unittest.main()

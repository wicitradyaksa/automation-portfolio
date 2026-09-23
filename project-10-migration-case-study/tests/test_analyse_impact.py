"""Tests on the arithmetic, and on the honesty of the presentation.

Two kinds of test here:

1. **The statistics are right.** Wilson intervals, nearest-rank percentiles,
   per-item normalisation, payback. Checked against hand-worked values, not
   against the implementation's own output -- a test that asserts the code
   returns what the code returns proves nothing.

2. **The write-up matches the data.** Every number in README.md is
   regenerated and compared. A case study whose prose has drifted from its
   data is worse than one with no numbers, because it looks rigorous.
"""

import json
import pathlib
import re
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import analyse_impact as ai

ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestWilsonInterval(unittest.TestCase):
    def test_contains_the_point_estimate(self):
        low, high = ai.wilson_interval(41, 2635)
        self.assertLess(low, 41 / 2635)
        self.assertGreater(high, 41 / 2635)

    def test_never_goes_below_zero_at_low_counts(self):
        """The reason this is Wilson and not the normal approximation. At
        these rates the normal approximation produces a negative lower bound
        -- a negative error rate, which is visibly wrong and quietly
        discredits every number printed next to it."""
        for successes, trials in ((0, 100), (1, 3000), (2, 5000)):
            with self.subTest(k=successes, n=trials):
                low, high = ai.wilson_interval(successes, trials)
                self.assertGreaterEqual(low, 0.0)
                self.assertLessEqual(high, 1.0)

    def test_zero_successes_still_gives_an_upper_bound(self):
        """'We saw no errors in 200 runs' is not 'the error rate is zero'."""
        low, high = ai.wilson_interval(0, 200)
        self.assertEqual(low, 0.0)
        self.assertGreater(high, 0.0)

    def test_matches_a_hand_worked_value(self):
        # k=10, n=100, z=1.96 -> approximately (0.0553, 0.1739)
        low, high = ai.wilson_interval(10, 100)
        self.assertAlmostEqual(low, 0.0553, places=3)
        self.assertAlmostEqual(high, 0.1739, places=3)

    def test_interval_narrows_as_the_sample_grows(self):
        narrow = ai.wilson_interval(100, 10000)
        wide = ai.wilson_interval(1, 100)
        self.assertLess(narrow[1] - narrow[0], wide[1] - wide[0])

    def test_zero_trials_does_not_divide_by_zero(self):
        self.assertEqual(ai.wilson_interval(0, 0), (0.0, 0.0))


class TestPercentile(unittest.TestCase):
    def test_nearest_rank_returns_an_observed_value(self):
        """Interpolation invents precision a 60-observation sample does not
        support, and returns a number nobody ever measured."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        for p in (10, 50, 95, 100):
            with self.subTest(p=p):
                self.assertIn(ai.percentile(values, p), values)

    def test_known_values(self):
        values = list(range(1, 101))
        self.assertEqual(ai.percentile(values, 50), 50)
        self.assertEqual(ai.percentile(values, 95), 95)
        self.assertEqual(ai.percentile(values, 100), 100)

    def test_p95_is_above_the_median(self):
        values = [float(v) for v in range(1, 61)]
        self.assertGreater(ai.percentile(values, 95), ai.percentile(values, 50))

    def test_empty_and_single(self):
        self.assertEqual(ai.percentile([], 95), 0.0)
        self.assertEqual(ai.percentile([7.0], 95), 7.0)


class TestRunAccounting(unittest.TestCase):
    def test_rework_counts_toward_the_total(self):
        """Excluding rework would flatter the automation, because most of the
        manual process's rework came from mistakes automation cannot make."""
        run = ai.Run(date="2026-01-05", batch_size=40, minutes=80.0, errors=2, rework_minutes=20.0)
        self.assertEqual(run.total_minutes, 100.0)


class TestSummary(unittest.TestCase):
    def setUp(self):
        self.result = ai.analyse()

    def test_both_arms_have_data(self):
        self.assertGreater(self.result["before"]["runs"], 30)
        self.assertGreater(self.result["after"]["runs"], 30)

    def test_minutes_per_item_normalises_for_batch_size(self):
        """Batch sizes differ between the two periods. Comparing per-run times
        alone compares different amounts of work and reports the difference as
        an improvement."""
        before = self.result["before"]
        expected = sum(
            r.total_minutes for r in ai.load(ai.DATA / "before_manual.csv")
        ) / before["items"]
        self.assertAlmostEqual(before["minutes_per_item"], round(expected, 3), places=3)

    def test_p95_exceeds_the_median_in_both_arms(self):
        for arm in ("before", "after"):
            with self.subTest(arm=arm):
                self.assertGreaterEqual(self.result[arm]["p95_minutes"], self.result[arm]["median_minutes"])

    def test_error_rate_matches_errors_over_items(self):
        for arm in ("before", "after"):
            data = self.result[arm]
            with self.subTest(arm=arm):
                self.assertAlmostEqual(data["error_rate"], round(data["errors"] / data["items"], 4), places=4)

    def test_the_confidence_interval_brackets_the_rate(self):
        for arm in ("before", "after"):
            data = self.result[arm]
            with self.subTest(arm=arm):
                self.assertLessEqual(data["error_rate_low"], data["error_rate"])
                self.assertGreaterEqual(data["error_rate_high"], data["error_rate"])


class TestComparison(unittest.TestCase):
    def setUp(self):
        self.comp = ai.analyse()["comparison"]

    def test_payback_is_build_hours_over_weekly_saving(self):
        expected = ai.BUILD_HOURS / self.comp["hours_saved_per_week"]
        self.assertAlmostEqual(self.comp["payback_weeks"], round(expected, 1), places=1)

    def test_annual_hours_use_46_weeks_not_52(self):
        """Nobody works 52 weeks. Leave, public holidays and the weeks the
        process does not run are real, and 52 is the standard way an impact
        number gets inflated by 13% without anyone noticing."""
        self.assertAlmostEqual(
            self.comp["hours_saved_per_year"], round(self.comp["hours_saved_per_week"] * 46, 1), places=1
        )

    def test_annual_value_is_hours_times_the_stated_rate(self):
        expected = self.comp["hours_saved_per_week"] * 46 * self.comp["hourly_cost_aud"]
        self.assertAlmostEqual(self.comp["annual_value_aud"], round(expected, 0), places=0)

    def test_the_hourly_rate_is_stated_not_hidden(self):
        """A reader with a different number must be able to substitute it."""
        self.assertIn("hourly_cost_aud", self.comp)
        self.assertGreater(self.comp["hourly_cost_aud"], 0)

    def test_build_cost_is_included(self):
        """An impact claim with no build cost is a benefit with no price."""
        self.assertGreater(self.comp["build_hours"], 0)

    def test_speedup_is_computed_per_item(self):
        result = ai.analyse()
        expected = result["before"]["minutes_per_item"] / result["after"]["minutes_per_item"]
        self.assertAlmostEqual(self.comp["speedup_factor"], round(expected, 1), places=1)


class TestHonesty(unittest.TestCase):
    """The claims this project makes must stay inside what the data supports."""

    def setUp(self):
        self.result = ai.analyse()

    def test_the_automated_arm_still_has_errors(self):
        """Automation moves failures, it does not abolish them. A case study
        claiming zero errors is one a reviewer stops believing."""
        self.assertGreater(self.result["after"]["errors"], 0)

    def test_the_automated_arm_still_has_a_tail(self):
        self.assertGreater(self.result["after"]["p95_minutes"], self.result["after"]["median_minutes"])

    def test_failed_automated_runs_are_recorded_at_full_manual_cost(self):
        """A fallback to the manual process is part of what the automated arm
        costs. Dropping those rows would be the easiest way to inflate this."""
        runs = ai.load(ai.DATA / "after_automated.csv")
        failures = [r for r in runs if "manually" in r.note]
        self.assertTrue(failures, "no fallback runs recorded")
        median = sorted(r.total_minutes for r in runs)[len(runs) // 2]
        for run in failures:
            with self.subTest(date=run.date):
                self.assertGreater(run.total_minutes, median * 2)

    def test_the_manual_arm_improves_over_time(self):
        """People get faster at a repetitive task. Crediting that learning
        curve to the automation would overstate the result."""
        runs = ai.load(ai.DATA / "before_manual.csv")
        first = [r.total_minutes / r.batch_size for r in runs[:15]]
        last = [r.total_minutes / r.batch_size for r in runs[-15:]]
        self.assertLess(sum(last) / len(last), sum(first) / len(first))

    def test_the_claimed_saving_is_not_implausible(self):
        """A guard against a generator or a data swap producing a number that
        would not survive an interview."""
        self.assertLess(self.result["comparison"]["speedup_factor"], 20)
        self.assertGreater(self.result["comparison"]["payback_weeks"], 0.5)


class TestOutputFormats(unittest.TestCase):
    def test_json_output_is_valid(self):
        out = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "analyse_impact.py"), "--json"],
            capture_output=True, text=True, check=True,
        )
        payload = json.loads(out.stdout)
        self.assertIn("comparison", payload)

    def test_text_output_runs(self):
        out = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "analyse_impact.py")],
            capture_output=True, text=True, check=True,
        )
        self.assertIn("Payback", out.stdout)

    def test_markdown_output_is_a_table(self):
        out = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "analyse_impact.py"), "--markdown"],
            capture_output=True, text=True, check=True,
        )
        self.assertTrue(out.stdout.startswith("| Measure |"))


class TestReadmeMatchesTheData(unittest.TestCase):
    """The drift check. A case study whose prose has wandered from its data is
    worse than one with no numbers, because it looks rigorous."""

    @classmethod
    def setUpClass(cls):
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")
        cls.result = ai.analyse()

    def test_headline_figures_appear_verbatim(self):
        comp = self.result["comparison"]
        for label, value in (
            ("hours saved per week", "%.1f" % comp["hours_saved_per_week"]),
            ("payback weeks", "%.1f" % comp["payback_weeks"]),
            ("speedup", "%.1f" % comp["speedup_factor"]),
        ):
            with self.subTest(figure=label):
                self.assertIn(value, self.readme, label + " (" + value + ") is not in README.md")

    def test_the_generated_table_is_present(self):
        for row in ai.render_markdown(self.result).split("\n"):
            with self.subTest(row=row[:40]):
                self.assertIn(row, self.readme, "README table is stale -- regenerate with --markdown")

    def test_the_readme_states_the_data_is_synthetic(self):
        """The single most important assertion in this file. Presenting
        generated numbers as measurements would be the one genuinely
        disqualifying thing this portfolio could do."""
        lowered = self.readme.lower()
        self.assertTrue(
            "sample data" in lowered or "synthetic" in lowered,
            "README must say plainly that the dataset is synthetic",
        )

    def test_the_readme_states_the_assumptions(self):
        for token in ("46", str(int(ai.HOURLY_COST_AUD)), str(int(ai.BUILD_HOURS))):
            with self.subTest(token=token):
                self.assertIn(token, self.readme)

    def test_every_percentage_is_either_derived_or_marked_as_an_estimate(self):
        """The strongest honesty check in this project.

        A percentage in the README may be one of exactly two things:

        * **derived** -- a figure the analysis produces from the data, written
          bare (``-81%``, ``1.56%``);
        * **an estimate** -- written with a leading ``~`` so the reader can
          see at a glance that nobody measured it (``~55%`` of the run was
          transcription).

        Anything else fails the build. The effect is that it is not possible
        to slip a favourable-sounding percentage into this prose: either the
        data supports it, or it is visibly flagged as a guess.

        The ``~`` convention exists because the step-by-step breakdown of
        where the manual time went is genuinely an estimate -- it came from
        watching the process, not from instrumenting it -- and pretending
        otherwise would be the exact failure this project is about.
        """
        produced = set()
        values = (
            list(self.result["comparison"].values())
            + list(self.result["before"].values())
            + list(self.result["after"].values())
        )
        for value in values:
            if isinstance(value, (int, float)):
                produced.update(
                    {"%.0f" % value, "%.1f" % value, "%.2f" % value,
                     "%.0f" % (value * 100), "%.1f" % (value * 100), "%.2f" % (value * 100)}
                )
        # Percentages the generated table computes inline (the -81% column).
        produced.update(re.findall(r"(\d+(?:\.\d+)?)%", ai.render_markdown(self.result)))

        # Structural, not claims: "95% CI", "100% of what they see".
        structural = {"95", "100"}

        for match in re.finditer(r"(~?)(\d+(?:\.\d+)?)%", self.readme):
            marked, number = match.group(1), match.group(2)
            with self.subTest(percentage=match.group(0)):
                if marked == "~":
                    continue  # explicitly an estimate; the reader is told
                self.assertTrue(
                    number in produced or number in structural,
                    number + "% is stated bare in README.md but the analysis does not "
                    "produce it -- either derive it or write it as ~" + number + "%",
                )


if __name__ == "__main__":
    unittest.main()

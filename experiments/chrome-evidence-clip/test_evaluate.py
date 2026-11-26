import io
import unittest

from evaluate import evaluate


def score(*rows):
    header = "batch,task_id,condition,task_type,decision_seconds,blind_quality_1_to_5,baseline_pair_task_id"
    return evaluate(io.StringIO("\n".join((header, *rows))))


def score_evidence(*rows):
    header = (
        "batch,task_id,condition,task_type,decision_seconds,blind_quality_1_to_5,baseline_pair_task_id,"
        "expected_relevant_ids,returned_top5_ids,cited_ids,missing_or_invalid_ids"
    )
    return evaluate(io.StringIO("\n".join((header, "1,a,A,design,100,3,,,,,", *rows))))


class EvaluationTests(unittest.TestCase):
    def test_pairs_are_order_independent_and_conditions_are_separate(self):
        report = score("1,b,B,design,80,4,a", "1,c,C,design,125,2,a", "1,a,A,design,100,3,")
        self.assertAlmostEqual(report["B"]["median_decision_time_reduction_pct"], 20)
        self.assertEqual(report["B"]["mean_blind_quality_delta"], 1)
        self.assertEqual(report["C"]["median_decision_time_reduction_pct"], -25)
        self.assertEqual(report["C"]["mean_blind_quality_delta"], -1)

    def test_absent_arm_has_no_estimate(self):
        arm = score("1,a,A,design,100,3,")["B"]
        self.assertEqual(arm["pairs"], 0)
        self.assertEqual(arm["unpaired_baselines"], 1)
        self.assertIsNone(arm["median_decision_time_reduction_pct"])

    def test_invalid_pairs_and_measurements(self):
        for row in (
            "2,b,B,design,80,4,a",
            "1,b,B,research,80,4,a",
            "1,b,B,design,nan,4,a",
            "1,b,B,design,80,6,a",
            "1,a,B,design,80,4,a",
            "1,b,B,design,80,4,",
        ):
            with self.subTest(row=row), self.assertRaises(ValueError):
                score("1,a,A,design,100,3,", row)
        with self.assertRaises(ValueError):
            score("1,a,A,design,100,3,", "1,b,B,design,80,3,a", "1,d,B,design,60,4,a")

    def test_retrieval_and_citations_are_measured_per_arm(self):
        report = score_evidence(
            '1,b,B,design,80,4,a,"E-1,E-2,",[E-1] E-3,E-1 E-99,E-99',
            "1,c,C,design,85,4,a,E-1,-,-,-",
        )
        self.assertEqual(report["B"]["retrieval"]["recall_at_5_pct"], 50)
        self.assertEqual(report["B"]["retrieval"]["relevant_at_5_pct"], 50)
        self.assertEqual(report["B"]["citations"]["integrity_pct"], 50)
        self.assertEqual(report["C"]["retrieval"]["recall_at_5_pct"], 0)
        self.assertIsNone(report["C"]["retrieval"]["relevant_at_5_pct"])
        self.assertEqual(report["C"]["citations"]["trials_without_citations"], 1)
        self.assertIsNone(report["C"]["citations"]["integrity_pct"])

    def test_unmeasured_and_invalid_evidence_fields(self):
        report = score_evidence("1,b,B,design,80,4,a,,,,")
        self.assertEqual(report["B"]["retrieval"]["measured_trials"], 0)
        self.assertEqual(report["B"]["citations"]["measured_trials"], 0)
        for fields in (
            "E-1,,,",
            "E-1,E-1 [E-1],,",
            "E-1,E-1 E-2 E-3 E-4 E-5 E-6,,",
            "E-1,invalid,,",
            ",,E-1,E-2",
        ):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                score_evidence(f"1,b,B,design,80,4,a,{fields}")

    def test_baseline_rows_reject_evidence_measurements(self):
        with self.assertRaisesRegex(ValueError, "apply only to B and C"):
            score_evidence("1,other,A,design,100,3,,E-1,E-1,,")

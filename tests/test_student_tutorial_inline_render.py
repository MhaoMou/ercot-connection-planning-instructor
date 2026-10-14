import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "docs/student_tutorial/scripts/build_latex_inputs.py"
SPEC = importlib.util.spec_from_file_location("build_latex_inputs", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class InlineRenderTests(unittest.TestCase):
    def test_inline_tokens_have_visible_boundaries_and_preserve_token_modes(self):
        rendered = MODULE._markdown_text(
            "`ValueError: bus_id 9999 is not present in the released catalog`; `bus_id`; "
            "`submission_id`; `validation_status`; "
            "run `python scripts/check_submission.py --file submission.csv`."
        )

        self.assertEqual(rendered.count(r"\hspace{0.35em}"), 10)
        self.assertIn(
            r"\hspace{0.35em}\texttt{ValueError: bus\_id 9999 is not present in the released catalog}\hspace{0.35em}",
            rendered,
        )
        self.assertIn(r"\hspace{0.35em}\path{bus_id}\hspace{0.35em}", rendered)
        self.assertIn(r"\hspace{0.35em}\path{submission_id}\hspace{0.35em}", rendered)
        self.assertIn(r"\hspace{0.35em}\path{validation_status}\hspace{0.35em}", rendered)
        self.assertIn(
            r"\hspace{0.35em}\texttt{python scripts/check\_submission.py --file submission.csv}\hspace{0.35em}",
            rendered,
        )

    def test_long_observed_output_keeps_each_token_readable(self):
        observed = "team=team-blue sites=2 rows=2 buses=2 generators=1 storage=1 horizon=None"
        rendered = MODULE._markdown_text(f"observed output: `{observed}`")

        self.assertIn(
            r"\hspace{0.35em}\texttt{\detokenize{team=team-blue}}\hspace{0.25em}\allowbreak"
            r"\texttt{\detokenize{sites=2}}\hspace{0.25em}\allowbreak"
            r"\texttt{\detokenize{rows=2}}\hspace{0.25em}\allowbreak"
            r"\texttt{\detokenize{buses=2}}\hspace{0.25em}\allowbreak"
            r"\texttt{\detokenize{generators=1}}\hspace{0.25em}\allowbreak"
            r"\texttt{\detokenize{storage=1}}\hspace{0.25em}\allowbreak"
            r"\texttt{\detokenize{horizon=None}}\hspace{0.35em}",
            rendered,
        )


if __name__ == "__main__":
    unittest.main()

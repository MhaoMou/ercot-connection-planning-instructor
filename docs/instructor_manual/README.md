# Instructor Manual

This LaTeX project produces the instructor-facing manual for the ACTIVSg2000
competitive data-center connection-planning game.

The `examples/multi_team_round/` directory contains a compact,
artifact-backed four-team example: full-horizon CSV submissions, the exact
rule file, leaderboard, compact evaluation summary, hashes, and representative
diagnostic/dispatch records. The example uses the current 2028 January--June,
2,000-bus ACTIVSg2000 release. The raw evaluation record is intentionally not
checked in because it is very large.

Compile `main.tex` with the built-in Codex LaTeX compiler. The generated PDF is
an instructor document; students should receive `docs/student_manual/main.pdf`
instead.

# Instructor Manual

This LaTeX project produces the instructor-facing manual for the ACTIVSg2000
competitive data-center connection-planning game.

The current submission contract is a canonical seven-column, one-row-per-site
CSV with no hourly horizon rows. The `examples/multi_team_round/` directory is
retained as a historical long-form compatibility package: its checked-in CSVs
and matching results document an older pipeline and are not current student
input examples. The raw evaluation record is intentionally not checked in
because it is very large.

Compile `main.tex` with the built-in Codex LaTeX compiler. The generated PDF is
an instructor document; students should receive `docs/student_manual/main.pdf`
instead.

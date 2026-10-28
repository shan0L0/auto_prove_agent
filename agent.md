# Agent Notes

## Test Policy

- Do not run the full test suite after every small change.
- Prefer focused tests that cover the files and behavior just changed.
- Run the full test suite only when the change is broad, touches shared infrastructure, may affect multiple execution paths, or the user explicitly asks for it.
- For prompt-only or narrow logging changes, run the smallest relevant tests first and report that full tests were not run.

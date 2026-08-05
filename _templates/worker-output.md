# Worker Output Layout

Dispatcher creates a unique private `workers/<role>/runs/<run-id>/` directory after
each call. It never overwrites an earlier worker response:

- `raw-output.txt` — unmodified stdout.
- `raw-stderr.txt` — unmodified stderr.
- `review-verdict.json` — only for accepted reviewer verdicts.

Raw output is not copied to append-only `log.md`. Claude main may read it for final
synthesis; do not forward it to a different reviewer.

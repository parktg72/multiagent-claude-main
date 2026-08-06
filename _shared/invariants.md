# Invariants

`bin/check-invariants` verifies:

1. Exactly six named workers and no `claude-main` worker.
2. Exact main/worker model, effort, kind, command, access pins, and empty fallback arrays.
3. Sol is sole workspace-write worker; Terra is read-only.
4. Every reviewer/advisor requires no-yes-man contract.
5. Fable tools and session persistence are disabled.
6. Required policies, schemas, templates, main launcher, and exact README argv map exist.
7. Dispatcher contains no dynamic evaluator or whole-root Bubblewrap bind.
8. Dispatcher runs these invariants before every approve/dispatch operation.
9. Review schemas use only supported validation keywords; annotation keywords are
   explicitly allowed and unknown validation keywords fail closed.

Run `bin/check-invariants --self-test`. Self-test mutates only an in-memory copy;
it must detect a removed worker and leave repository files unchanged.

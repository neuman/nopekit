# The bracket before its migration

These are the bytes `examples/bracket/.nopekit/` held at 801cece, the last commit
before checkpoint 1.3 migrated the bracket (U32): its ignore file (`gitignore`
here, so git does not read it as this directory's ignore file), its `ledger.json`
and its run history. Nothing reads them but `tests/_projects.bracket_copy`, which
puts them back around today's model, gates and selftest to give every test the
legacy bracket it was written against — and the R-8 oracle a bracket the old
spine reads natively.

A fixture, not a source: never edit these to make a test pass. The migrated
bracket is `examples/bracket`; `bracket_copy(..., migrated=True)` copies that.

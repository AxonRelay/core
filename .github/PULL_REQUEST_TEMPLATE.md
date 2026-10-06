<!-- What changed and, more importantly, why. Link the issue or ADR if there is one. -->

**Verified by**

<!-- Which tests, or which command you ran by hand. "CI is green" is necessary, not sufficient. -->

**Checklist**

- [ ] Docs updated where behaviour changed (README, docs/, docstrings)
- [ ] No credentials or personal information in the diff or the commit message
- [ ] No regulatory or compliance claim added without a dated primary source, and `python3 scripts/check_doc_claims.py` passes ([docs/regulatory-positioning.md](../docs/regulatory-positioning.md))
- [ ] Squash-merge friendly: one topic, a subject line that reads as a changelog entry

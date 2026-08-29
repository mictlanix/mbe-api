# Phase 0 Research: Price List Retirement

**Feature**: `015-price-list-retirement` | **Date**: 2026-08-29

Eight decisions. R1–R4 shape the retirement, R5–R6 the report, R7–R8 close the edges. The
starting point in every case is what `010-product-merge-integrity` already settled for the other
irreversible catalog operation; where this feature departs from it, the departure is the finding.

---

## R1: Where the operator names the replacement

**Decision**: A `replacement` query parameter on `DELETE /price-lists/{id}`, optional.

**Rationale**: The move and the deletion have to be one transaction (FR-006) — a separate
"reassign these customers" call would leave the client holding a half-finished retirement whenever
the second call fails, which is the exact failure mode the issue objects to in today's
one-DELETE-per-price loop. Putting it on the request that performs the retirement makes the
atomicity structural rather than something the client has to arrange.

A query parameter rather than a request body: `DELETE` with a body is legal but poorly supported by
generated clients, and this codebase has no precedent for one. It is a single integer, which is
what query parameters are for.

Optional rather than required, because a required parameter would break every existing caller and
would force an operator retiring an unassigned list to name a tier that will not be used. Omitted,
the behaviour is exactly today's: refused, with the blocker named (FR-004).

**Alternatives considered**:
- `PUT /price-lists/{id}/customers` as a bulk reassignment, then delete. Rejected: two requests,
  two transactions, and the window between them is a half-retired list.
- Inferring the replacement (lowest id, or a configured default). Rejected outright: which tier a
  customer sits on is a commercial decision, and the spec's whole argument for keeping `customer`
  a blocker is that the API must not make it silently.

---

## R2: The order of operations inside the retirement

**Decision**: move the customers, *then* run the blocker check, then delete the prices, then delete
the list — all before a single commit.

**Rationale**: Running the check first would mean special-casing it ("ignore `customer` if a
replacement was named"), which is a second place encoding the same knowledge. Moving first means
the generic check simply finds nothing left to complain about: after the `UPDATE`, no customer
references the list, so `assert_not_referenced` passes for the ordinary reason rather than an
exempted one. One rule, no special case.

It also gives the right answer when both a replacement *and* some other blocker are present: the
customers move, the check still refuses, and the whole thing rolls back — the operator learns about
the real blocker rather than being told about the customers they already handled.

**Alternatives considered**: check first with `customer` conditionally exempt. Rejected as above —
the exemption would then depend on a request parameter, and `exempt` is meant to name relations the
delete cascades, not relations the caller happened to pre-empt.

---

## R3: One set for what is exempted and what is swept

**Decision**: a module-level `_DELETE_CASCADE = frozenset({'product_price'})`, passed to
`assert_not_referenced(..., exempt=_DELETE_CASCADE)` and used to filter `referencing_columns(PriceList)`
for the cascade — the same set read twice, never two lists.

**Rationale**: This is the shape of GH #112 in miniature. There, a hand-written list of relations
drifted from the relations that actually existed and the merge silently skipped 11 of them. Here the
drift available is smaller but the same kind: a table exempted from the blocker check but not
actually deleted leaves an orphan row and a foreign key violation at `DELETE`; a table deleted but
not exempted is unreachable code behind a 409. Deriving both from one `frozenset` makes both
mistakes unrepresentable — adding a member exempts it and sweeps it in the same edit.

The loop over `referencing_columns` is what ties the set to reality: the member names a table, and
the column to filter on comes from the mapped foreign key rather than from a second constant.

**Alternatives considered**: `product_price_service.delete_for_price_list(db, id)` alongside
`exempt=frozenset({'product_price'})`, mirroring `delete_product` literally, as GH #181 proposes.
Rejected on the strength of the precedent it would copy: `delete_product` states `product_price`
twice, in two files, and nothing checks that the two agree. It works because the set has one member
and always has. Since this feature exists to make the relations self-maintaining (FR-010), copying
the pattern that is one edit away from drifting is the wrong half of `delete_product` to inherit.

---

## R4: Why only the prices are swept, and why everything else blocks

**Decision**: `product_price` is the only member of the cascade set. Every other relation to
`price_list`, present or future, keeps blocking with a 409 (FR-011).

**Rationale**: The split is the merge's configuration-versus-history line, read from the same side.
A `product_price` row is unique on `(product, list)` and states the price of a product *in this
list*: it exists only because the list does, nothing else can reach it once the list is gone, and it
records no event. `delete_product` already reached this conclusion for the other half of the pair.

`customer.price_list` is the opposite: a non-nullable assignment to a commercial tier, where the row
survives the list and needs somewhere to land. It cannot be swept and cannot be nulled, which is
precisely why it gets R1's replacement rather than a place in the cascade set.

Blocking is the default for anything added later, which is deliberate. A relation nobody has thought
about should fail loudly at the 409 rather than be deleted on a guess — the failure is recoverable,
the deletion is not.

**Alternatives considered**: sweeping any relation whose foreign key is part of a unique key
covering the target ("configuration" detected structurally). Rejected — it is a rule inferred from
two examples that would silently start deleting a table someone adds tomorrow. Naming the one table
is a decision on the record.

---

## R5: What the report says

**Decision**: `{items: [{category, count}], total}`, categories as `table.column`, largest first —
the merge preview's shape, computed by the same `find_blocking_references` with no `exempt`.

**Rationale**: Calling it with no `exempt` is what makes FR-008 true by construction: the report
counts every relation, which is exactly the union of what the retirement sweeps (the cascade set)
and what it moves or refuses on (everything else). There is no third list to keep in step.

The flat shape does not distinguish "will be deleted" from "must be reassigned", exactly as the
merge preview does not distinguish moved from discarded. The distinction is knowable from the
category name and is stated in the contract; encoding it in the payload would mean the report
carrying a copy of the cascade set, which is the drift R3 exists to prevent.

**Alternatives considered**: two labelled sections (`deleted`, `reassigned`). Rejected for the
reason above, and because it would answer differently for a relation added later, which by R4 is
neither.

---

## R6: New schemas rather than reusing the merge preview's

**Decision**: `PriceListDeletePreviewItem` / `PriceListDeletePreviewResponse`, structurally
identical to `ProductMergePreviewItem` / `ProductMergePreviewResponse`.

**Rationale**: The two options that avoid duplication are both worse. Reusing the merge's schemas
puts a class named `ProductMergePreviewResponse` in the generated client's price-list call, which is
the readability complaint GH #175 was filed about. Renaming them to something neutral changes the
class name a shipped endpoint generates, breaking every current consumer of the merge preview for a
cosmetic gain. Two four-line models is the cheapest of the three.

Recorded in the plan's Complexity Tracking, since Constitution V requires new schemas to be
justified.

**Alternatives considered**: a generic `ReferenceCountResponse` shared by both. Worth doing the next
time a third caller appears, and cheap then; not worth a breaking client rename for the second.

---

## R7: What guarantees the all-or-nothing

**Decision**: nothing new — the session opened by `get_db` is committed once, at the end of
`delete_price_list`, and any `HTTPException` raised before it leaves the session to close without a
commit, which rolls the transaction back.

**Rationale**: Every statement the retirement issues — the customer `UPDATE`, the price `DELETE`,
the list `DELETE` — runs on the one `AsyncSession`, and there is no intermediate commit. `get_db`'s
`async with AsyncSessionLocal() as session` closes on the way out of the request, and closing an
`AsyncSession` with an open transaction rolls it back. So the 400, the 404 and the 409 all leave the
data untouched without any explicit rollback, and so does an integrity error raised by the final
`DELETE`.

This is worth stating rather than assuming: FR-006 is the requirement most likely to be quietly
broken by a later edit that adds a `commit()` for convenience partway through.

**Alternatives considered**: an explicit `begin_nested` / savepoint. Rejected as machinery for a
guarantee the request scope already provides.

---

## R8: A replacement named when nobody is assigned

**Decision**: accepted; the `UPDATE` matches no rows and the retirement proceeds.

**Rationale**: A client cannot know the assignment count without asking first, so the defensive
habit — always send a replacement — has to be safe. Refusing would make that habit fail
unpredictably, on exactly the lists that are easiest to retire. The replacement is still validated
(exists, not the list itself) so a typo is caught whether or not it would have mattered.

**Alternatives considered**: 422 when the replacement is redundant. Rejected: it punishes the safe
client and tells the operator nothing they can act on.

---

## R9: Authentication, not privilege

**Decision**: both the new report and the changed delete use `get_current_user`, matching every
other endpoint in the price-list router.

**Rationale**: `SystemObject.PRICE_LISTS` exists (value 5), but no endpoint in
`app/api/v1/endpoints/price_lists.py` gates on it — the whole router is authenticated-only. Gating
just the new report would be inconsistent inside its own file and would 403 users who can already
list, create, update and delete price lists, which discloses strictly less than the endpoints they
already have.

Whether the price-list router should require `SystemObject.PRICE_LISTS` throughout is a real
question, and a pre-existing one that predates this feature. Raising it here would mean changing
five endpoints nobody asked about (Constitution III), so it is noted and left alone.

**Alternatives considered**: `require_privilege(SystemObject.PRICE_LISTS, AccessRight.READ)` on the
report only. Rejected as above.

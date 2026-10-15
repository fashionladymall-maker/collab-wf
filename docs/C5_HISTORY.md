# C5 installation history contract

`live.collect_install_materials(inst, *, tool_root=None)` is a preparatory,
read-only collector. It reads the tool's `local/install`, the project's
`workflow/evidence/install`, host `reviews` and `releases`, transport and lane
configuration, review rules, and the configured verifier file. Missing stores
are explicit descriptor entries. It includes ordinary baseline blobs and
review raw outputs, rejects symlinks and malformed JSON objects, and checks
file bytes and directory membership again before returning.

The result contains `state`, `descriptor`, `materials_sha256`, parsed
`documents`, and pending work. Its descriptor uses `install-materials/1` with
`complete=false`; `authorization` is always `unavailable`. Collection does not
validate an installation chain, prove provenance, execute a verifier, or change
the behavior of `live.check` or goals. A recorded `allowed` or `granted` value is
material to inspect, never an authorization from this collector.

The stability check is optimistic. It does not provide an OS transaction or
protection against malicious changes by the same user between reads.

## Required B-owned historical API

The following is a proposed next interface, not an implemented public API:

```text
collect_install_history(context, install_ref, reader)
  -> {schema, state, facts, sources, selectors, diagnostics}
```

`context` identifies the calling project by canonical root and project name,
the turn, an execution identity `{id, nonce}`, the expected installation target
kind and canonical root, and explicit host `{local, tool_root}` paths. The
nonce is allocated for the invocation before execution; the final event
sequence is diagnostic because it is unavailable at invocation start.
`install_ref` must be emitted by the actual B2b consumer and carried unchanged
into the corresponding exec event:

```json
{
  "schema": "install-ref/1",
  "tx_id": "tx-<id>",
  "request_id": "rq-<id>",
  "content_id": "<sha256>",
  "package_sha256": "<sha256>",
  "target": {"kind": "tool", "root": "<canonical-root>"},
  "prepared_sha256": "<sha256>"
}
```

The exec writer must retain the reference only from the actual invocation's
structured result. A user-provided reference, command string, basename, or
time-window match alone cannot link the exec event to an installation. An
unsuccessful invocation remains a failed, timed-out, or unknown attempt even
when another invocation installed the same package.

The reader must expose `bytes(path)`, `directory(path)` and
`git(root, argv)`. `bytes` returns the frozen bytes or `None` for recorded
absence. `directory` returns an explicit directory descriptor with `state` and
ordered `members` (name, type, mode). `git` returns
`{returncode, stdout, stderr}` with byte outputs from controlled read-only
queries against the recorded commit/tree. The executable, environment,
repository configuration and exact query arguments must be frozen and replayed
by C. Reads outside the collected set are rejected. B must perform every
semantic file read, selector enumeration and Git query through this reader.
C can wrap its strict readers and add every result to the frozen window
descriptor, then replay the same selectors and queries during revalidation.

The historical result should use a versioned schema, with state
`valid|failed|unknown|corrupt|unsupported|changed`. Its facts must separately
express completion, authorization at install start, synthetic provenance,
target and exact request/content/attempt/package/transaction identities,
installation and review times, and the original base/rules identities.
`sources` and `selectors` identify every raw dependency consumed; diagnostics
explain unknown or rejected cases. A synthetic fixture cannot become production
authorization.

Required historical checks:

- Verify prepared, baseline, started, review-install and completed records and
  their links, original base proof, raw review materials, frozen package,
  current trust roots, and all content requests/attempts.
- Enforce `review_started <= review_completed <= result_registered <=
  install_started <= install_completed`. A review registered on a previous day
  can qualify when valid at installation start. Registration after installation
  cannot retroactively authorize it.
- Validate review expiry at installation start; distinguish historical
  compliance from current eligibility to reinstall.
- Revalidation must detect changed raw materials, changed current verifier
  bytes/pin/identity/rules and newly registered block decisions.
- Do not call `consume` or recompute the historical base from today's installed
  target. Do not silently drop failed, partial, recovered, late or unknown
  attempts.

Once the B API and exec reference are available, C can bind them into the
frozen read set and replace the legacy fourth-segment lookup. The complete C5
timing and provenance work remains pending until its positive and negative
integration tests pass. Goals verification remains disabled for the new
collection protocol until the separate authorization integration is complete.

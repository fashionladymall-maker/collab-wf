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

## B-owned historical API

The historical verifier is separate from the preparatory material collector:

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
C wraps its strict readers in `live_history.CaptureReader` and adds every
result to the frozen window descriptor. `FrozenReader` replays raw bytes,
directory members and binary Git replies without opening disk or running Git.
Fresh progress rediscovers the same sources and selectors and repeats every
controlled query. Git object reads accept only complete commit/tree/blob OIDs;
HEAD substitution, config includes, replace refs, shallow repositories,
alternates and partial clones are unsupported.

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

## Live and goals consumption

`ops.run_exec` allocates the execution ID and nonce before starting the command.
The protected consumer writes its prepared reference to that invocation's
inherited receipt FD; ops preserves the receipt, metadata and event. C compares
all three representations and output sizes before calling the historical
verifier. The structured fourth segment uses those references even when paths
contain spaces or the command ends in flags or Python source. Legacy command
parsing remains a reporting path and cannot supply modern authorization.

Historical review times are compared to the original installation start, not
the exec start or the current clock. C additionally requires installation
start/completion to fall within the actual invocation. Ops currently records
whole seconds, so its end represents that entire second. A project apply event
counts as an automatic child operation only when its exact reference and exec
identity match a valid project transaction and a driver-owned exec event.

Modern goals use `live-collection/3`. A stored `granted` bit is never accepted on
its own: normal progress freshly captures the inputs, reruns the historical
provider, re-evaluates the report, validates the full tool/project closure,
checks goal/dependency definitions and verifies each replaced output's evidence.
The selected candidate rounds must contain both tool and project transaction
histories, and all ten strict live criteria must pass even for a narrow segment
mapping. A grant also requires approved goals, a clean matching tool, a matching
project fingerprint and non-synthetic provenance. `live-collection/2` remains a
collection-only protocol.

Tool cleanliness uses controlled Git status with repository fsmonitor and the
untracked cache disabled. Its envelope binds the index and local ignore and
attribute files as well as status output. External filters, submodule settings,
and external attribute/exclude configuration are unsupported. A changed ignore
dependency invalidates reuse even when status still reports a clean checkout.

Synthetic histories can be complete and historically valid while authorization
remains unavailable. The real-install integration tests always use explicitly
synthetic review/verifier fixtures and never produce a production grant.
Missing, corrupt, failed, recovered, late or unmatched attempts stay visible;
newly registered blocks, raw-material drift and current trust-root changes
invalidate reuse.

The reader probes run in `wflib.selftest_live_history`; the actual exec,
protected installation, frozen replay and goals tests run in
`wflib.selftest_c5_integration`. Both are included in the isolated C lane.

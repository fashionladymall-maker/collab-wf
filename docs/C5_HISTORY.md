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
project fingerprint, non-synthetic provenance and a positive production trust
registration (below). `live-collection/2` remains a
collection-only protocol.

Tool cleanliness uses controlled Git status with repository fsmonitor and the
untracked cache disabled. Its envelope binds the index and local ignore and
attribute files as well as status output. External filters, submodule settings,
and external attribute/exclude configuration are unsupported. A changed ignore
dependency invalidates reuse even when status still reports a clean checkout.

## Production trust registry (C5-G1)

Historical validity is not a production grant. B's provenance marks a history
synthetic only when the verifier configuration or output says so; a fixture
verifier whose transport entry and output both omit `synthetic` and whose bytes
are re-pinned is reported as `platform_verified` with `synthetic=false`. The
host trust root is therefore completed by an explicit, positive, versioned
registration. Nothing grants by default.

`LOCAL/production_trust.json`, schema `production-trust/1`:

```json
{
  "schema": "production-trust/1",
  "registrations": [
    {
      "id": "<[A-Za-z0-9][A-Za-z0-9._-]{0,63}>",
      "class": "production",
      "verifier": {"path": "<canonical absolute path>", "sha256": "<sha256>"},
      "identity": {"platform": "<reviewer_identity.platform>", "models": ["<model_verified>"]}
    }
  ]
}
```

Every object has exactly these keys. `class` is `production` or `synthetic`;
ids, verifier paths and verifier digests are unique, so one byte sequence
cannot be both production and synthetic. A grant (`authorization=available`)
requires, in addition to every historical and closure condition:

- the registry is present, strict JSON and schema-valid;
- the current transport `review_verifier.path` equals one registration's path,
  its pinned sha256 equals the registered sha256, and transport does not mark
  it synthetic;
- the registration class is `production`;
- the frozen verifier bytes hash to the registered sha256;
- every valid window history has `synthetic=false` (fact and provenance),
  `level=platform_verified`, `verifier_sha256` equal to the registration,
  `reviewer_identity.platform` equal to `identity.platform` and
  `model_verified` in `identity.models`.

The result is `production_trust` (`production-trust-check/1`) with `state`
`absent|corrupt|unsupported|unregistered|mismatch|synthetic|changed|unavailable|trusted`
and one denial reason, e.g. `production_trust_registry_absent`,
`production_trust_registry_invalid:<field>`,
`production_trust_verifier_unregistered`,
`production_trust_verifier_sha256_mismatch`,
`production_trust_registration_synthetic`,
`production_trust_transport_synthetic`,
`production_trust_history_synthetic`,
`production_trust_platform_mismatch`, `production_trust_model_mismatch`.
Reasons are ordered: historical reasons first, then exec-less transaction
reasons (`install_transaction_corrupt`, `install_transaction_time_unknown`),
then trust reasons, so an
explicitly synthetic history still reports `synthetic_install_history`.

The registry is a frozen input of every modern window
(`descriptor.trust_inputs`, `production-trust-inputs/1`) and is bound through
`window_inputs_sha256`. Creating, editing or deleting it changes the
descriptor, so earlier collections and grants become `changed`; a registration
added after a denial never upgrades the stored denial, a fresh collection is
required. A recreated file is a new identity and stays invalid until
recollected. Frozen replay reads the registry and verifier bytes only from the
snapshot. Goal bindings record `binding.production_trust` (state, registry
digest, registration id and class); progress re-evaluates and compares it.
Records written before this registry existed fail revalidation as `changed`.

Like `transport.json` and `lanes.json`, the registry is host configuration that
the same OS user can edit. It prevents misconfiguration and self-claims from
becoming production grants; it does not defend against a malicious host owner.

The selftest positive cases register a local fixture verifier as `production`
under the id `selftest-registry-contract` in an isolated temporary host. They
exercise the registry contract; the grant names that selftest registration and
is not evidence of any real platform verifier.

## Legacy installation entries without receipts

`live._bound_histories` wraps B's provider. Every structured invocation is
first bound with `exec_binding` and classified from its full metadata command;
all of these reads go through the same strict capture reader and are replayed
by the frozen reader, so capture and replay derive rows from the same frozen
sources. Referenced rows come only from the provider. Rows without a reference
are rebuilt by C:

- an exec event without `install_exec` that is a legacy installation command
  becomes `unknown` with `legacy_install_reference_unavailable`;
- an exec with `install_exec` and no receipt becomes `unknown` with
  `installation_receipt_has_no_transaction` when its full invocation metadata
  command (events keep only 300 characters) is a legacy installation command,
  or `unsuccessful_exec_has_no_transaction_reference` when it failed or was
  interrupted;
- an ordinary successful command is not an installation attempt, even if its
  text contains `wf self-update` (for example inside `echo`);
- an inner review (`wf drill review`, any entry form) runs a complete inner
  review/driver cycle in an isolated child project and LOCAL. It is never
  handed to B's provider: the row is `unknown`, `reference=null`, with
  `inner_review_child_project_not_bound`, plus
  `inner_review_receipt_not_grafted` when the receipt nevertheless carries
  references (for example a child-project reference). Child references are
  never grafted onto the parent project or tool, and their presence does not
  turn the whole window corrupt. The inner run's own exit status does not
  matter; an outer exit 0 still yields the unknown row. An inner review
  anywhere in a compound command takes precedence, so a parent `wf apply` in
  the same command conservatively stays unknown rather than valid.

History capture is gated by `live._history_required(events)`: any exec with a
structured receipt (`install_exec`) or any exec whose recorded command is a
legacy installation or inner review. A pure legacy window (no structured
receipt at all) is therefore collected through `_bound_histories` as well
(`review_inputs` uses `live-install-history/1`, trust inputs are frozen) and
its legacy rows block authorization. Window `complete` is true only when
structured receipts exist, so such a window is collected but explicitly
incomplete. Pre-receipt events are classified from the event command, which
ops truncates to 300 characters; structured invocations use the full metadata
command. Direct `live.check` routing is described under install transactions
below.

## Install transactions without exec receipts

An installation attempt can exist only in a target-owned transaction store,
for example a driver's in-process `release.apply_zip` that writes `prepared`
and then fails on a real review block, with no exec and no event. Every
capture therefore lists both stores through the strict capture reader: the
project's `workflow/evidence/install` and the tool's `<tool_root>/local/install`.
The frozen listings decide the history gate together with the exec gate: a
store containing any `tx-*`/`restore-*` member, or a store fault, opens the
history protocol even in a window with no exec at all. Without either,
`review_inputs` stays `unavailable` but records `transaction_stores` (each
`present`/`absent`), and the frozen listings make a later transaction
invalidate that window.

Every transaction no provider row references becomes its own row: `seq=null`,
`reference=null`, diagnostic `install_transaction_without_exec_receipt`, and a
`transaction` object (`install-transaction/1`: store, id, job
`install|restore`, record states, `attempt_at`, `attribution`). No reference is
derived from it. Each `*.json` record must be a regular file, strict JSON,
sealed with B's `body_sha256`, and match the writer's protocol:

| job | record | schema | state | id |
|---|---|---|---|---|
| install | `baseline.json` | `baseline/1` | — | `tx_id` |
| install | `prepared.json` | `install_tx/1` | `prepared` | `tx_id`, `lock.acquired_at` timezone-aware |
| install | `started`/`failed`/`partial.json` | `install_tx/1` | same as name | `tx_id`, `prepared_sha256` |
| install | `completed.json` | `install_completed/1` | `completed` | `tx_id`, `prepared_sha256` |
| install | `recovered.json` | `install_recovered/1` | `recovered` | `tx_id` |
| restore | `prepared.json` | `install_restore/1` | `prepared` | `restore_id`, `prepared_at` timezone-aware |
| restore | `completed`/`partial.json` | `install_restore/1` | same as name | `restore_id`, `prepared_sha256` |

Directory names must be `tx-<32 hex>` or `restore-<32 hex>`. Any deviation is
explicit and makes the row `corrupt`: `transaction_record_unknown:<name>`,
`transaction_record_schema:<name>`, `transaction_record_field:<name>:<field>`
(state, id, lock/prepared_at, prepared_sha256), `transaction_record_missing:
prepared.json`, `transaction_record_corrupt:<name>` (JSON or seal),
`transaction_record_not_file:<name>`, `transaction_id_invalid`,
`transaction_member_not_directory`. A bad start field is never treated as an
ordinary unknown time. Otherwise the row is `failed` for a failed/partial
record and `unknown` for everything else (a completed transaction without an
exec receipt is still unknown).

Store paths are read through `live._StoreReader`. At capture, an
`unsupported` or `unreadable` problem (symlinked store, a store that is not a
directory, a symlinked or special member, permission errors) is recorded as a
fault in `review_inputs.transaction_faults` and becomes a corrupt row:
`transaction_store_<state>:<detail>` (for example `symlink directory`,
`NotADirectoryError`), `transaction_directory_<state>:<detail>`, or
`transaction_record_<state>:<name>`. Frozen replay reproduces these faults
from the descriptor and reads everything else through the frozen reader, so
it opens no file; fresh revalidation and the freeze check re-probe each fault
and report `changed` when it no longer holds. Other problems still abort the
collection. A symlinked member makes its whole store a fault.

The attempt is attributed by its validated start time to the round whose first
send it follows, rounds running until the next send (`turns.jsonl`, frozen).
Rows attributed to an evaluated round block authorization through the window
rule (`install_history:failed|unknown|corrupt`) and count in criterion 4:
`attempts.unbound_transactions`, `total`, and `failed`/`unknown`, with status
`attempt_failed` and the transaction ids in the explanation. Independent of the
window, any corrupt row (`install_transaction_corrupt`) or time-unknown row
(`install_transaction_time_unknown`) blocks (`install_transactions`,
`install-transactions/1`, which also lists the store states). Transactions
before the first round are listed but do not block. The tool store is shared
by every project on the host, so a tool install performed through another
project's exec during this window is unbound here and blocks this window
conservatively. Goals re-evaluate and compare `install_transactions` like the
other derived facts.

### Direct `live.check`

A direct `live.check` uses the same frozen collection as goals
(`_capture_inputs` → `_evaluate_snapshot`) whenever the window has history
facts, so it lists the same unbound transactions, criterion-4 counts and
decision (`live._direct_history_route`):

- `strict`: a structured receipt exists, or a transaction store holds a
  `tx-*`/`restore-*` member or cannot be listed. A capture problem returns the
  explicit `input_problem` report; it never falls back to a report that omits
  the transaction.
- `legacy`: only classified legacy/inner exec commands. If strict capture is
  impossible, the legacy report is returned with
  `authorization = {state: unavailable, why: [install_history_uncollected],
  input_problem}`.
- otherwise (an ordinary window without transactions) the report is
  unchanged.

The store listing used for routing is not evaluated; the frozen listing is.
On the frozen path with history, the report carries
`authorization` (`live-report-authorization/1`: `state`, ordered `why`,
`unbound_transactions`), identical for direct and snapshot use and bound into
`report_sha256`.

Boundaries: when B's provider already reads a store for a bound history, it
reads it strictly, so a store fault there still aborts that collection as
before. Capture and routing use the process `WF_HOME` as tool root, so a
fixture that does not isolate `WF_HOME` sees that host's real tool store.

`live._legacy_install` tokenizes with a quote-aware shell lexer and recognizes direct
`wf`/`wf.py` invocations (any path, including `python3 '<space path>/wf.py'`),
leading assignments and `env`/`timeout`/`nohup`/`time`/`nice`/`sudo`/`command`/
`exec` wrappers, `bash|sh|zsh -c` and `eval` nesting, global `--project`/`--json`
options and the `apply` (project), `self-update` (tool) and `drill review`
(`inner_review`, never a tool attempt) entries. The lexer keeps quoted text
(including quoted `;`, `&&`, `|`, `(`) out of operators, treats redirection
targets, numeric file-descriptor prefixes, comments and here-document bodies
as non-commands, skips reserved words (`if`/`then`/`do`/`{`/`!` …), splits on
unquoted newlines, and recursively checks `$(...)` and backquote bodies,
including those inside double quotes and unquoted here-documents. Function
definitions (`name() {...}`, `name() (...)`, `function name {...}`) do not run:
their bodies, including substitutions and here-documents inside them, count
only where the function is invoked later in the same command (also through
`$(name)`, `eval` or another function). A function shadows a command of the
same name, including `wf`; `command`, `env`, `exec` and other wrappers bypass
functions. A command
that mentions a wf entry but cannot be tokenized is `parsed=false`, kind
unknown. Rows carry `legacy_command` `{schema, entry, kind, parsed}` as a
diagnostic only; no installation reference is derived from a basename or
command string. Scripts, aliases, variables and functions defined outside the
command (for example in a sourced file) are not resolved. Criterion 4 counts tool references and legacy tool entries; the legacy
report path reads the parsed first package operand and reports
`旧版安装命令无法解析（unknown）` when none exists. A missing legacy package or
review result is frozen as an absent source and reported (`没有记录` /
`版本包哈希对不上或已不在`) instead of aborting the whole collection. Project legacy entries are
visible as unknown history rows and block authorization.

Synthetic histories can be complete and historically valid while authorization
remains unavailable. The real-install integration tests always use explicitly
synthetic review/verifier fixtures and never produce a production grant.
Missing, corrupt, failed, recovered, late or unmatched attempts stay visible;
newly registered blocks, raw-material drift and current trust-root changes
invalidate reuse.

The reader probes run in `wflib.selftest_live_history`; the actual exec,
protected installation, frozen replay and goals tests run in
`wflib.selftest_c5_integration`. Both are included in the isolated C lane.
The production-trust registry and legacy-entry tests run in
`wflib.selftest_c5_trust` (`python3 -m unittest wflib.selftest_c5_trust`);
they are not wired into an existing aggregator.

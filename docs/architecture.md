# Architecture

The classifier is independent of Git, execution policy and CI. Structured adapters produce
suite/name test identities, terminal statuses, build failures and parser errors. `classify`
assigns one outcome per identity. The executor bounds each invocation and kills its POSIX process
group on timeout.

The Git CLI resolves immutable commit IDs, finds their merge base, and exports regular tracked
files into temporary directories. It never checks out or restores the user's tree. Only declared
executable test files are copied from head into base. Files a guard inspects, including test
sources, therefore retain their base versions. External mode copies executables into a third
directory; the two measured trees remain complete unmodified snapshots. This supports guards
that scan their own tracked source. Snapshot preparation refuses links and submodules rather than
silently following files outside the measurement. Commands run without a `.git` directory.

Head is run twice for automatic selection; base runs once, with three additional confirmations
when any selected status is assertion or compile red. Explicit selection uses the library's
base-first measurement order. Every selected test must satisfy the chosen policy. P2p acceptance
is explicit and does not rewrite evidence into fail-to-pass. All reported head failures and parser
errors remain rejecting, even outside the selection.

The composite Action installs the package from its own release tree and runs the same CLI. Build
and publish are separate jobs. Only the release publish job obtains an OIDC token, behind the
release environment. No repository-specific tracker, agent hook, credential helper or deployment
logic is included.

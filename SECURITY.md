# Security

This tool executes the command you supply against code from Git commits. Code, dependency scripts,
wrappers and tests can access the runner's filesystem, network, environment and credentials.
The tool is not a sandbox. Run untrusted changes in an isolated disposable environment without
secrets or write credentials. Do not execute contributor code in `pull_request_target` jobs.

Timeouts kill POSIX process groups, but cannot contain a process that deliberately escapes its
group. Structured reports can be fabricated by hostile code; this is a test-quality check for
cooperative workflows, not a proof against adversarial authors. Finite reruns cannot rule out all
flakes. Absolute report paths outside the measured directory are rejected. Exported snapshots
reject links and submodules. Temporary trees are removed on normal completion and exceptions;
an abrupt host termination can leave temporary files for the host to clean.

Report a vulnerability privately using the publishing repository's Security Advisories page.
If private reporting is not enabled yet, contact the maintainer through their public profile
before sharing exploit details. Version 0.1 is the initial supported release series.

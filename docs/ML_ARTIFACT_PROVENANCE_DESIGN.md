# ML Artifact Provenance: Threat Model and Design

## Status

**Design only — not implemented or enabled.** This document proposes a security boundary for ML artifacts before any signing mechanism is built. It does not authorize paper promotion, deployment, key creation, or changes to existing artifacts.

## 1. Problem statement

The ML artifact loader verifies that a model's weight bytes match the SHA-256 digest recorded in its adjacent JSON manifest. This detects accidental corruption or a changed weight file when the manifest remains unchanged.

It does **not** establish who produced the artifact. An actor able to replace both the weights and the adjacent manifest can calculate a new digest and make the pair internally consistent. The read-only audit command has the same limitation: `digest_matches` means byte consistency, not trusted provenance.

The goal is to establish that a paper-eligible artifact was approved by a trusted producer, and that the exact manifest and weight bytes being loaded are the ones that producer approved.

## 2. Assets and security goals

Protect:

- Model weight bytes and their associated manifest/metadata.
- The model identity, strategy/symbol/market/timeframe metadata, eligibility decision, and test metrics used for promotion.
- The trusted verification key configuration.
- The paper activation decision and its audit trail.
- Signing credentials and release workflow permissions.

Required properties:

1. **Authenticity:** only an authorized producer can create an artifact accepted as trusted.
2. **Integrity:** changes to signed metadata or weight bytes invalidate verification.
3. **Context binding:** a signature covers model identity and all security-relevant manifest fields, not just the weights digest.
4. **Fail-closed promotion:** paper eligibility requires valid trusted provenance once enforcement is explicitly enabled.
5. **Traceability:** verification and rejection reasons can be audited without logging secrets.
6. **Safe migration:** existing unsigned artifacts are not silently upgraded to trusted status.

## 3. Threat model

### In scope

- Accidental or malicious edits to a weight file.
- Replacement of both a manifest and weight file in the artifact directory.
- Manifest substitution between model IDs or incompatible strategy contexts.
- Artifact copied from an untrusted machine or experiment directory.
- Stale or revoked signing keys.
- Unauthorized or compromised CI workflow attempting to sign artifacts.
- Path/symlink manipulation around artifact lookup (existing path-containment checks remain mandatory).
- Downgrade attempts that remove signature metadata or present a legacy manifest after enforcement.

### Trust assumptions that must be explicit

- The runtime's trusted public key or key-set configuration is protected independently from the artifact directory.
- The signing private key is not stored beside artifacts, committed to source control, or readable by the runtime process.
- Only a reviewed, protected release workflow or approved operator can invoke signing.
- Repository branch protections, workflow permissions, and secret/KMS access controls are configured to match this model.

If an attacker can modify both the artifact and the runtime's trusted public key configuration, signature verification cannot protect authenticity. If an attacker can invoke the signer with arbitrary input, a cryptographic signature alone does not make that input safe.

### Out of scope

- Proving that a model is profitable or statistically robust.
- Preventing an authorized signer from approving a poor model.
- Securing a compromised runtime host or operating system.
- Replacing the existing ML eligibility gate, chronological holdout discipline, or manual release checks.

## 4. Proposed artifact contract

Prefer a detached signature or a clearly specified signed envelope, with a canonical serialization format. The signed payload should include at minimum:

- A schema/version identifier and signature algorithm identifier.
- Stable model ID and artifact format version.
- SHA-256 digest of the exact weight bytes.
- Security-relevant manifest fields: eligibility status, strategy identity and parameters/version, symbol, market, timeframe, feature/schema version, target, model type, threshold, and test metrics used by the gate.
- Producer identity and signing-key ID.
- Creation timestamp and, if supported by the release process, an immutable experiment/run reference.

Canonicalization must be deterministic and documented. The verifier must reject unknown algorithms, malformed signatures, duplicate/ambiguous fields, unsupported schema versions, and missing required fields. It must not trust values copied from an unsigned adjacent manifest.

The verifier must calculate the digest from the weight bytes it is about to load, verify the signature over the canonical payload, and ensure the signed model ID and context match the requested model. Existing path containment and regular-file/symlink checks remain in force. Verification must happen before any `joblib.load` call.

A detached signature keeps the existing manifest readable but adds a sidecar; a signed envelope may simplify atomicity but requires a manifest-format migration. Select one only after reviewing the current `ModelStore` contract and deployment/configuration model.

## 5. Key management options

| Option | Strengths | Risks / requirements |
|---|---|---|
| Managed KMS/HSM signer | Private key stays outside repository and artifact host; centralized access policy and audit | Provider-specific setup, availability, cost, rotation and recovery plan |
| Protected CI signing key | Can fit an existing release pipeline | Secret exposure risk; strict workflow permissions, environment approvals, protected branches and limited token permissions required |
| Operator-held offline key | Strong separation from runtime and CI | Operational burden, human error, availability and rotation procedures |

**Do not select or implement an option until the deployment owner confirms the available infrastructure and threat assumptions.** Never generate or commit a production key as part of this change. Never store a private key in `ml_models/` or any artifact bundle.

The verification public key/key-set must be pinned in an independently trusted runtime configuration or trust store—not accepted from the artifact being verified. Define key IDs, validity, rotation overlap, emergency revocation, and behavior when the trusted key source is unavailable.

## 6. Fail-closed behavior and compatibility

After an explicitly approved enforcement cutover:

- Paper promotion and paper loading reject missing, invalid, unknown-key, revoked-key, or mismatched signatures.
- A valid weight digest without a valid signature is insufficient.
- Legacy unsigned artifacts remain **untrusted**. Do not add a digest or signature to an unverified old artifact and call that proof of origin.
- Rebuild artifacts from a trusted experiment and have the authorized signing workflow sign the approved output.
- If signature verification cannot complete, do not activate the model. Emit a bounded, actionable error and an audit event without sensitive data.
- Do not silently fall back to unsigned mode in production or paper runtime.
- Define an explicit rollback plan before cutover. Rollback must not re-enable unsigned loading without a reviewed, time-bounded exception and visible operational control.

The read-only audit command remains useful for inventory but is not a signing tool and must never repair or bless artifacts.

## 7. Test plan

Unit and integration coverage should include:

- Valid signature and matching weight digest.
- One-byte weight tampering.
- Manifest field tampering, including eligibility metrics, model ID, strategy context, threshold, and feature version.
- Signature tampering, malformed signature, unknown algorithm, unknown key ID, and revoked key.
- Missing signature and legacy manifest under enforcement.
- Unsupported schema and non-canonical/ambiguous payload rejection.
- Artifact substitution across model IDs or strategy contexts.
- Symlink/path escape and file replacement between verification and load; minimize time-of-check/time-of-use risk by verifying and loading the same opened file or a safely pinned immutable copy where feasible.
- Key rotation with old/new key overlap, revocation, unavailable trust configuration, and expired keys if expiry is part of the chosen policy.
- Atomic publish behavior so a partial artifact bundle is never considered ready.
- No private key material in logs, exceptions, test artifacts, or repository history.

Use generated ephemeral keys only in isolated tests. Tests must not depend on a real production secret or live KMS account.

## 8. Rollout gates

1. **Architecture decision:** confirm threat assumptions, signer choice, public-key distribution, canonical payload, algorithm, and ownership.
2. **Prototype:** implement verifier and deterministic signature fixtures in isolation; keep enforcement disabled.
3. **Adversarial tests:** demonstrate that tampering with either manifest or weights is rejected and that legacy artifacts fail closed when enforcement is enabled.
4. **Artifact inventory:** run `python -m ml.audit_artifacts --directory <actual-artifact-directory> --json` on the owning machine. Preserve output as evidence; do not modify artifacts.
5. **Trusted regeneration:** identify legacy artifacts and regenerate from trusted experiments. Re-evaluate eligibility on the required untouched test segment before signing.
6. **Protected signing path:** restrict signer invocation and verify workflow permissions/branch protection independently.
7. **Staged enforcement:** exercise against a non-production/paper test environment, review logs and rollback behavior, then obtain explicit approval before enabling enforcement for real paper configurations.
8. **Post-change review:** verify CI, key rotation/revocation runbook, artifact recovery, and release evidence.

No phase automatically authorizes the next. Passing tests does not prove signer infrastructure or runtime key configuration is secure.

## 9. Open decisions — required before implementation

- Where does the application run, and which operator/team owns the runtime host?
- Is there an approved KMS/HSM or protected CI signing mechanism?
- Who is authorized to sign and approve model promotion?
- Which exact manifest fields are stable and security-relevant in the current code?
- Should the signature be detached or embedded in a versioned envelope?
- Where is the public key set pinned and how is it updated safely?
- What is the required key rotation/revocation SLA?
- Is there a permitted emergency exception, and who approves it?
- What evidence must be retained for a model promotion?

## References in this repository

- [ML pipeline and promotion rules](ML.md)
- [Read-only artifact audit](ML.md#read-only-artifact-compatibility-audit)
- [Release verification checklist](RELEASE_VERIFICATION_CHECKLIST.md)


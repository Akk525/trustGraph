# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in TrustGraph itself, please report it privately rather than opening a public GitHub issue.

**Contact:** open a [GitHub Security Advisory](https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/creating-a-repository-security-advisory) on this repository, or email the maintainer directly.

We will acknowledge receipt within 72 hours and aim to respond with an assessment within 7 days.

---

## Attack Surface

TrustGraph reads contract source files, optionally calls an external API, and invokes a local subprocess. The relevant security boundaries:

| Surface | Risk | Notes |
|---|---|---|
| Gemini API call | Prompt injection via crafted `.sol` input | Function source text is sent to Gemini. Malicious contracts could attempt to manipulate the explanation output. Gemini output is informational only — it cannot affect findings. |
| `forge test` subprocess | Foundry test execution | Generated tests run inside Foundry's isolated environment but inherit the local shell. Do not run TrustGraph against untrusted contract paths in a sensitive environment without reviewing generated tests first. |
| Contract file paths | Path traversal | TrustGraph reads from a user-specified path. Standard filesystem permissions apply. |

---

## Out of Scope

- Vulnerabilities in the Solidity contracts that TrustGraph analyses (finding these is TrustGraph's job)
- Vulnerabilities in Foundry, forge, or solc
- Vulnerabilities in the Gemini API or Google AI infrastructure

---

## Supported Versions

TrustGraph is currently at v0.1.0. No long-term support commitment exists at this stage. Security fixes will be applied to the latest release only.

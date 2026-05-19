# Security Policy

## Scope

This document covers security issues in the **TrustGraph VS Code extension** itself — the extension code, its VS Code API usage, and its interaction with the TrustGraph CLI subprocess.

For vulnerabilities in the core TrustGraph CLI (the Python analysis tool), refer to the [root repository SECURITY.md](https://github.com/Akk525/trustGraph/blob/main/SECURITY.md).

---

## Reporting a Vulnerability

Report vulnerabilities privately. Do not open a public GitHub issue for security matters.

**Preferred method:** Open a [GitHub Security Advisory](https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/creating-a-repository-security-advisory) on this repository.

We aim to acknowledge receipt within 72 hours and provide an initial assessment within 7 days.

---

## Extension Security Considerations

| Surface | Risk | Notes |
|---|---|---|
| CLI subprocess invocation | Command injection | The CLI path and contract path come from VS Code workspace settings. Malicious workspace settings (e.g., from a shared `.vscode/settings.json`) could point to an arbitrary binary. The extension does not sanitise the `cliPath` setting beyond passing it to `spawn`. Review workspace settings before opening untrusted projects. |
| `report.json` parsing | Malformed JSON | The extension reads `report.json` from the output directory. A crafted report file could cause parsing errors. Parsing is wrapped in standard try/catch; no code execution occurs from parsed content. |
| Webview HTML rendering | XSS | The finding detail webview renders finding content as HTML. Content is escaped via `textContent` assignment in JavaScript, not `innerHTML`. Direct HTML injection from report data is not possible. |
| Output channel | No risk | CLI stdout/stderr is written to a VS Code Output channel using the VS Code API. No execution occurs. |

---

## Out of Scope

- Vulnerabilities in the contracts that TrustGraph analyses
- Vulnerabilities in the Foundry toolchain (`forge`, `anvil`)
- Vulnerabilities in the Gemini API
- Vulnerabilities in VS Code itself

---

## Supported Versions

The extension is at v0.1.0. Security fixes are applied to the latest published version only.

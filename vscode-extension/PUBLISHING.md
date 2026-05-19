# Publishing the TrustGraph VS Code Extension

## Prerequisites

- Node.js ≥ 18
- `vsce` installed: `npm install -g @vscode/vsce`
- A Visual Studio Marketplace publisher account at [marketplace.visualstudio.com/manage](https://marketplace.visualstudio.com/manage)
- A Personal Access Token (PAT) from [dev.azure.com](https://dev.azure.com)

## PAT Setup

1. Go to [dev.azure.com](https://dev.azure.com) → your organisation → User Settings → Personal Access Tokens.
2. Create a new token:
   - **Organisation**: All accessible organisations
   - **Scopes**: Marketplace → **Manage**
   - Set an appropriate expiry date.
3. Copy the token — it is only shown once.

## Login

```bash
vsce login akk525
# paste your PAT when prompted
```

## Package (local VSIX)

```bash
cd vscode-extension
npm run compile
npx @vscode/vsce package
# produces trustgraph-<version>.vsix
```

## Install Locally (test before publish)

```bash
code --install-extension trustgraph-0.1.0.vsix
```

To uninstall: `code --uninstall-extension akk525.trustgraph`

## Publish

```bash
npx @vscode/vsce publish
```

To publish a specific version bump in one step:

```bash
npx @vscode/vsce publish patch   # 0.1.0 → 0.1.1
npx @vscode/vsce publish minor   # 0.1.0 → 0.2.0
npx @vscode/vsce publish major   # 0.1.0 → 1.0.0
```

This updates `package.json` version, commits it, and publishes.

## Version Bump Flow (manual)

1. Update `version` in `package.json`.
2. Add a section to `CHANGELOG.md` for the new version.
3. Commit: `git commit -am "chore: bump extension to vX.Y.Z"`
4. Tag: `git tag vscode-vX.Y.Z`
5. Run `npx @vscode/vsce publish`.

## Before Every Publish — Checklist

- [ ] `npm run compile` succeeds with no TypeScript errors
- [ ] `npx @vscode/vsce package` succeeds with no errors or warnings
- [ ] `CHANGELOG.md` has an entry for the new version
- [ ] `package.json` version matches the CHANGELOG entry
- [ ] Screenshots in `media/screenshots/` are current and not broken
- [ ] `trustgraph.cliPath` default is empty (PATH lookup, not a local dev path)

## Remaining Manual Steps Before First Publish

- **Extension icon**: Add a 128×128 PNG as `media/icon.png` and set `"icon": "media/icon.png"` in `package.json`. Without this the Marketplace shows a grey placeholder.
- **Gallery banner**: Optionally add `"galleryBanner": {"color": "#1a1a2e", "theme": "dark"}` to `package.json` for a branded Marketplace header.
- **Publisher verified**: Confirm the `akk525` publisher ID exists and is verified at [marketplace.visualstudio.com/manage](https://marketplace.visualstudio.com/manage).

#!/usr/bin/env node
// Pre-package safety check — runs before every `npm run package`
// Fails fast if packaging would include secrets, missing icons, or broken references.

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
let failed = false;

function fail(msg) {
  console.error(`[check-package-safety] FAIL: ${msg}`);
  failed = true;
}

function ok(msg) {
  console.log(`[check-package-safety] ok: ${msg}`);
}

// 1. Reject .env files in extension root (should never be here)
const envFiles = ['.env', '.env.local', '.env.production', '.env.development'];
for (const f of envFiles) {
  if (fs.existsSync(path.join(ROOT, f))) {
    fail(`.env file found at ${f} — must not be packaged`);
  }
}
ok('no .env files in extension root');

// 2. Verify required media assets exist
const requiredMedia = [
  'media/trustgraph-icon.png',
  'media/trustgraph-icon.svg',
];
for (const asset of requiredMedia) {
  const abs = path.join(ROOT, asset);
  if (!fs.existsSync(abs)) {
    fail(`required asset missing: ${asset}`);
  } else {
    const size = fs.statSync(abs).size;
    if (size < 100) fail(`asset looks empty or corrupt: ${asset} (${size} bytes)`);
    else ok(`${asset} exists (${size} bytes)`);
  }
}

// 3. Verify package.json icon field points to an existing file
const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
if (pkg.icon) {
  const iconPath = path.join(ROOT, pkg.icon);
  if (!fs.existsSync(iconPath)) {
    fail(`package.json "icon" references missing file: ${pkg.icon}`);
  } else {
    ok(`package.json icon exists: ${pkg.icon}`);
  }
}

// 4. Verify activity bar icon field exists
const activityIcons = (pkg.contributes?.viewsContainers?.activitybar || []).map(c => c.icon);
for (const icon of activityIcons) {
  if (!icon) continue;
  const iconPath = path.join(ROOT, icon);
  if (!fs.existsSync(iconPath)) {
    fail(`activitybar icon references missing file: ${icon}`);
  } else if (!icon.endsWith('.svg')) {
    fail(`activitybar icon must be an SVG, got: ${icon}`);
  } else {
    ok(`activitybar icon exists and is SVG: ${icon}`);
  }
}

// 5. Scan packageable files for obvious secret patterns
const SECRET_PATTERNS = [
  /AIza[0-9A-Za-z_-]{35}/,          // Google API key
  /GEMINI_API_KEY\s*=\s*["'][^"']+["']/,  // hardcoded Gemini key assignment
  /ghp_[0-9A-Za-z]{36}/,            // GitHub PAT
  /npm_[0-9A-Za-z]{36}/,            // npm token
  /sk-[a-zA-Z0-9]{32,}/,            // OpenAI-style key
];

const SCAN_DIRS = ['out', 'src'];
for (const dir of SCAN_DIRS) {
  const abs = path.join(ROOT, dir);
  if (!fs.existsSync(abs)) continue;
  const files = fs.readdirSync(abs, { recursive: true, withFileTypes: true });
  for (const entry of files) {
    if (!entry.isFile()) continue;
    const filePath = path.join(entry.parentPath ?? entry.path, entry.name);
    const ext = path.extname(entry.name);
    if (!['.js', '.ts', '.json', '.md'].includes(ext)) continue;
    const content = fs.readFileSync(filePath, 'utf8');
    for (const pattern of SECRET_PATTERNS) {
      if (pattern.test(content)) {
        fail(`secret pattern detected in ${path.relative(ROOT, filePath)} (pattern: ${pattern.source.slice(0, 20)}...)`);
      }
    }
  }
}
ok('no secret patterns found in packageable source files');

// 6. Check README references images that exist
const readmePath = path.join(ROOT, 'README.md');
if (fs.existsSync(readmePath)) {
  const readme = fs.readFileSync(readmePath, 'utf8');
  const imgMatches = [...readme.matchAll(/!\[.*?\]\((\.\/[^)]+)\)/g)];
  for (const [, imgRef] of imgMatches) {
    const imgPath = path.join(ROOT, imgRef);
    if (!fs.existsSync(imgPath)) {
      fail(`README references missing image: ${imgRef}`);
    } else {
      ok(`README image exists: ${imgRef}`);
    }
  }
}

if (failed) {
  console.error('\n[check-package-safety] Pre-package checks FAILED. Fix issues above before packaging.');
  process.exit(1);
} else {
  console.log('\n[check-package-safety] All pre-package checks passed.');
}

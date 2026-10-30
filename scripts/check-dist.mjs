#!/usr/bin/env node
// Checks the built site in dist/: every root-relative href/src must carry the base path
// (BASE_PATH, default "/") and point to a file that exists. Run after `npm run build`.
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const dist = new URL('../dist/', import.meta.url).pathname;
const base = (process.env.BASE_PATH || '/').replace(/\/?$/, '/');
const ATTR = /\s(?:href|src|poster)=(?:"([^"]*)"|'([^']*)')/g;
const SRCSET = /\ssrcset=(?:"([^"]*)"|'([^']*)')/g;

function* htmlFiles(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) yield* htmlFiles(path);
    else if (name.endsWith('.html')) yield path;
  }
}

function targetExists(pathname) {
  const rel = decodeURIComponent(pathname.slice(base.length));
  const candidates = rel === '' || rel.endsWith('/') ? [join(dist, rel, 'index.html')] : [join(dist, rel), join(dist, rel, 'index.html')];
  return candidates.some((c) => existsSync(c));
}

const problems = new Map();
let checked = 0;
for (const file of htmlFiles(dist)) {
  const html = readFileSync(file, 'utf8');
  const links = [...html.matchAll(ATTR)].map((m) => m[1] ?? m[2]);
  for (const m of html.matchAll(SRCSET)) links.push(...(m[1] ?? m[2]).split(',').map((s) => s.trim().split(/\s+/)[0]));
  for (const raw of links) {
    if (!raw || !raw.startsWith('/') || raw.startsWith('//')) continue;
    checked++;
    const pathname = raw.replace(/&amp;/g, '&').split(/[?#]/)[0];
    let issue;
    if (!pathname.startsWith(base)) issue = `missing base path ${base}`;
    else if (!targetExists(pathname)) issue = 'target not found';
    if (issue) {
      const key = `${raw} (${issue})`;
      if (!problems.has(key)) problems.set(key, new Set());
      problems.get(key).add(relative(dist, file));
    }
  }
}

if (problems.size) {
  for (const [key, pages] of problems) {
    const list = [...pages];
    console.error(`✗ ${key}\n    on ${list.slice(0, 3).join(', ')}${list.length > 3 ? ` and ${list.length - 3} more` : ''}`);
  }
  console.error(`\n${problems.size} broken internal link(s) out of ${checked} checked (base ${base}).`);
  process.exit(1);
}
console.log(`✓ ${checked} internal links checked, all resolve (base ${base}).`);

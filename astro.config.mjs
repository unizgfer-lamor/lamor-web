// @ts-check
import { readFileSync } from 'node:fs';
import { unified } from '@astrojs/markdown-remark';
import sitemap from '@astrojs/sitemap';
import { defineConfig } from 'astro/config';
import rehypeBase from './src/lib/rehype-base.mjs';

// Deployment target. GitHub Actions sets these from repository variables; the defaults are the
// final production address. For a preview at https://<org>.github.io/<repo>/ set BASE_PATH=/<repo>.
const site = process.env.SITE_URL || 'https://lamor.fer.hr';
const base = (process.env.BASE_PATH || '/').replace(/\/?$/, '/');

// Old Quilt CMS URLs -> new pages. Astro does not prefix redirect targets with `base`, so we do.
const legacy = JSON.parse(readFileSync(new URL('./src/data/legacy-redirects.json', import.meta.url), 'utf8'));
const redirects = Object.fromEntries(
  Object.entries(legacy).map(([from, to]) => [from, `${base.replace(/\/$/, '')}${to}`]),
);

export default defineConfig({
  site,
  base,
  trailingSlash: 'always',
  build: { format: 'directory' },
  compressHTML: true,
  redirects,
  // Draft previews are reachable by link only.
  integrations: [sitemap({ filter: (page) => !new URL(page).pathname.includes('/news/preview/') })],
  markdown: {
    processor: unified({ rehypePlugins: [[rehypeBase, { base }]] }),
  },
});

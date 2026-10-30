import { Marked } from 'marked';
import { url } from './url';

// Renders Markdown stored in frontmatter (e.g. the news `lead`). Content bodies go through
// Astro's own pipeline instead; this only needs to fix root-relative links for the base path.
const marked = new Marked({
  gfm: true,
  walkTokens(token) {
    if (token.type === 'link' || token.type === 'image') token.href = url(token.href);
    if (token.type === 'html') {
      token.text = token.text.replace(
        /\b(src|href)=(["'])(\/(?!\/)[^"']*)\2/g,
        (_: string, attr: string, q: string, path: string) => `${attr}=${q}${url(path)}${q}`,
      );
    }
  },
});

export function renderMarkdown(source: string | undefined): string {
  return source ? (marked.parse(source, { async: false }) as string) : '';
}

/** First paragraph as plain text, for meta descriptions and RSS. */
export function plainSummary(source: string | undefined, maxLength = 300): string {
  if (!source) return '';
  const text = source
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/<[^>]+>/g, '')
    .replace(/[*_`#>]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  return text.length > maxLength ? `${text.slice(0, maxLength - 1).replace(/\s+\S*$/, '')}…` : text;
}

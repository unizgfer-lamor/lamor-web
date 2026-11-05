import type { CollectionEntry } from 'astro:content';
import { getPublications } from './collections';

type Publication = CollectionEntry<'publications'>['data'];

/** Order used on the publications page: newest year first, then newest CROSBI entry first. */
export function byYearThenEntry(a: { data: Publication }, b: { data: Publication }): number {
  return (
    b.data.year - a.data.year ||
    (b.data.croris ?? 0) - (a.data.croris ?? 0) ||
    a.data.title.localeCompare(b.data.title)
  );
}

const FOLD: Record<string, string> = { đ: 'd', Đ: 'D', ł: 'l', Ł: 'L', ø: 'o', Ø: 'O', ß: 'ss', æ: 'ae', Æ: 'AE' };

/** 'Koledić' -> 'koledic' (for citation keys). */
function keyPart(text: string): string {
  return text
    .replace(/[đĐłŁøØßæÆ]/g, (c) => FOLD[c])
    .normalize('NFD')
    .replace(/\p{M}/gu, '')
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '');
}

const STOP_WORDS = new Set(['a', 'an', 'the', 'on', 'of', 'for', 'in', 'to', 'and', 'with', 'from', 'by', 'via', 'towards', 'toward']);

/** Citation key like 'koledic2026generalizing': first author's surname, year, first significant title word. */
function baseKey(p: Publication): string {
  const surname = keyPart((p.authors[0]?.name ?? 'lamor').split(',')[0]) || 'lamor';
  const word = p.title.split(/[\s\-–—:/]+/).map(keyPart).find((w) => w && !STOP_WORDS.has(w)) ?? '';
  return `${surname}${p.year}${word}`;
}

/** Escape LaTeX special characters in a field value (UTF-8 letters such as č, ć, ž stay as they are). */
function tex(value: string): string {
  return value
    .replace(/\\/g, '\\textbackslash{}')
    .replace(/([{}&%$#_])/g, '\\$1')
    .replace(/~/g, '\\textasciitilde{}')
    .replace(/\^/g, '\\textasciicircum{}')
    .replace(/\s+/g, ' ')
    .trim();
}

/** Keep the capitalisation of acronyms and mixed-case words (SLAM, LiDAR, Gauss-Helmert) in BibTeX styles. */
function protectCase(title: string): string {
  return title.replace(/[^\s:,;()]+/g, (word) => (/^.+\p{Lu}/u.test(word.replace(/^\p{P}+/u, '')) ? `{${word}}` : word));
}

function entry(p: Publication, key: string): string {
  const bib = p.bib ?? {};
  const conference = p.category === 'conference';
  const fields: [string, string | undefined][] = [
    ['author', p.authors.map((a) => tex(a.name)).join(' and ')],
    ['title', protectCase(tex(p.title.replace(/[.\s]+$/, '')))],
    conference ? ['booktitle', tex(bib.booktitle || p.venue)] : ['journal', tex(p.venue)],
    ['volume', bib.volume && tex(bib.volume)],
    ['number', bib.number && tex(bib.number)],
    ['pages', bib.pages && tex(bib.pages)],
    ['publisher', bib.publisher && tex(bib.publisher)],
    ['address', bib.address && tex(bib.address)],
    ['year', String(p.year)],
    ['doi', p.doi],
    ['url', !p.doi ? p.url : undefined],
    ['note', p.status === 'accepted' ? 'Accepted for publication' : undefined],
  ];
  const body = fields
    .filter(([, v]) => v)
    .map(([k, v]) => `  ${k.padEnd(9)} = {${v}}`)
    .join(',\n');
  return `@${conference ? 'inproceedings' : 'article'}{${key},\n${body}\n}`;
}

let cache: Promise<Map<string, string>> | undefined;

/**
 * BibTeX entry of every publication, by publication id, in page order. Keys are made unique across the
 * whole list (a, b, … suffixes, assigned oldest entry first so existing keys stay stable when new papers
 * appear). A hand-written `bibtex` value (Publication corrections / Extra publications) is used verbatim.
 */
export function getBibtex(): Promise<Map<string, string>> {
  cache ??= (async () => {
    const pubs = (await getPublications()).sort(byYearThenEntry);
    const keys = new Map<string, string>();
    const groups = new Map<string, string[]>();
    for (const { id, data } of [...pubs].reverse()) {
      const base = baseKey(data);
      groups.set(base, [...(groups.get(base) ?? []), id]);
    }
    for (const [base, ids] of groups) {
      ids.forEach((id, i) => keys.set(id, ids.length === 1 ? base : `${base}${String.fromCharCode(97 + (i % 26))}${i >= 26 ? Math.floor(i / 26) : ''}`));
    }
    return new Map(pubs.map(({ id, data }) => [id, data.bibtex?.trim() || entry(data, keys.get(id)!)]));
  })();
  return cache;
}

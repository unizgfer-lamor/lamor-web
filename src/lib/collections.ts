import { getCollection, type CollectionEntry } from 'astro:content';

/** News items on the homepage, and per page of the news archive. */
export const HOME_NEWS_COUNT = 8;
export const NEWS_PAGE_SIZE = 10;

const byOrder =<T extends { data: { order: number } }>(a: T, b: T) => a.data.order - b.data.order;

/** Published news, newest first. */
export async function getNews(): Promise<CollectionEntry<'news'>[]> {
  const news = await getCollection('news', ({ data }) => !data.draft);
  return news.sort((a, b) => b.data.date.valueOf() - a.data.date.valueOf());
}

/** Unpublished news (draft: true), newest first. Only the hidden /news/preview/ pages show them. */
export async function getDrafts(): Promise<CollectionEntry<'news'>[]> {
  const drafts = await getCollection('news', ({ data }) => data.draft);
  return drafts.sort((a, b) => b.data.date.valueOf() - a.data.date.valueOf());
}

export async function getPeople(categories: CollectionEntry<'people'>['data']['category'][]) {
  const people = await getCollection('people', ({ data }) => categories.includes(data.category));
  return people.sort((a, b) => byOrder(a, b) || a.data.name.localeCompare(b.data.name, 'hr'));
}

export async function getSorted<C extends 'projects' | 'equipment' | 'partners' | 'courses'>(collection: C) {
  const entries = (await getCollection(collection)) as CollectionEntry<C>[];
  return entries.sort((a, b) => byOrder(a as never, b as never));
}

export async function getPublications(category?: 'journal' | 'conference') {
  return getCollection('publications', ({ data }) => !category || data.category === category);
}

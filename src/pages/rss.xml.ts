import rss from '@astrojs/rss';
import type { APIContext } from 'astro';
import { getNews } from '../lib/collections';
import { plainSummary } from '../lib/markdown';
import { getSettings } from '../lib/site';
import { url } from '../lib/url';

export async function GET(context: APIContext) {
  const settings = await getSettings();
  const news = (await getNews()).slice(0, 50);
  return rss({
    title: `${settings.short_title} news`,
    description: settings.description,
    site: context.site!,
    items: news.map((entry) => ({
      title: entry.data.title,
      pubDate: entry.data.date,
      author: entry.data.author,
      description: plainSummary(entry.data.lead || entry.body, 500),
      link: url(`/news/${entry.id}/`),
    })),
  });
}

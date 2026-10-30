import { getEntry } from 'astro:content';

export async function getSettings() {
  const entry = await getEntry('settings', 'site');
  if (!entry) throw new Error('Site settings (src/data/site.yaml) are missing');
  return entry.data;
}

/** GitHub "edit this file" link for a content entry, if the repository URL is configured. */
export function editUrl(repoUrl: string | undefined, filePath: string | undefined): string | undefined {
  if (!repoUrl || !filePath) return undefined;
  return `${repoUrl.replace(/\/$/, '')}/edit/main/${filePath.replace(/^\.?\//, '')}`;
}

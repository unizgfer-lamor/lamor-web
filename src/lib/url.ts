const BASE = import.meta.env.BASE_URL.replace(/\/$/, '');

/** Prefix a root-relative path ("/news/") with the site base path; leave absolute URLs alone. */
export function url(path: string): string;
export function url(path: string | undefined): string | undefined;
export function url(path: string | undefined): string | undefined {
  if (!path || !path.startsWith('/') || path.startsWith('//')) return path;
  if (BASE && (path === BASE || path.startsWith(`${BASE}/`))) return path;
  return BASE + path;
}

/** Path of the current page without the base, for "active" navigation highlighting. */
export function stripBase(pathname: string): string {
  return BASE && pathname.startsWith(BASE) ? pathname.slice(BASE.length) || '/' : pathname;
}

export function isExternal(href: string): boolean {
  return /^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith('//');
}

export interface NavItem {
  label: string;
  href: string;
  /** Path prefixes that mark this item as active (defaults to `href`). */
  match?: string[];
  children?: NavItem[];
}

// Main navigation. Kept in code (not in the CMS) so an accidental edit can't break every page.
export const nav: NavItem[] = [
  { label: 'Home', href: '/', match: ['/news/'] },
  {
    label: 'Research',
    href: '/research/projects/',
    match: ['/research/'],
    children: [
      { label: 'Projects', href: '/research/projects/' },
      { label: 'Equipment', href: '/research/equipment/' },
      { label: 'Our partners', href: '/research/partners/' },
    ],
  },
  { label: 'Teaching', href: '/teaching/' },
  { label: 'Publications', href: '/publications/' },
  {
    label: 'People',
    href: '/people/',
    match: ['/people/', '/contact/'],
    children: [
      { label: 'Current', href: '/people/' },
      { label: 'Alumni', href: '/people/alumni/' },
      { label: 'Contact', href: '/contact/' },
    ],
  },
  { label: "Student's corner", href: '/students-corner/' },
];

export function isActive(item: NavItem, path: string): boolean {
  if (item.href === '/' && path === '/') return true;
  const prefixes = item.match ?? [item.href];
  return prefixes.some((prefix) => prefix !== '/' && path.startsWith(prefix)) || (item.href !== '/' && path.startsWith(item.href));
}

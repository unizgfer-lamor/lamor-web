import { visit } from 'unist-util-visit';

const ATTRS = ['src', 'href', 'poster'];
const RAW_ATTR = /\b(src|href|poster)=(["'])\/(?!\/)/g;

/**
 * Prefix root-relative links ("/images/x.png") in Markdown content with the site base path,
 * so content written for https://lamor.fer.hr/ also works under https://<org>.github.io/<repo>/.
 * Raw HTML inside Markdown (e.g. migrated tables) is still a string at this stage, so it is
 * rewritten with a regex.
 */
export default function rehypeBase({ base = '/' } = {}) {
  const prefix = base.replace(/\/$/, '');
  if (!prefix) return () => {};
  const fix = (value) =>
    typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') && !value.startsWith(`${prefix}/`)
      ? prefix + value
      : value;

  return (tree) => {
    visit(tree, (node) => {
      if (node.type === 'element' && node.properties) {
        for (const attr of ATTRS) {
          if (attr in node.properties) node.properties[attr] = fix(node.properties[attr]);
        }
      } else if (node.type === 'raw') {
        node.value = node.value.replace(RAW_ATTR, (_, attr, quote) => `${attr}=${quote}${prefix}/`);
      }
    });
  };
}

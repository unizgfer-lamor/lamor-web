import { getBibtex } from '../lib/bibtex';

// All journal and conference papers as one BibTeX file, regenerated on every build (i.e. after each
// CROSBI sync and every edit of the publication corrections).
export async function GET() {
  const entries = [...(await getBibtex()).values()];
  const header = `% LAMOR publications (journal and conference papers), ${entries.length} entries.\n% Generated from CROSBI on ${new Date().toISOString().slice(0, 10)}. Encoding: UTF-8.\n`;
  return new Response(`${header}\n${entries.join('\n\n')}\n`, {
    headers: { 'Content-Type': 'application/x-bibtex; charset=utf-8' },
  });
}

# LAMOR website

Source of the website of the **Laboratory for Autonomous Systems and Mobile Robotics (LAMOR)**,
University of Zagreb Faculty of Electrical Engineering and Computing — <https://lamor.fer.hr>.

It is a static site built with [Astro](https://astro.build) from Markdown files, published with GitHub Pages.
The look follows the previous Quilt CMS site (navy/crimson theme, carousel, project sidebar).

- **Editing content** (news, people, projects, …): see [EDITING.md](EDITING.md). No programming needed.
- **Publications** are fetched automatically from [CRORIS/CROSBI](https://www.croris.hr/crosbi/) by a Python tool.

## Running locally

### 1. Install the tools (once per machine)

| Tool | Needed for | Install (Linux/macOS) |
|---|---|---|
| Git + Git LFS | getting the code and the PDFs | `sudo apt install git git-lfs` (macOS: `brew install git git-lfs`), then `git lfs install` |
| Node.js 24 (≥ 22.22) | the website | via [fnm](https://github.com/Schniz/fnm): `curl -fsSL https://fnm.vercel.app/install \| bash`, open a new terminal, `fnm install 24` (nvm works too) |
| [uv](https://docs.astral.sh/uv/) | only the publications tool in `tools/` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` (uv downloads Python 3.12 itself if needed) |

### 2. Get the code

```sh
git clone <repo-url> lamor-web
cd lamor-web
```

The clone downloads ~590 MB of PDFs from Git LFS. To skip them, clone with
`GIT_LFS_SKIP_SMUDGE=1 git clone <repo-url> lamor-web`. The site still builds; the PDF links just
point to small placeholder files. `git lfs pull` fetches them later.

### 3. Run the website

```sh
fnm use          # switch to the Node version in .nvmrc (24); with nvm: nvm use
npm ci           # install dependencies (first time, and whenever package-lock.json changes)
npm run dev      # development server: http://localhost:4321  (Ctrl+C stops it)
```

Changes to Markdown files, components and styles show up in the browser right away. Site search only
works in a production build:

```sh
npm run build    # build into dist/ and create the search index
npm run preview  # serve dist/ at http://localhost:4321
```

If port 4321 is taken, Astro picks the next free one (it prints the URL), or pass `-- --port 4400`.

### 4. Run the publications tool (optional)

```sh
cd tools
uv sync                     # create tools/.venv (first time)
uv run lamor-pubs sync      # fetch new/changed publications from CRORIS into the SQLite database
uv run lamor-pubs export    # write src/data/publications.json and tools/publications/report.md
uv run pytest               # tests
```

Restart `npm run dev` afterwards to see the new publication list. More in [Publications from CRORIS](#publications-from-croris).

### All commands

| Command | What it does |
|---|---|
| `npm run dev` | Development server with live reload (site search is only available after a build). |
| `npm run build` | Production build into `dist/` plus the Pagefind search index. |
| `npm run preview` | Serve `dist/` locally. |
| `npm run check` | Type-check the code and validate all content against the schemas. |
| `npm run check:dist` | After a build: verify every internal link and image in `dist/` resolves. |

To test the site under a sub-path (as on `https://<org>.github.io/<repo>/`):
`BASE_PATH=/lamor-web/ npm run build && BASE_PATH=/lamor-web/ npm run check:dist`.

## Project structure

```
src/content/            Markdown content, one file per item (edited by people / Pages CMS)
  news/ people/ projects/ equipment/ partners/ courses/ theses/ pages/
src/data/
  site.yaml             site settings: contact, carousel, sidebar links
  nav.ts                main navigation
  publications.json     GENERATED from CRORIS — do not edit by hand
  publication-overrides.yaml   manual corrections to CRORIS entries (hide, PDF, venue, …)
  publications-extra.yaml      publications that are not in CRORIS
  legacy-redirects.json old Quilt CMS URLs → new pages
src/content.config.ts   content schemas (fields and types of every collection)
src/pages/              routes; src/components/, src/layouts/, src/styles/global.css
public/                 static files: design/ (logos, carousel), images/, files/, _news/ (migrated)
tools/                  Python tools: publication sync (pubs) and the one-off migration (migrate)
.pages.yml              Pages CMS configuration
.github/workflows/      deploy.yml (Pages), check.yml (pull requests), publications-sync.yml (manual)
```

Old asset URLs (`/images/<number>/…`, `/_news/…`) were kept so links to PDFs on the old site keep
working after `lamor.fer.hr` points to the new site.

## Publications from CRORIS

The tool in `tools/` keeps a SQLite database (`tools/publications/lamor-pubs.sqlite`) of all CRORIS
publications of the researchers listed in `tools/publications/config.yaml`, and exports the journal and
conference papers (2010 onward) to `src/data/publications.json`.

```sh
cd tools
uv run lamor-pubs sync          # fetch new/changed records from the CRORIS API (--full refetches everything)
uv run lamor-pubs export        # classify, de-duplicate and write src/data/publications.json + report.md
uv run pytest                   # tests
```

- Researchers, year cut-off, which CRORIS types count as journal/conference papers, venue name fixes:
  `tools/publications/config.yaml`.
- Wrong or missing entries: fix them in CROSBI if possible; otherwise use `src/data/publication-overrides.yaml`
  (by CROSBI id: `hide`, `category`, `title`, `venue`, `pdf`, …) or add to `src/data/publications-extra.yaml`.
- BibTeX: the export also writes structured `bib` fields (volume, number, pages, booktitle, publisher,
  address). `src/lib/bibtex.ts` turns them into an entry per paper (shown under the *BibTeX* button).
  A `bibtex` value in the overrides or
  extras replaces the generated entry.
- `tools/publications/report.md` summarises each export (counts per year, exclusions, merged duplicates).

**Automation.** `.github/workflows/publications-sync.yml` runs sync + export and opens a pull request with
the changes (only when the publication list changed). It currently runs **only manually**
(Actions → "Publications sync" → Run workflow). To run it
periodically, uncomment the `schedule:` block in that file. The repository setting
*Settings → Actions → General → "Allow GitHub Actions to create and approve pull requests"* must be enabled.

## Deployment (GitHub Pages)

1. Push the repository to GitHub (with Git LFS).
2. *Settings → Pages → Build and deployment → Source: GitHub Actions*.
3. Every push to `main` builds and deploys (`.github/workflows/deploy.yml`). The site URL and base path are
   taken from the Pages configuration automatically (`https://<org>.github.io/<repo>/` until a custom domain
   is set). Repository variables `SITE_URL` / `BASE_PATH` override them.
4. Optional: in `src/data/site.yaml` set `repo_url` to enable "Edit this page" links.

**Switching lamor.fer.hr to the new site**

1. *Settings → Pages → Custom domain*: `lamor.fer.hr` (a `CNAME` file is not needed with Actions deploys).
   Verify the domain for the GitHub organisation to prevent takeovers.
2. FER IT: point `lamor.fer.hr` to GitHub Pages (`CNAME <org>.github.io`).
3. Enable *Enforce HTTPS* once the certificate is issued, then re-run the deploy workflow.
4. Old URLs: `/lamor/...` pages redirect to their new locations (`src/data/legacy-redirects.json`).

## PDFs and Git LFS

Paper and thesis PDFs and news attachments migrated from the old site (~590 MB) are stored with Git LFS
(see `.gitattributes`). The deploy workflow caches LFS objects so builds don't use up the LFS bandwidth
quota (10 GiB/month on free plans). Files uploaded through Pages CMS (`public/files/`,
`public/images/<section>/`) are normal Git files.

The built site is ~610 MB; GitHub Pages allows sites up to 1 GB, so large new files (videos, big PDFs)
are better linked from elsewhere (e.g. YouTube, the FER repository) than added to the repo.

## Migration from the old site (one-off)

`tools/lamor_tools/migrate` scraped the Quilt CMS site into `src/content/` and `public/`
(`uv run lamor-migrate --help`). Raw pages are cached in `tools/.cache/` (not committed); reports with
items that needed manual attention are in `tools/reports/`.

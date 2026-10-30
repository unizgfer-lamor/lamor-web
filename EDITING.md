# Editing the LAMOR website

All content lives in this repository as small text files (Markdown with a short header). After any change
is saved to the `main` branch, the website rebuilds and updates by itself within about two minutes.

There are two ways to edit:

1. **Pages CMS** (recommended): forms in the browser, image upload, no Markdown knowledge needed.
2. **Directly on GitHub**: edit the files in the browser (good for quick text fixes).

## 1. Pages CMS

1. Open <https://app.pagescms.org> and sign in with GitHub. If you don't have a GitHub account, ask a
   repository admin to invite you as a collaborator by e-mail (Pages CMS → the repository → *Collaborators*).
2. Choose the `lamor-web` repository, branch `main`.
3. Pick a section on the left (News, People, Projects, …), open an item or click *Add an entry*, fill in
   the form and click *Save*. Saving creates a commit and the website updates automatically.

### Adding a news item

- **Title**, **Date**, **Author**.
- **Image**: optional, shown next to the news item (around 240 px wide is enough; it is shown at 200 px).
- **Lead**: the text shown on the homepage and in the news archive. A few sentences.
- **Full text**: optional. If filled in, the list shows a *More…* link to a page with the full article.
- **Attachments**: optional PDFs or other files.
- **Draft**: tick it to save the item without publishing it.

### People, projects, equipment, …

- **People** have a *Category* (Faculty, PhD student, Alumni, …) that decides where they appear, and an
  *Order* number (smaller numbers come first within the category). Photos should be portraits, about 2:3.
- **Projects**: *Status* (Ongoing/Past) decides the section. Tick *Show the logo in the "Current projects"
  sidebar* for projects that should appear in the right column of every page.
- **Pages** are free-form pages such as *Student's corner*. A new page `my-page` appears at `/my-page/`
  (add a link to it from another page or ask a maintainer to add it to the menu).
- **Site settings**: contact details, homepage carousel images, sidebar links.

### Publications

Journal and conference papers are imported automatically from **CROSBI** (CRORIS) for Ivan Petrović,
Ivan Marković, Luka Petrović and Marija Seder. To get a paper on the website, **enter it in CROSBI**.

- To fix or hide an imported paper, or attach a PDF, use **Publication corrections** with the paper's
  CROSBI id (the number in its CROSBI link).
- Papers that will never be in CROSBI can be added under **Extra publications**.
- **Theses** are edited by hand in the *Theses* section.

## 2. Editing on GitHub

Open the repository on GitHub, go to the folder, open a file and click the pencil icon (*Edit this file*).
To add an item, use *Add file → Create new file* in the right folder. Commit directly to `main` (or open a
pull request if you want someone to review it first).

| What | Folder | File name |
|---|---|---|
| News | `src/content/news/` | `2026-10-21-short-title.md` |
| People | `src/content/people/` | `name-surname.md` |
| Projects | `src/content/projects/` | `acronym.md` |
| Equipment / partners / courses / theses | `src/content/<section>/` | `short-name.md` |
| Free-form pages | `src/content/pages/` | `page-name.md` → `/page-name/` |
| Images | `public/images/<section>/` | referenced as `/images/<section>/file.jpg` |
| Documents | `public/files/<section>/` | referenced as `/files/<section>/file.pdf` |

Every file starts with a header between `---` lines (field names must stay exactly as in the examples),
followed by optional Markdown text.

**News item**

```markdown
---
title: LAMOR presents new results at ICRA 2027
date: 2027-05-20
author: Ivan Marković
image: /images/news/icra-2027.jpg
lead: |-
  Our paper **"Some great title"** was presented at [ICRA 2027](https://2027.ieee-icra.org/).
---

Optional full text of the article. Leave it out for short news.
```

**Person**

```markdown
---
name: Ana Anić
category: phd   # faculty | senior | phd | manager | alumni | guest | intern
group: Motion Planning and Control Group
photo: /images/people/ana-anic.jpg
profile: https://www.fer.unizg.hr/en/ana.anic
order: 200
---
```

**Project**

```markdown
---
acronym: NEWPROJ
title: Full project title
status: ongoing  # ongoing | past
logo: /images/projects/newproj.png
sidebar: true
website: https://newproj.fer.hr
funding: Croatian Science Foundation (Total budget 200,000 EUR)
start: 2027-01-01
end: 2030-12-31
pi: Ivan Petrović
---

One paragraph describing the project.
```

If a field name or value is wrong (e.g. an unknown category), the website build fails and the old version
stays online. The failed run is shown under the repository's **Actions** tab with the file and field that
caused it; fix the file and save again.

## Markdown in two minutes

```markdown
**bold**, *italic*, [link text](https://example.com)

- bullet list item
1. numbered list item

## Heading

![Image description](/images/news/photo.jpg)
```

## Previewing locally (optional)

See [README.md](README.md#quick-start-developers): `npm ci && npm run dev`, then open <http://localhost:4321>.

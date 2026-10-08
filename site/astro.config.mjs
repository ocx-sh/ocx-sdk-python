// The Python SDK section of ocx.sh, mounted at /integrations/python/ (a claim in the theme's nav.json).
import starlight from '@astrojs/starlight';
import ocxTheme from '@ocx-sh/theme/starlight';
import { defineConfig } from 'astro/config';
import starlightPydocs, { createPydocsSidebarGroup } from 'starlight-pydocs';
import ocxGroupTitles from './plugins/group-titles.mjs';
import { API_BASE, GROUPS, PACKAGE } from './reference-groups.mjs';

const HIDDEN = createPydocsSidebarGroup();

export default defineConfig({
  base: '/integrations/python/',
  // Starlight reads it before plugins run, so the theme checks it rather than sets it.
  trailingSlash: 'always',
  devToolbar: { enabled: false },
  integrations: [
    starlight({
      title: 'ocx-sdk',
      customCss: ['./src/styles/site.css'],
      description: 'Python SDK for OCX.',
      social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/ocx-sh/ocx-sdk-python' }],
      editLink: { baseUrl: 'https://github.com/ocx-sh/ocx-sdk-python/edit/main/docs/' },
      // The docs use ```pycon fences (Sybil runs their >>> examples); Shiki has no such grammar.
      expressiveCode: { shiki: { langAlias: { pycon: 'python', 'python-contract': 'python', 'python-no-run': 'python' } } },
      plugins: [
        ocxTheme(),
        ocxGroupTitles(),
        starlightPydocs({
          packages: GROUPS.map((g) => ({
            name: PACKAGE,
            search: ['../src'],
            docstringStyle: 'google',
            // Inherited members repeat on every subclass; the base class page documents them once.
            filters: { inherited: false },
            base: `${API_BASE}/${g.slug}`,
            members: { include: g.include.map((p) => `${PACKAGE}.${p}`) },
            // Not in the sidebar (no placeholder): the API index lists the groups; prev/next walk them.
            sidebar: { label: g.label, group: HIDDEN },
            sourceLink: { host: 'github', repo: 'ocx-sh/ocx-sdk-python', root: '..' },
          })),
          // Pinned: no dump is committed, uvx fetches this griffe at build time.
          runner: { command: ['uvx', '--from', 'griffe==2.3.0', 'griffe'] },
          // One entry per group page: a per-page symbol index, inventory and llms.txt would only repeat
          // (and the symbol search inlines its index into every group's first page).
          symbolSearch: false,
          publishInventory: false,
          llmsTxt: false,
          components: { ModuleDoc: './components/ModuleDoc.astro', Signature: './components/Signature.astro', ClassDoc: './components/ClassDoc.astro' },
        }),
      ],
      sidebar: [
        { label: 'Home', link: '/' },
        {
          label: 'Start',
          items: ['guide/quickstart', 'guide/bootstrap'],
        },
        {
          label: 'Guides',
          items: [
            'guide/run-pinned-tools',
            'guide/pytest-fixture',
            'guide/cross-platform',
            'guide/async',
            'guide/from-subprocess',
            'guide/projects',
            'guide/authoring',
            'guide/troubleshooting',
            'guide/reproducible-ci',
            'guide/hermetic-ci',
            'guide/vendoring',
          ],
        },
        {
          label: 'Concepts',
          items: [
            'guide/concepts/why-a-wrapper',
            'guide/concepts/compatibility',
            'guide/concepts/concurrency',
            'guide/concepts/errors-and-security',
          ],
        },
        {
          label: 'Reference',
          items: [
            'reference/api',
            'reference/command-map',
            'reference/environment',
            'reference/compatibility-checklist',
          ],
        },
        {
          label: 'Contributing',
          items: ['contributing', 'contributing/setup', 'contributing/docs', 'contributing/releasing'],
        },
        'changelog',
      ],
    }),
  ],
});

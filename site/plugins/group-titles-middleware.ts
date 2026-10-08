import { defineRouteMiddleware } from '@astrojs/starlight/route-data';
import { GROUPS, API_BASE } from '../reference-groups.mjs';

const labels = new Map(GROUPS.map((g) => [`${API_BASE}/${g.slug}`, g.label]));

export const onRequest = defineRouteMiddleware((context) => {
  const { starlightRoute } = context.locals;
  const slug = context.url.pathname.replace(/^\/|\/$/g, '').replace(/^integrations\/python\//, '');
  const label = labels.get(slug);
  if (!label) return;
  const old = starlightRoute.entry.data.title;
  starlightRoute.entry.data.title = label;
  for (const tag of starlightRoute.head) {
    if (tag.tag === 'title') tag.content = tag.content?.replace(old, label);
    if (tag.attrs?.property === 'og:title') tag.attrs.content = label;
  }
});

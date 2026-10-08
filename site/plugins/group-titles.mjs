// Every API group page is the `ocx_sdk` module in starlight-pydocs' eyes and would be titled
// "ocx_sdk"; the route middleware retitles it with the group's label.
import { fileURLToPath } from 'node:url';

export default function groupTitles() {
  return {
    name: 'ocx-group-titles',
    hooks: {
      'config:setup'({ addRouteMiddleware }) {
        addRouteMiddleware({ entrypoint: fileURLToPath(new URL('./group-titles-middleware.ts', import.meta.url)), order: 'post' });
      },
    },
  };
}

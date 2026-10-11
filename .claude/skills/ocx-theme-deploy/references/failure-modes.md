# Deploy failure modes

You loaded this file because a deploy failed or did not run, or you need to
know what a visitor sees after a partial deploy.

| Case | Behaviour | Site afterwards | Fix |
|---|---|---|---|
| Repo owns no claim, `path` not owned, or empty `storage-key` | Exit 1 at step 1, zero requests | Unchanged | Add the claim (theme release), fix `path`, or check the environment secret is visible to the job (`environment: ocx.sh`, running on `main`) |
| `preview` with `path`, an unknown preview site, or a `sh-ocx-preview-` zone without `preview` | Exit 1 at step 1, zero requests | Unchanged | Drop `path`, use a site name from `sites.mjs`, or set `preview` |
| Link or layout check fails | Exit 1 at step 2, problems listed, zero writes | Unchanged | Run `npx ocx-site check --dist dist` locally and fix each listed link or file |
| Missing `index.html` or Pagefind entry | Exit 1 at step 2 | Unchanged | Build the right directory; keep Pagefind on for a `search: true` claim |
| Bad key (401 on the listing) | Exit 1 at step 3 | Unchanged | Ask the owner to rotate `BUNNY_STORAGE_KEY` (or `BUNNY_PREVIEW_KEY_<SLUG>` for a preview) |
| Upload fails after three tries in phase 1 | Exit 1, no phase 2, no prune | Old pages with old assets, plus some new assets: consistent | Re-run the workflow |
| Upload fails in phase 2 | Exit 1, no prune | Each page is old or new, and every page's assets exist | Re-run the workflow |
| Prune cap: more than half of at least 10 listed HTML files would go | Exit 1 after the uploads, count named, zero DELETE | New pages live, stale pages still there: consistent | Check `dist` and `path`. For a real mass removal, dispatch once with `force-prune: 'true'`, then remove it |
| Delete fails during prune | Exit 1, names the files | Consistent; stale pages linger until the next deploy | Re-run the workflow |
| Two deploys of the same repo | Serialized by the `concurrency` group | | Keep the group and `cancel-in-progress: false` |
| Daily deploy stopped: GitHub disabled the scheduled workflow after 60 days without repository activity | No scheduled run appears in the Actions list; the workflow shows as disabled | Last deploy stays live; a push or dispatch still deploys | `gh workflow enable deploy.yml`. Push and `workflow_dispatch` are the real triggers; the schedule is a convenience |

Rollback: re-run the deploy workflow on the older commit (dispatch it from
`main` after a revert, or re-run the old run). Deploys never delete assets,
so older pages stay whole.

Geo-replication across Bunny's storage regions can lag by seconds. A page
fetched in that window may miss a just-uploaded asset; it heals on its own.

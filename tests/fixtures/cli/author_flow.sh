#!/bin/sh
# Documentation, NOT an executable fixture. Exact working command sequence
# captured live during WP00 (ocx 0.5.8) against a throwaway local registry.
#
# Full loop: create -> test (local, no registry) -> push -> install from
# registry -> exec. All steps succeeded; no failures to report.
#
# Registry: `docker run -d -p 127.0.0.1:5001:5000 --name wp00-reg registry:2`
# was the port named in the task brief, but 5001 was already bound by an
# UNRELATED container from another project on this host
# (test-mirror-registry-1) — do not assume 5001 is free. This run used 5099
# instead; pick any free local port.

set -eu

docker run -d -p 127.0.0.1:5099:5000 --name wp00-reg registry:2

# Package layout: a flat bin/ directory (strip_components defaults to 0, so
# no "strip" declaration needed since we built the tree pre-flattened).
mkdir -p build/bin
cat > build/bin/hello <<'SH'
#!/bin/sh
echo "hello from wp00 spike"
SH
chmod +x build/bin/hello

# Minimal metadata.json — PATH env entry + declared binaries. No dependencies.
cat > metadata.json <<'JSON'
{
  "$schema": "https://ocx.sh/schemas/metadata/v1.json",
  "type": "bundle",
  "version": 1,
  "env": [
    {"key": "PATH", "type": "path", "required": true, "value": "${installPath}/bin", "visibility": "public"}
  ],
  "binaries": ["hello"]
}
JSON

export OCX_HOME=/tmp/wp00-home
# Plaintext local registry needs the insecure-registries opt-in.
export OCX_INSECURE_REGISTRIES=127.0.0.1:5099

# 1. Build the bundle. Emits INFO logs to stderr; --format json produces NO
#    stdout JSON for `package create` (writes files, prints nothing structured).
ocx --format json package create build \
  -i 127.0.0.1:5099/wp00/hello:1.0.0 -p linux/amd64 \
  -m metadata.json -o out/hello-1.0.0.tar.xz
# -> out/hello-1.0.0.tar.xz, out/hello-1.0.0-metadata.json (sidecar copy),
#    out/hello-1.0.0-receipt.json: {"version":1,"platform":"linux/amd64","identifier":"127.0.0.1:5099/wp00/hello:1.0.0"}

# 2. Test locally BEFORE pushing — no registry round-trip. The `-- CMD` form
#    prints the command's raw output, NOT a JSON envelope (even under
#    --format json). Use --script for the stable JSON envelope (see below).
ocx --format json package test -i wp00/hello:1.0.0-test -p linux/amd64 \
  out/hello-1.0.0.tar.xz -- hello
# -> stdout: "hello from wp00 spike" (verbatim child stdout, no wrapper)

# 2b. Scripted test — this is what produces the stable v1 JSON envelope
#     ({"status","assertion","run"}). See package_test.json fixture.
cat > smoke.star <<'STAR'
r = ocx.run("hello")
expect.ok(r)
expect.contains(r.stdout, "hello")
STAR
ocx --format json package test -i wp00/hello:1.0.0-test -p linux/amd64 \
  out/hello-1.0.0.tar.xz --script smoke.star

# 3. Push to the registry (-n = new repo, skips existing-tag lookup).
ocx --format json package push -n out/hello-1.0.0.tar.xz
# -> {"identifier","status":"pushed","manifest_digest","cascade_tags_written":[],
#     "canonical_tags_written":[...],"layers":{"mounted","uploaded","verified"}}

# 4. Install FROM the registry (fresh identifier, no local build artifacts
#    referenced — proves the push round-tripped).
ocx --format json package install 127.0.0.1:5099/wp00/hello:1.0.0

# 5. Exec the installed package.
ocx package exec 127.0.0.1:5099/wp00/hello:1.0.0 -- hello
# -> "hello from wp00 spike"

# Cleanup
docker rm -f wp00-reg
rm -rf /tmp/wp00-home /tmp/wp00-author build out metadata.json smoke.star

# Notable filesystem detail observed along the way: the registry host
# "127.0.0.1:5099" was slugified to "127.0.0.1_5099" in the installed
# package's symlink path (colon -> underscore; dots preserved) — that's the
# RELAXED slug (to_relaxed_slug), a *different* transform from the STRICT
# registry_slug (to_slug) used for OCX_AUTH_* env var names — see
# tests/fixtures/slug_fixtures.json's "note_relaxed_slug".

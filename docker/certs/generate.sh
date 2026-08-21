#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors
#
# Generate the throwaway TLS material the `authed` compose service serves.
#
# The acceptance tier needs one registry behind real TLS: registry:2 refuses
# htpasswd auth over plaintext, and ocx's `insecure_registries` opt-in covers
# transport only — it never disables certificate verification. So the authed
# service gets a self-signed CA, and the test session points `SSL_CERT_FILE`
# at that CA so ocx trusts it (ocx merges the host trust store on top of its
# bundled Mozilla roots, so one extra root is all it takes).
#
# A CA plus a leaf rather than a single self-signed certificate: rustls needs
# a trust anchor that can sign, and the leaf carries `IP:127.0.0.1` in its SAN
# because that is the name ocx connects to.
#
# Output is gitignored — this script is the checked-in source of truth. It is
# idempotent: existing material is left alone, so a re-run costs nothing.
set -eu

cd "$(dirname "$0")"

if [ -f ca.crt ] && [ -f registry.crt ] && [ -f registry.key ]; then
	exit 0
fi

openssl req -x509 -nodes -newkey rsa:2048 -sha256 -days 3650 \
	-keyout ca.key -out ca.crt \
	-subj "/CN=ocx-sdk-acc-ca" \
	-addext "basicConstraints=critical,CA:TRUE" \
	-addext "keyUsage=critical,keyCertSign,cRLSign"

openssl req -nodes -newkey rsa:2048 -sha256 \
	-keyout registry.key -out registry.csr \
	-subj "/CN=127.0.0.1"

cat >leaf.ext <<'EXT'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=IP:127.0.0.1,DNS:localhost
EXT

openssl x509 -req -in registry.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
	-sha256 -days 3650 -extfile leaf.ext -out registry.crt

rm -f registry.csr leaf.ext ca.srl
# The registry container reads the pair as a different uid than the one that
# generated it, so the key has to be world-readable. Throwaway material for a
# loopback-only listener — never a pattern for a real key.
chmod 644 ca.crt registry.crt registry.key

# `test-ca.pem`

A throwaway self-signed certificate, generated once for
`test_dist.py::ca_bundle_opener` tests. It is a **public certificate with no
private key**, is not trusted by anything, and is loaded only to prove that
`OCX_INSTALL_CA_BUNDLE` reaches `ssl.create_default_context(cafile=...)`.

Expiry is irrelevant: loading a PEM into a trust store does not validate dates.
Regenerate with:

    openssl req -x509 -newkey rsa:2048 -keyout /dev/null -out test-ca.pem \
        -days 1 -nodes -subj "/CN=ocx-sdk-test"

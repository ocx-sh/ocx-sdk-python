# startswith-sep.awk: startsWith( calls whose argument is not a string literal and names no sep before the closing paren, a path-prefix containment test with no separator.
# Usage: git ls-files -z --cached --others --exclude-standard -- 'GLOB' | xargs -0 -r awk -f startswith-sep.awk --
# Output: FILE:LINE:text per hit. Empty output is the pass. A literal quote or backtick right after the paren skips the call; so does the word sep before the first ")".
# Watched red on full.startsWith(root), green on full.startsWith(root + sep) and name.startsWith('x'); gawk 5.3.2 and busybox 1.37.0 awk (measured 2026-10-11).
{
  s = $0; hit = 0
  while (match(s, /startsWith\(/)) {
    r = substr(s, RSTART + RLENGTH)
    c = substr(r, 1, 1); k = index(r, ")"); a = k ? substr(r, 1, k - 1) : r
    if (c != "\"" && c != "'" && c != "`" && a !~ /(^|[^[:alnum:]_])sep($|[^[:alnum:]_])/) hit = 1
    s = r
  }
  if (hit) print FILENAME ":" FNR ":" $0
}

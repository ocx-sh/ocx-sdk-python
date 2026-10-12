# resolve-startswith.awk: realpath( or resolve() followed within 160 characters by startswith(, the string-prefix containment test that a sibling directory defeats.
# Usage: git ls-files -z --cached --others --exclude-standard -- 'GLOB' | xargs -0 -r awk -f resolve-startswith.awk --
# Output: FILE:LINE:text of the first line per hit. Empty output is the pass. The window runs across line breaks.
# Watched red on os.path.realpath(p).startswith(base) and on a resolve().startswith( pair, green on resolve().is_relative_to(base); gawk 5.3.2 and busybox 1.37.0 awk (measured 2026-10-11).
FNR == 1 { scan(); name = FILENAME }
{ L[++n] = $0 }
END { scan() }
function scan(  i, k, w, r, p, pat) {
  for (i = 1; i <= n; i++) for (p = 1; p <= 2; p++) {
    pat = (p == 1) ? "realpath\\(" : "resolve\\(\\)"
    if (match(L[i], pat)) {
      w = substr(L[i], RSTART + RLENGTH)
      for (k = 1; length(w) < 200 && i + k <= n; k++) w = w "\n" L[i + k]
      r = index(w, "startswith(")
      if (r && r - 1 <= 160) { print name ":" i ":" L[i]; break }
    }
  }
  split("", L); n = 0
}

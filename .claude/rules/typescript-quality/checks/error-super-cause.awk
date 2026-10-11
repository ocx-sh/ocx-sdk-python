# error-super-cause.awk: a class extending Error whose super( call within 300 characters passes no second argument, so no cause is carried.
# Usage: git ls-files -z --cached --others --exclude-standard -- 'GLOB' | xargs -0 -r awk -f error-super-cause.awk --
# Output: FILE:LINE:text of the extends line per hit. Empty output is the pass. The window runs across line breaks; a super( with one argument is the hit.
# Watched red on class E extends Error whose constructor calls super(m), green on the twin calling super(m, { cause }); gawk 5.3.2 and busybox 1.37.0 awk (measured 2026-10-11).
FNR == 1 { scan(); name = FILENAME }
{ L[++n] = $0 }
END { scan() }
function scan(  i, k, w, r, p, off) {
  for (i = 1; i <= n; i++) if (match(L[i], /extends Error/)) {
    w = substr(L[i], RSTART + RLENGTH)
    for (k = 1; length(w) < 400 && i + k <= n; k++) w = w "\n" L[i + k]
    off = 0
    while ((p = index(substr(w, off + 1), "super(")) > 0) {
      if (off + p - 1 > 300) break
      r = substr(w, off + p + 6)
      if (r ~ /^[^,)]*\)/) { print name ":" i ":" L[i]; break }
      off += p + 5
    }
  }
  split("", L); n = 0
}

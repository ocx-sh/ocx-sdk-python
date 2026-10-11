# client-timeout.awk: httpx.Client( / httpx.AsyncClient( constructions with no timeout= in the rest of the call.
# Usage: git ls-files -z --cached --others --exclude-standard -- 'GLOB' | xargs -0 -r awk -f client-timeout.awk --
# Output: FILE:LINE:text per hit. Empty output is the pass. The window is the rest of the line plus the next 3 lines.
# Watched red on a three-line httpx.Client( call with no timeout=, green on the twin carrying timeout=5.0 on its last argument line; gawk 5.3.2 and busybox 1.37.0 awk (measured 2026-10-11).
FNR == 1 { scan(); name = FILENAME }
{ L[++n] = $0 }
END { scan() }
function scan(  i, k, w) {
  for (i = 1; i <= n; i++) if (match(L[i], /httpx\.(Async)?Client\(/)) {
    w = substr(L[i], RSTART + RLENGTH)
    for (k = 1; k <= 3 && i + k <= n; k++) w = w "\n" L[i + k]
    if (w !~ /timeout=/) print name ":" i ":" L[i]
  }
  split("", L); n = 0
}

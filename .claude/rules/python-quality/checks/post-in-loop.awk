# post-in-loop.awk: a for or while statement with a .post( call on the same line or in the next 6 lines.
# Usage: git ls-files -z --cached --others --exclude-standard -- 'GLOB' | xargs -0 -r awk -f post-in-loop.awk --
# Output: FILE:LINE:text of the loop line per hit. A window, not a parse: a .post( after the loop body ended inside the window is a hit to read.
# Watched red on a for loop whose body calls client.post(, green on a loop that appends and posts eight lines later; gawk 5.3.2 and busybox 1.37.0 awk (measured 2026-10-11).
FNR == 1 { scan(); name = FILENAME }
{ L[++n] = $0 }
END { scan() }
function scan(  i, k, w) {
  for (i = 1; i <= n; i++) if (L[i] ~ /^[[:space:]]*(for|while) /) {
    w = L[i]
    for (k = 1; k <= 6 && i + k <= n; k++) w = w "\n" L[i + k]
    if (w ~ /\.post\(/) print name ":" i ":" L[i]
  }
  split("", L); n = 0
}

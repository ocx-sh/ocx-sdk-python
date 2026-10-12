# mkdir-after-exists.awk: an exists() test with a mkdir( call within the next 4 lines, a check-then-create race.
# Usage: git ls-files -z --cached --others --exclude-standard -- 'GLOB' | xargs -0 -r awk -f mkdir-after-exists.awk --
# Output: FILE:LINE:text of the exists() line per hit. Empty output is the pass.
# Watched red on an exists() test followed by mkdir(), green on mkdir(exist_ok=True); gawk 5.3.2 and busybox 1.37.0 awk (measured 2026-10-11).
FNR == 1 { scan(); name = FILENAME }
{ L[++n] = $0 }
END { scan() }
function scan(  i, k) {
  for (i = 1; i <= n; i++) if (L[i] ~ /exists\(\)/)
    for (k = 1; k <= 4 && i + k <= n; k++) if (L[i + k] ~ /mkdir\(/) { print name ":" i ":" L[i]; break }
  split("", L); n = 0
}

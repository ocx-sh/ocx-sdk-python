# eq-return-false.awk: a def __eq__ whose body returns False within the next 4 lines, where NotImplemented belongs.
# Usage: git ls-files -z --cached --others --exclude-standard -- 'GLOB' | xargs -0 -r awk -f eq-return-false.awk --
# Output: FILE:LINE:text of the def line per hit. Empty output is the pass.
# Watched red on a def __eq__ that returns False three lines down, green on the twin returning NotImplemented; gawk 5.3.2 and busybox 1.37.0 awk (measured 2026-10-11).
FNR == 1 { scan(); name = FILENAME }
{ L[++n] = $0 }
END { scan() }
function scan(  i, k) {
  for (i = 1; i <= n; i++) if (L[i] ~ /def __eq__/)
    for (k = 1; k <= 4 && i + k <= n; k++) if (L[i + k] ~ /return False/) { print name ":" i ":" L[i]; break }
  split("", L); n = 0
}

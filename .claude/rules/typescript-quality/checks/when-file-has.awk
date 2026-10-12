# when-file-has.awk: print every line matching PAT, but only in files that match GATE somewhere, a two-stage list-then-search in one pass.
# Usage: GIT-FED-LIST | GATE='ERE' PAT='ERE' xargs -0 -r awk -f when-file-has.awk --
# Output: FILE:LINE:text per hit. Empty output is the pass. Both patterns are EREs read from the environment so no escape is eaten.
# Watched red on a file importing node:child_process and calling execSync(, green on a file without the import that calls /a/.exec(s); gawk 5.3.2 and busybox 1.37.0 awk (measured 2026-10-11).
BEGIN { gate = ENVIRON["GATE"]; pat = ENVIRON["PAT"] }
FNR == 1 { flush(); name = FILENAME }
{ L[FNR] = $0; n = FNR; if ($0 ~ gate) g = 1; if ($0 ~ pat) H[FNR] = 1 }
END { flush() }
function flush(  i) {
  if (g) for (i = 1; i <= n; i++) if (H[i]) print name ":" i ":" L[i]
  split("", L); split("", H); n = 0; g = 0
}

# context.awk: print every line matching PAT with BEFORE lines before and AFTER lines after it (grep context options are outside the portable set).
# Usage: PAT='ERE' AFTER=8 BEFORE=0 [ICASE=1] awk -f context.awk -- FILE...    (or: GIT-FED-LIST | PAT=... xargs -0 -r awk -f context.awk --)
# Output: FILE:LINE:text for a match, FILE-LINE-text for context, and a lone -- between groups that do not touch.
# PAT is an ERE (the awk and grep -E dialects agree). The environment carries it so no escape is eaten. ICASE=1 lowercases the pattern and the line.
# Watched red on a match flanked by before and after lines (printed the match and its AFTER lines), green on a twin without the pattern; gawk 5.3.2 and busybox 1.37.0 awk (measured 2026-10-11).
BEGIN { pat = ENVIRON["PAT"]; a = ENVIRON["AFTER"] + 0; b = ENVIRON["BEFORE"] + 0; ic = (ENVIRON["ICASE"] != ""); if (ic) pat = tolower(pat) }
FNR == 1 { flush(); name = FILENAME }
{ L[FNR] = $0; n = FNR; if ((ic ? tolower($0) : $0) ~ pat) H[FNR] = 1 }
END { flush() }
function flush(  i, lo, hi, last, k) {
  last = 0
  for (i = 1; i <= n; i++) if (H[i]) {
    lo = i - b; if (lo < 1) lo = 1
    hi = i + a; if (hi > n) hi = n
    if (last && lo > last + 1) print "--"
    for (k = (lo > last ? lo : last + 1); k <= hi; k++) print name (H[k] ? ":" : "-") k (H[k] ? ":" : "-") L[k]
    if (hi > last) last = hi
  }
  split("", L); split("", H); n = 0
}

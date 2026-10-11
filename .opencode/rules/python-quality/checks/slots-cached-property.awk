# slots-cached-property.awk: files holding a @dataclass(... slots=True) followed within 600 characters by @cached_property, a pair that raises at class creation.
# Usage: git ls-files -z --cached --others --exclude-standard -- 'GLOB' | xargs -0 -r awk -f slots-cached-property.awk --
# Output: one FILE per line. Empty output is the pass. The window is the 600 characters after slots=True.
# Watched red on a @dataclass(slots=True) class carrying a @cached_property, green on the same class without it; gawk 5.3.2 and busybox 1.37.0 awk (measured 2026-10-11).
FNR == 1 { scan(); name = FILENAME }
{ L[++n] = $0 }
END { scan() }
function scan(  i, k, w, r) {
  for (i = 1; i <= n; i++) if (match(L[i], /@(dataclasses\.)?dataclass\(/)) {
    w = substr(L[i], RSTART)
    for (k = 1; length(w) < 900 && i + k <= n; k++) w = w "\n" L[i + k]
    if (match(w, /^[^)]*slots=True/)) {
      r = substr(w, RLENGTH + 1, 600)
      if (index(r, "@cached_property")) { print name; split("", L); n = 0; return }
    }
  }
  split("", L); n = 0
}

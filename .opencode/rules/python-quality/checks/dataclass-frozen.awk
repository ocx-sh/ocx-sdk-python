# dataclass-frozen.awk: dataclass decorators that are not frozen: a bare @dataclass, or @dataclass(...) with no frozen=True before the first ")" on the line.
# Usage: git ls-files -z --cached --others --exclude-standard -- 'GLOB' | xargs -0 -r awk -f dataclass-frozen.awk --
# Output: FILE:LINE:text per hit. Empty output is the pass. A multi-line decorator whose frozen=True sits on a later line is reported (read it).
# Watched red on a bare @dataclass and on @dataclass(slots=True), green on @dataclass(frozen=True) and @dataclasses.dataclass(frozen=True, slots=True); gawk 5.3.2 and busybox 1.37.0 awk (measured 2026-10-11).
/@(dataclasses\.)?dataclass(\(\))?[[:space:]]*$/ { print FILENAME ":" FNR ":" $0; next }
match($0, /@(dataclasses\.)?dataclass\(/) {
  r = substr($0, RSTART + RLENGTH); k = index(r, ")"); if (k) r = substr(r, 1, k - 1)
  if (r !~ /frozen=True/) print FILENAME ":" FNR ":" $0
}

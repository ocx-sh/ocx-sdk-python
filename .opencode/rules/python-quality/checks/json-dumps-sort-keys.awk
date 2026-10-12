# json-dumps-sort-keys.awk: json.dump( / json.dumps( calls with no sort_keys on the rest of the line.
# Usage: git ls-files -z --cached --others --exclude-standard -- 'GLOB' | xargs -0 -r awk -f json-dumps-sort-keys.awk --
# Output: FILE:LINE:text per hit. Empty output is the pass. A call whose sort_keys sits on a later line is reported (read it).
# Watched red on json.dumps(obj), green on json.dumps(obj, sort_keys=True); gawk 5.3.2 and busybox 1.37.0 awk (measured 2026-10-11).
match($0, /json\.dumps?\(/) { if (substr($0, RSTART + RLENGTH) !~ /sort_keys/) print FILENAME ":" FNR ":" $0 }

# urlopen-timeout.awk: urlopen( calls with no timeout= on the rest of the line.
# Usage: git ls-files -z --cached --others --exclude-standard -- 'GLOB' | xargs -0 -r awk -f urlopen-timeout.awk --
# Output: FILE:LINE:text per hit. Empty output is the pass. A call whose timeout= sits on a later line is reported (read it).
# Watched red on urlopen(u), green on urlopen(u, timeout=10); gawk 5.3.2 and busybox 1.37.0 awk (measured 2026-10-11).
match($0, /urlopen\(/) { if (substr($0, RSTART + RLENGTH) !~ /timeout=/) print FILENAME ":" FNR ":" $0 }

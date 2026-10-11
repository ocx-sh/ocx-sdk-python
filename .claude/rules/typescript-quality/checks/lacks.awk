# lacks.awk: list each file that matches HAS and never matches NEED, a two-stage list-then-invert done in one awk pass so no second xargs reads newline-separated names.
# Usage: GIT-FED-LIST | HAS='ERE' NEED='ERE' xargs -0 -r awk -f lacks.awk --
# Output: one file name per line. Empty output means every file that matches HAS also matches NEED. The pass is read from the output alone.
# Watched red on a file with JSON.parse( and no Map, Object.create(null) or __proto__, green on the twin holding new Map(); gawk 5.3.2 and busybox 1.37.0 awk (measured 2026-10-11).
BEGIN { has = ENVIRON["HAS"]; need = ENVIRON["NEED"] }
FNR == 1 { flush(); name = FILENAME; h = 0; nd = 0 }
$0 ~ has { h = 1 }
$0 ~ need { nd = 1 }
END { flush() }
function flush() { if (name != "" && h && !nd) print name; name = "" }

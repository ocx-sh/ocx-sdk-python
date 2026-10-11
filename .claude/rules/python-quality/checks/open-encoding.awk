# open-encoding.awk: text-mode open( / .read_text( / .write_text( with no encoding= on the rest of the line.
# Usage: git ls-files -z --cached --others --exclude-standard -- 'GLOB' | xargs -0 -r awk -f open-encoding.awk --
# Output: FILE:LINE:text per hit. Empty output is the pass. open( skips os.open( and a binary mode literal ("rb", "wb", "ab", "xb") on the line.
# Watched red on open(p), p.read_text() and p.write_text(t), green on open(p, encoding=...), open(p, "rb"), os.open(p, 0) and read_text(encoding=...); gawk 5.3.2 and busybox 1.37.0 awk (measured 2026-10-11).
function bad(s, pre,   p, c, r) {
  p = index(s, pre); if (!p) return 0
  r = substr(s, p + length(pre))
  return r !~ /encoding=/
}
{
  hit = 0
  s = $0
  while (match(s, /open\(/)) {
    pre = substr(s, 1, RSTART - 1); r = substr(s, RSTART + RLENGTH)
    if ((pre == "" || pre ~ /[^[:alnum:]_]$/) && pre !~ /os\.$/ && r !~ /encoding=/ && r !~ /"[rwax]b/) hit = 1
    s = r
  }
  if (bad($0, ".read_text(") || bad($0, ".write_text(")) hit = 1
  if (hit) print FILENAME ":" FNR ":" $0
}

# hex_in_comment.awk: ROU hash scan. Prints FILE:LINE:TEXT for each line where a
# comment marker (// or #) is followed by a commit-hash-looking token: 7 to 40
# lowercase hex digits holding at least one digit and at least one letter a-f,
# standing alone between non-word characters. No lookahead, so POSIX awk runs it
# (gawk and busybox awk).
{
  if (!match($0, /\/\/|#/)) next
  rest = substr($0, RSTART + RLENGTH)
  while (match(rest, /[0-9a-f]+/)) {
    tok = substr(rest, RSTART, RLENGTH)
    before = (RSTART == 1) ? "" : substr(rest, RSTART - 1, 1)
    after = substr(rest, RSTART + RLENGTH, 1)
    if (before !~ /[[:alnum:]_]/ && after !~ /[[:alnum:]_]/ && length(tok) >= 7 && length(tok) <= 40 && tok ~ /[0-9]/ && tok ~ /[a-f]/) {
      print FILENAME ":" FNR ":" $0
      next
    }
    rest = substr(rest, RSTART + RLENGTH)
  }
}

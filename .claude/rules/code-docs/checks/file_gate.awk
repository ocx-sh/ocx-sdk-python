# file_gate.awk: a file-level grep gate that needs no -Z and no GNU-only flag.
# Reads the files named on the command line (git-fed through xargs -0).
# Patterns arrive as POSIX ERE in the environment, so no shell escaping applies:
#   HAS    required  a file must contain a line matching this
#   ALSO   optional  ... and a line matching this
#   LACKS  optional  ... and no line matching this
#   FOLD   optional  any non-empty value makes HAS case-insensitive (write HAS in plain words)
# Prints the name of each file that passes. POSIX awk; gawk and busybox awk run it.
BEGIN {
  H = ENVIRON["HAS"]; A = ENVIRON["ALSO"]; L = ENVIRON["LACKS"]; F = ENVIRON["FOLD"]
  if (H == "") { print "file_gate.awk: HAS is required" > "/dev/stderr"; bad = 1; exit 2 }
  if (F != "") H = tolower(H)
}
FNR == 1 { flush(); file = FILENAME; h = a = l = 0 }
{
  if ((F != "" ? tolower($0) : $0) ~ H) h = 1
  if (A != "" && $0 ~ A) a = 1
  if (L != "" && $0 ~ L) l = 1
}
END { if (!bad) flush() }
function flush() {
  if (file == "") return
  if (h && (A == "" || a) && !l) print file
}

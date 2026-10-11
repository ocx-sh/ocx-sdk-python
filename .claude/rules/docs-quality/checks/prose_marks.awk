# prose_marks.awk: DOC-PLAIN-01 mark scan over stripped prose on stdin.
# Prints N:LINE for each line holding an en dash, em dash, semicolon or curly quote.
# Run it as LC_ALL=C awk -f prose_marks.awk, so each UTF-8 byte is one character
# (en dash E2 80 93, em dash E2 80 94, curly quotes E2 80 98, 99, 9C, 9D).
# POSIX awk; gawk and busybox awk run it.
BEGIN { re = sprintf("%c%c[%c%c%c%c%c%c]|;", 226, 128, 147, 148, 152, 153, 156, 157) }
$0 ~ re { print NR ":" $0 }

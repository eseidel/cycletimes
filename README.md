cycletimes.py is a tool for trying to understand
chrome productivity. Uses commit and review information
to produce statistics on how fast developers are
able to upload, review, test, commit, release changes.

See cycletimes.py help for help.

# Precache all the review/commit information into
# /chromepath/cycletimes_cache
cycletimes.py /chromepath update

# Reads cached files from /chromepath/cycletimes_cache
# and dumps per-repository stats to stdout.
cycletimes.py /chromepath stats

# Reads cached files from /chromepath/cycletimes_cache
# and dumps js to stdout for use with graph.html
cycletimes.py /chromepath graph

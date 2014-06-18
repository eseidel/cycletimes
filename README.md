cycletimes.py is a tool for trying to understand
chrome productivity. Uses commit and review information
to produce statistics on how long it takes changes between
upload, review, test, commit, and release.

See cycletimes.py help for help.

# Precache all the review/commit information into
# /chromepath/cycletimes_cache
cycletimes.py /chrome/path update
# Note 'update' can take a very long time.
# Use -v to see verbose output, including when
# requests have to hit the network.

# Reads cached files from /chromepath/cycletimes_cache
# and dumps per-repository stats to stdout.
cycletimes.py /chrome/path stats

# Reads cached files from /chromepath/cycletimes_cache
# and dumps js to stdout for use with graph.html
cycletimes.py /chrome/path graph


TODO:
- Verify that timezones are correct for all timestamps!
(Timezones can mean hours, which is a lot of time!)
- Blink Rolls - list DEPS, grab webkit_revs and commit_date.
- Could build a list of date -> rev and filter out any rollouts?
- Understand Reverts
- Could show what % of time was due to tree-closures by
overlapping lgtm -> commit time with tree closure data?
- Could show what % of tiem was due to CQ by overlapping
CQ data with lgtm -> commit time.
- Want to show what % of a typical patch time is spent waiting for X.

Should be possile to do skia by grabbing the DEPS from the branch
and using that to get Skia revisions.

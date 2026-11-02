# Vendored consumer (test fixture only)

`hermesfeed_pr40.py` is an unmodified copy of `bridge/hermesfeed.py` from
omoresearch/omo-space PR #40 (branch `feat/hermes-feed-v2`, commit
7effa94d3337f9920dc44539320476d10bbe252c), MIT, © Omo Research. It is the Omo bridge's reader
for this plugin's feed. `tests/test_contract_pr40.py` writes a feed with this plugin and reads it
back with that reader, so a drift between the two fails the suite. It is never imported by the
plugin itself. Refresh it when the bridge's reader changes.

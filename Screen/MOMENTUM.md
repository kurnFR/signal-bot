## Telegram watches and confirmations

A volume watch uses the existing screener thresholds. Its ranking score prioritizes unusual activity. The message shows the signal candle's high, low, open timestamp, order flow and higher-timeframe context.

A delivered directional 1m watch can produce one linked confirmation within the following five one-minute candles. The first close outside the range must occur after watch delivery and in the watch direction. An opposite close invalidates the watch. A wick or a close exactly on the boundary leaves it open. Missing candles prevent confirmation across a gap. When twenty complete preceding candles exist, the follow-up reports distance beyond the level divided by their mean high–low range. This is an observation of price action.

The screener polls Binance spot closed candles for eligible watches. The existing delivery gate, score threshold and rolling hourly caps apply to both stages. Only the originating watch is exempt from the symbol cooldown for its follow-up. Confirmations expire at the next one-minute close. In the database, a confirmation row links to its watch via parent_watch_id. Its screening metrics describe the watch. The breakout close, level, normalized distance and close time are in watch_context. The watch dashboard counts originating watches.

## CMS deployment and replay

Apply migration 031_screener_momentum with the existing migration environment and restart the screener. The web process also checks this schema on startup. Watch for a natural VOLUME WATCH, then inspect its child row and delivered_at if a fresh directional break occurs. A parent can legitimately have no follow-up.

Run from the repository with the CMS database environment:

    python -m Screen.replay_momentum --days 14 --limit 1000

Use --legacy-1m only after verifying the interval on older alerts. Legacy rows require a Binance historical candle for their range. The report uses the most recent eligible database rows up to the requested limit. It splits the requested time interval chronologically at 70%, excludes earlier observations whose measurement crosses the split, and reports sample counts. Entries use the first minute opening strictly after the event. Delivered stages use Telegram send acknowledgment recorded by the server. Simulated break times use candle close and omit delivery limits and latency. Returns and favorable/adverse excursions are gross spot price changes signed in the watch direction, at 1, 5 and 15 minutes. Nonpositive return is the reported false-alert proxy. Missing future candles remove only the affected horizons. Compare sample sizes and movement before entry across stages before adjusting thresholds.

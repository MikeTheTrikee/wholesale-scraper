#!/bin/bash
# Daily cron script — runs scraper for yesterday to today
# Set this as a cron job on Railway: 0 6 * * *  (6am daily)

TODAY=$(date +%Y-%m-%d)
YESTERDAY=$(date -d "yesterday" +%Y-%m-%d)

echo "Running daily probate scrape: $YESTERDAY to $TODAY"
python scraper.py --start $YESTERDAY --end $TODAY

-- Every map name in the data, with how many players and runs. Run it on the live database:
--   npx wrangler d1 execute classicexp --remote --json --file tools/map_report.sql > tools/maps-live.json
-- then:  node tools/make_cleanup.mjs tools/maps-live.json > tools/cleanup-maps.sql
SELECT map, COUNT(DISTINCT install) AS players, COUNT(*) AS runs FROM runs GROUP BY map ORDER BY map;

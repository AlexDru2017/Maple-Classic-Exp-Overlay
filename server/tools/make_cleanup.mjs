// Writes the SQL that puts the maps already in the database right, with the same rules the site
// now applies to every new run (src/mapnames.js): OCR slips fixed ("ist" -> "1st"), the game's
// spelling used, and runs on towns taken out (nobody farms in a town).
//
//   node tools/make_cleanup.mjs tools/maps-live.json > tools/cleanup-maps.sql
//
// It only prints SQL; nothing is changed until the SQL is run, which is up to you:
//   npx wrangler d1 execute classicexp --remote --file tools/cleanup-maps.sql --yes
//   npx wrangler d1 execute classicexp --remote --file schema.sql --yes     (rebuilds the summary tables)
import fs from "node:fs";
import { canonicalMap } from "../src/mapnames.js";

const raw = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const rows = (Array.isArray(raw) ? raw[0] : raw).results;
const q = (s) => `'${String(s).replace(/'/g, "''")}'`;
const out = ["-- Made by tools/make_cleanup.mjs. Review it, then run it."];
const review = [];
let renames = 0, towns = 0;
for (const { map, players, runs } of rows) {
  const c = canonicalMap(map);
  if (c.town) {
    out.push(`-- town: ${map} (${players} players, ${runs} runs)`, `DELETE FROM runs WHERE map = ${q(map)};`);
    towns++;
  } else if (c.name !== map) {
    out.push(`-- ${map}  ->  ${c.name}  (${players} players, ${runs} runs)`);
    // (runs of one player can't collide: a run is unique by player and start time)
    out.push(`UPDATE OR IGNORE runs SET map = ${q(c.name)} WHERE map = ${q(map)};`, `DELETE FROM runs WHERE map = ${q(map)};`);
    renames++;
  } else if (!c.listed) {
    review.push(`--   ${map} (${players} players, ${runs} runs)`);
  }
}
out.push("DELETE FROM answers;", "",
  `-- ${renames} names put right, ${towns} town names removed.`,
  "-- Not on the game's list (event maps, new maps, or misreads): left as they are. Look them over:", ...review);
console.log(out.join("\n"));

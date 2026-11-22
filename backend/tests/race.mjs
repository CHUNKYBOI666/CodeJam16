import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { promisify } from "node:util";

const sql = promisify(execFile);
const database = process.env.CODEJAM_TEST_DB_URL ??
  "postgresql://postgres:postgres@127.0.0.1:54322/postgres";

if (!["127.0.0.1", "localhost"].includes(new URL(database).hostname)) {
  throw new Error("The race harness requires a local test database.");
}

async function query(query) {
  const { stdout } = await sql("psql", ["-XAtq", "--set=ON_ERROR_STOP=1", "--dbname", database, "-c", query]);
  return stdout.trim();
}

const fixture = JSON.parse(await readFile(new URL("../../fixtures/person-down.json", import.meta.url)));
const location = { map_version: "kitchen-v2", map_x: 0.24, map_y: 0.62 };
const people = [randomUUID(), randomUUID()];
const runId = `race-${randomUUID()}`;
try {
  await query(`
    insert into auth.users(id) values ('${people[0]}'), ('${people[1]}');
    insert into public.people(id,name) values ('${people[0]}','Ada'), ('${people[1]}','Jordan');
  `);
  for (let index = 0; index < 50; index += 1) {
    const event = JSON.stringify({ ...fixture, ...location, episode_id: `${runId}-${index}` }).replaceAll("'", "''");
    const id = await query(`set role service_role; select (public.create_check('${event}')).id;`);
    const contestants = people.map((person) => query(`
      set role authenticated;
      set request.jwt.claim.sub = '${person}';
      select (public.accept_check('${id}')).owner_name;
    `));
    const results = await Promise.allSettled(contestants);
    const winners = results.filter(({ status }) => status === "fulfilled");
    const losers = results.filter(({ status }) => status === "rejected");
    assert.equal(winners.length, 1);
    assert.match(losers[0].reason.stderr, new RegExp(winners[0].value + " accepted this first[.]"));
    assert.equal(await query(`
      select count(*) from public.check_events
      where check_id = '${id}' and event = 'accepted';
    `), "1");
    const snapshots = await Promise.all(people.map(async (person) => JSON.parse(await query(`
      set role authenticated;
      set request.jwt.claim.sub = '${person}';
      select jsonb_build_object('owner_name', item->>'owner_name', 'map_version', item->'map_version',
          'map_x', item->'map_x', 'map_y', item->'map_y')
      from jsonb_array_elements(public.room_snapshot()->'checks') item where item->>'id' = '${id}';
    `))));
    for (const snapshot of snapshots) assert.deepEqual(snapshot, { owner_name: winners[0].value, ...location });
  }
  console.log("50/50 mapped races had one winner, one claim history entry and the original pin in both snapshots.");
} finally {
  await query(`
    delete from public.check_events where check_id in (select id from public.checks where episode_id like '${runId}-%');
    delete from public.checks where episode_id like '${runId}-%';
    delete from public.people where id in ('${people[0]}', '${people[1]}');
    delete from auth.users where id in ('${people[0]}', '${people[1]}');
  `);
}

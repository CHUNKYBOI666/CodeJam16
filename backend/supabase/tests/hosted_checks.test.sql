begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select no_plan();

select has_table('public', 'checks', 'checks table exists');
select col_is_unique('public', 'checks', 'episode_id', 'episode IDs are unique');
select ok(exists (select from pg_publication_tables where pubname = 'supabase_realtime' and tablename = 'checks'), 'checks use Realtime');
select ok(exists (
    select from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'checks'
      and attnames @> array['map_x', 'map_y', 'map_version']::name[]
), 'checks publish map fields through Realtime');

insert into app_private.app_settings(key, value) values ('room_code', 'test-room')
on conflict (key) do update set value = excluded.value;
insert into auth.users(id) values
    ('00000000-0000-0000-0000-000000000001'),
    ('00000000-0000-0000-0000-000000000002');
set local role anon;
select throws_ok('select * from public.checks', '42501', null, 'unauthenticated reads are denied');
reset role;

set local role authenticated;
set local request.jwt.claim.sub = '00000000-0000-0000-0000-000000000001';
select is((select count(*)::integer from checks), 0, 'unjoined reads return no checks');
select throws_ok($$select public.join_room('Jordan', 'wrong')$$, 'P0001', 'Wrong room code', 'wrong room denied');
select throws_ok($$select public.join_room('Jordan', null)$$, 'P0001', 'Wrong room code', 'missing room denied');
select throws_ok($$select public.join_room('  ', 'test-room')$$, 'P0001', 'Type your name to join', 'blank name denied');
select throws_ok('select public.get_invite_code()', '42501', null, 'unjoined invite denied');
select throws_ok($$select public.accept_check(gen_random_uuid())$$, '42501', null, 'unjoined action denied');
select throws_ok($$select public.create_check('{}')$$, '42501', null, 'browser cannot ingest');
select throws_ok($$select public.report_feed('live', 'Live', 15, 'live')$$, '42501', null, 'browser cannot report feed');
select throws_ok('select * from app_private.app_settings', '42501', null, 'room settings are private');
select is((public.join_room(' Jordan ', 'test-room')).name, 'Jordan', 'names are trimmed');
select is(length((public.join_room(repeat('ñ', 41), 'test-room')).name), 40, 'names are capped');
select is((public.join_room('Jordan', 'test-room')).name, 'Jordan', 'rejoin retains identity');
select is(public.get_invite_code(), 'test-room', 'member can share the current invite');
select throws_ok('delete from public.checks', '42501', null, 'browser cannot delete');
select throws_ok('update public.checks set status = null', '42501', null, 'browser cannot update');
select throws_ok('insert into public.checks default values', '42501', null, 'browser cannot insert');
reset role;

set local role service_role;
select lives_ok($$
    select public.create_check('{"episode_id":"test-episode","signal":"possible_person_down","camera_id":"demo-camera","zone_id":"unknown","detected_at":"2026-01-01T00:00:00Z","source":"replay"}')
$$, 'service ingests event');
select lives_ok($$select public.report_feed('replay', 'Rehearsal clip', 12, 'replay')$$, 'service reports feed');
reset role;
select is((select place from checks where episode_id = 'test-episode'), 'Near the stage', 'use the default place');
select is((select map_version from checks where episode_id = 'test-episode'), null, 'legacy event has no map');

set local role service_role;
select lives_ok($$
    select public.create_check('{"episode_id":"kitchen-pin","signal":"possible_person_down","camera_id":"kitchen","detected_at":"2026-01-01T00:00:00Z","source":"live","map_version":"kitchen-v1","map_x":0.2,"map_y":0.8}')
$$, 'service ingests a pin');
select lives_ok($$
    select public.create_check('{"episode_id":"kitchen-pin","signal":"possible_person_down","camera_id":"kitchen","detected_at":"2026-01-01T00:00:00Z","source":"live","map_version":"kitchen-v1","map_x":0.3,"map_y":0.1}')
$$, 'service retries a mapped episode');
select throws_ok($$
    select public.create_check('{"episode_id":"partial-pin","signal":"possible_person_down","camera_id":"kitchen","detected_at":"2026-01-01T00:00:00Z","source":"live","map_version":"kitchen-v1","map_x":0.2}')
$$, '23514', null, 'incomplete pin rejected');
select throws_ok($$
    select public.create_check('{"episode_id":"outside-pin","signal":"possible_person_down","camera_id":"kitchen","detected_at":"2026-01-01T00:00:00Z","source":"live","map_version":"kitchen-v1","map_x":0.2,"map_y":1.1}')
$$, '23514', null, 'out-of-range pin rejected');
reset role;

set local role authenticated;
select is((public.accept_check((select id from checks where episode_id = 'kitchen-pin'))).map_y,
    0.8::numeric, 'claim retains the original pin after a retry');
select is(
    (select item->>'map_version' from jsonb_array_elements(public.room_snapshot()->'checks') as item
     where item->>'episode_id' = 'kitchen-pin'),
    'kitchen-v1', 'room snapshot includes the map version'
);
reset role;

set local role service_role;
select lives_ok($$
    select public.create_check('{"episode_id":"test-episode","signal":"possible_person_down","camera_id":"demo-camera","zone_id":"unknown","detected_at":"2026-01-01T00:00:00Z","source":"replay"}')
$$, 'retry with same episode');
reset role;
select is((select count(*)::integer from checks where episode_id = 'test-episode'), 1, 'one incident per episode');
select is((select count(*)::integer from check_events where event = 'created' and check_id = (select id from checks where episode_id = 'test-episode')), 1, 'creation history written once');

set local role authenticated;
select is((select count(*)::integer from checks where episode_id = 'test-episode'), 1, 'joined members can read checks');
select is((public.accept_check((select id from checks where episode_id = 'test-episode'))).status, 'accepted', 'claim advances status');
select is((public.accept_check((select id from checks where episode_id = 'test-episode'))).owner_name, 'Jordan', 'owner repeat is idempotent');
select throws_ok($$select public.resolve_check((select id from checks where episode_id = 'test-episode'), 'False alarm')$$, 'P0001', 'Only the assigned responder can resolve an arrived check', 'must arrive before resolving');

set local request.jwt.claim.sub = '00000000-0000-0000-0000-000000000002';
select is((select count(*)::integer from checks where episode_id = 'test-episode'), 0, 'unjoined user cannot see an existing check');
select lives_ok($$select public.join_room('Alex', 'test-room')$$, 'another user can join');
select throws_ok($$select public.accept_check((select id from checks where episode_id = 'test-episode'))$$, 'P0001', 'Jordan accepted this first.', 'loser sees winner');
select throws_ok($$select public.arrive_check((select id from checks where episode_id = 'test-episode'))$$, 'P0001', 'Only the assigned responder can mark arrival', 'non-owner cannot arrive');
select throws_ok($$select public.resolve_check((select id from checks where episode_id = 'test-episode'), 'False alarm')$$, 'P0001', 'Only the assigned responder can resolve an arrived check', 'non-owner cannot resolve');

set local request.jwt.claim.sub = '00000000-0000-0000-0000-000000000001';
select lives_ok($$select public.join_room('Jo', 'test-room')$$, 'owner can rename');
select is((select owner_name from checks where episode_id = 'test-episode'), 'Jo', 'open owner label updates');
select is((public.arrive_check((select id from checks where episode_id = 'test-episode'))).status, 'arrived', 'owner can arrive');
select throws_ok($$select public.resolve_check((select id from checks where episode_id = 'test-episode'), null)$$, 'P0001', 'Choose a resolution outcome', 'unknown outcome denied');
select is((public.resolve_check((select id from checks where episode_id = 'test-episode'), 'False alarm')).outcome, 'False alarm', 'owner can resolve');
select lives_ok($$select public.join_room('J', 'test-room')$$, 'owner can rename after resolution');
select is((select owner_name from checks where episode_id = 'test-episode'), 'Jo', 'closed owner label retained');
select is((select actor_name from check_events where event = 'resolved' and check_id = (select id from checks where episode_id = 'test-episode')), 'Jo', 'resolution history retains actor');
select is((select count(*)::integer from check_events where event = 'accepted' and check_id = (select id from checks where episode_id = 'test-episode')), 1, 'claim history written once');
reset role;


create function pg_temp.fail_claim_history() returns trigger language plpgsql as $fn$
begin
    if new.event = 'accepted' then raise exception 'simulated history failure'; end if;
    return new;
end;
$fn$;
create trigger fail_claim_history before insert on public.check_events
for each row execute function pg_temp.fail_claim_history();
do $setup$ begin perform public.create_check('{"episode_id":"rollback","signal":"possible_person_down","camera_id":"demo-camera","zone_id":"demo-area","detected_at":"2026-01-01T00:00:00Z","source":"replay"}'); end $setup$;
set local role authenticated;
select throws_ok($$select public.accept_check((select id from checks where episode_id = 'rollback'))$$, 'P0001', 'simulated history failure', 'claim fails if history fails');
select is((select status from checks where episode_id = 'rollback'), 'needs_responder', 'failed history rolls back claim');
reset role;

select * from finish();
rollback;

-- Loaded by local reset. Do not include this development code in a hosted seed.
insert into app_private.app_settings(key, value) values ('room_code', 'codejam-local')
on conflict (key) do nothing;

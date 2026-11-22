alter table public.checks
    add column map_x numeric,
    add column map_y numeric,
    add column map_version text,
    add constraint checks_map_location_valid check (
        num_nonnulls(map_x, map_y, map_version) = 0
        or (
            num_nonnulls(map_x, map_y, map_version) = 3
            and map_x between 0 and 1
            and map_y between 0 and 1
            and char_length(btrim(map_version)) between 1 and 80
        )
    );

insert into app_private.app_settings(key, value) values ('zone:kitchen', 'Kitchen')
on conflict (key) do nothing;

create or replace function public.create_check(p_event jsonb)
returns public.checks language plpgsql set search_path = '' as $$
declare
    saved public.checks;
    place_label text;
begin
    select value into place_label from app_private.app_settings
    where key in ('default_place', 'zone:' || (p_event->>'zone_id'))
    order by key = 'default_place' limit 1;
    insert into public.checks(
        episode_id, signal, camera_id, place, source, detected_at, map_x, map_y, map_version
    )
    values (
        p_event->>'episode_id', p_event->>'signal', p_event->>'camera_id', place_label,
        p_event->>'source', (p_event->>'detected_at')::timestamptz,
        (p_event->>'map_x')::numeric, (p_event->>'map_y')::numeric, p_event->>'map_version'
    )
    on conflict (episode_id) do nothing returning * into saved;
    if found then
        insert into public.check_events(check_id, event) values (saved.id, 'created');
    else
        select * into strict saved from public.checks where episode_id = p_event->>'episode_id';
    end if;
    return saved;
end;
$$;

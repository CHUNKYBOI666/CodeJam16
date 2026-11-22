create schema app_private;
revoke all on schema app_private from public, anon, authenticated, service_role;
grant usage on schema app_private to authenticated;

create table public.people (
    id uuid primary key references auth.users(id),
    name text not null check (char_length(name) between 1 and 40),
    joined_at timestamptz not null default now()
);

create table public.checks (
    id uuid primary key default gen_random_uuid(),
    episode_id text not null unique check (char_length(episode_id) between 1 and 200),
    signal text not null check (signal = 'possible_person_down'),
    camera_id text not null check (char_length(camera_id) between 1 and 200),
    place text not null,
    source text not null check (source in ('live', 'replay', 'mock')),
    detected_at timestamptz not null,
    created_at timestamptz not null default clock_timestamp(),
    status text not null default 'needs_responder' check (status in ('needs_responder', 'accepted', 'arrived', 'resolved')),
    owner_id uuid references public.people(id),
    owner_name text,
    outcome text check (outcome in ('Confirmed person down', 'False alarm', 'Couldn''t verify')),
    resolved_at timestamptz,
    check ((status <> 'needs_responder') = (owner_id is not null) and (owner_id is null) = (owner_name is null)),
    check ((status = 'resolved') = (resolved_at is not null) and (resolved_at is null) = (outcome is null))
);

create table public.check_events (
    id bigint generated always as identity primary key,
    check_id uuid not null references public.checks(id),
    event text not null check (event in ('created', 'accepted', 'arrived', 'resolved')),
    actor_id uuid references public.people(id),
    actor_name text,
    note text,
    created_at timestamptz not null default clock_timestamp()
);

create index check_events_check_id_idx on public.check_events(check_id);
create index checks_owner_idx on public.checks(owner_id);
create index checks_closed_idx on public.checks(resolved_at desc, id) where status = 'resolved';

create table public.feed_status (
    id smallint primary key default 1 check (id = 1),
    status text not null check (status in ('live', 'replay', 'offline', 'detector_unavailable')),
    label text not null,
    fps numeric not null check (fps between 0 and 1000),
    source text not null check (source in ('live', 'replay')),
    updated_at timestamptz not null default clock_timestamp()
);

create table app_private.app_settings (
    key text primary key,
    value text not null check (value <> '')
);

insert into app_private.app_settings(key, value) values ('default_place', 'Near the stage');

alter table public.people enable row level security;
alter table public.checks enable row level security;
alter table public.check_events enable row level security;
alter table public.feed_status enable row level security;
alter table app_private.app_settings enable row level security;

revoke all on public.people, public.checks, public.check_events, public.feed_status from public, anon, authenticated, service_role;
grant select on public.people, public.checks, public.check_events, public.feed_status to authenticated;
revoke all on app_private.app_settings from public, anon, authenticated, service_role;

create function app_private.is_member()
returns boolean language sql stable security definer set search_path = '' as $$
    select exists (select from public.people where id = (select auth.uid()));
$$;

create policy joined_people on public.people for select to authenticated using ((select app_private.is_member()));
create policy joined_checks on public.checks for select to authenticated using ((select app_private.is_member()));
create policy joined_events on public.check_events for select to authenticated using ((select app_private.is_member()));
create policy joined_feed on public.feed_status for select to authenticated using ((select app_private.is_member()));

create policy joined_presence_read on realtime.messages for select to authenticated using (
    extension = 'presence' and (select realtime.topic()) = 'welfare-room' and (select app_private.is_member())
);
create policy joined_presence_write on realtime.messages for insert to authenticated with check (
    extension = 'presence' and (select realtime.topic()) = 'welfare-room' and (select app_private.is_member())
);

alter publication supabase_realtime add table public.checks, public.feed_status, public.people;

create function app_private.join_room(p_name text, p_room_code text)
returns public.people language plpgsql security definer set search_path = '' as $$
declare
    person public.people;
    caller uuid := (select auth.uid());
    room_code text;
    cleaned text := left(regexp_replace(p_name, '^[[:space:]]+|[[:space:]]+$', '', 'g'), 40);
begin
    if caller is null then
        raise exception 'Join first' using errcode = '42501';
    end if;
    select value into room_code from app_private.app_settings where key = 'room_code';
    if room_code is null or p_room_code is distinct from room_code then
        raise exception 'Wrong room code';
    end if;
    if coalesce(cleaned, '') = '' then
        raise exception 'Type your name to join';
    end if;
    insert into public.people(id, name) values (caller, cleaned)
    on conflict (id) do update set name = excluded.name returning * into person;
    update public.checks set owner_name = cleaned
    where owner_id = caller and status in ('accepted', 'arrived') and owner_name <> cleaned;
    return person;
end;
$$;

create function app_private.get_invite_code()
returns text language plpgsql stable security definer set search_path = '' as $$
begin
    if not app_private.is_member() then
        raise exception 'Join first' using errcode = '42501';
    end if;
    return (select value from app_private.app_settings where key = 'room_code');
end;
$$;

create function app_private.act(p_id uuid, p_action text, p_outcome text default null)
returns public.checks language plpgsql security definer set search_path = '' as $$
declare
    actor public.people;
    saved public.checks;
begin
    -- Serialize this actor's actions with their own name changes.
    select * into actor from public.people where id = (select auth.uid()) for share;
    if not found then
        raise exception 'Join first' using errcode = '42501';
    end if;

    case p_action
        when 'accepted' then
            update public.checks set status = 'accepted', owner_id = actor.id, owner_name = actor.name
            where id = p_id and status = 'needs_responder' returning * into saved;
            if not found then
                select * into saved from public.checks where id = p_id;
                if not found then
                    raise exception 'Check not found';
                elsif saved.owner_id = actor.id then
                    return saved;
                end if;
                raise exception '% accepted this first.', saved.owner_name;
            end if;
        when 'arrived' then
            update public.checks set status = 'arrived'
            where id = p_id and status = 'accepted' and owner_id = actor.id returning * into saved;
            if not found then
                raise exception 'Only the assigned responder can mark arrival';
            end if;
        when 'resolved' then
            if p_outcome is null or p_outcome not in ('Confirmed person down', 'False alarm', 'Couldn''t verify') then
                raise exception 'Choose a resolution outcome';
            end if;
            update public.checks set status = 'resolved', outcome = p_outcome, resolved_at = clock_timestamp()
            where id = p_id and status = 'arrived' and owner_id = actor.id returning * into saved;
            if not found then
                raise exception 'Only the assigned responder can resolve an arrived check';
            end if;
        else raise exception 'Unknown action';
    end case;

    insert into public.check_events(check_id, event, actor_id, actor_name, note)
    values (saved.id, p_action, actor.id, actor.name, p_outcome);
    return saved;
end;
$$;

create function public.join_room(p_name text, p_room_code text)
returns public.people language sql set search_path = '' as $$
    select app_private.join_room(p_name, p_room_code);
$$;

create function public.get_invite_code()
returns text language sql stable set search_path = '' as $$
    select app_private.get_invite_code();
$$;

create function public.accept_check(p_id uuid)
returns public.checks language sql set search_path = '' as $$
    select app_private.act(p_id, 'accepted');
$$;

create function public.arrive_check(p_id uuid)
returns public.checks language sql set search_path = '' as $$
    select app_private.act(p_id, 'arrived');
$$;

create function public.resolve_check(p_id uuid, p_outcome text)
returns public.checks language sql set search_path = '' as $$
    select app_private.act(p_id, 'resolved', p_outcome);
$$;

create function public.server_time()
returns timestamptz language sql set search_path = '' as $$
    select clock_timestamp();
$$;

create function public.room_snapshot()
returns jsonb language sql stable set search_path = '' as $$
    select jsonb_build_object(
        'checks', coalesce((select jsonb_agg(c) from (
            select * from public.checks where status <> 'resolved'
            union all
            (select * from public.checks where status = 'resolved' order by resolved_at desc, id limit 30)
        ) c), '[]'::jsonb),
        'feed', (select to_jsonb(f) from public.feed_status f where id = 1),
        'people', coalesce((select jsonb_agg(p) from public.people p), '[]'::jsonb)
    );
$$;

-- Service-role RPCs stay invoker functions with only the required relation grants.
grant usage on schema app_private to service_role;
grant select on app_private.app_settings to service_role;
grant select, insert on public.checks to service_role;
grant insert on public.check_events to service_role;
grant usage on sequence public.check_events_id_seq to service_role;
grant select, insert, update on public.feed_status to service_role;

create function public.create_check(p_event jsonb)
returns public.checks language plpgsql set search_path = '' as $$
declare
    saved public.checks;
    place_label text;
begin
    select value into place_label from app_private.app_settings
    where key in ('default_place', 'zone:' || (p_event->>'zone_id'))
    order by key = 'default_place' limit 1;
    insert into public.checks(episode_id, signal, camera_id, place, source, detected_at)
    values (p_event->>'episode_id', p_event->>'signal', p_event->>'camera_id', place_label,
            p_event->>'source', (p_event->>'detected_at')::timestamptz)
    on conflict (episode_id) do nothing returning * into saved;
    if found then
        insert into public.check_events(check_id, event) values (saved.id, 'created');
    else
        select * into strict saved from public.checks where episode_id = p_event->>'episode_id';
    end if;
    return saved;
end;
$$;

create function public.report_feed(p_status text, p_label text, p_fps numeric, p_source text)
returns void language sql set search_path = '' as $$
    insert into public.feed_status (id, status, label, fps, source)
    values (1, p_status, p_label, p_fps, p_source)
    on conflict (id) do update set status = excluded.status, label = excluded.label,
        fps = excluded.fps, source = excluded.source, updated_at = clock_timestamp();
$$;

revoke execute on all functions in schema app_private from public, anon, authenticated, service_role;
grant execute on function app_private.is_member(), app_private.join_room(text,text),
    app_private.get_invite_code(), app_private.act(uuid,text,text) to authenticated;

revoke execute on function public.join_room(text,text), public.get_invite_code(), public.accept_check(uuid),
    public.arrive_check(uuid), public.resolve_check(uuid,text), public.server_time(), public.room_snapshot(),
    public.create_check(jsonb), public.report_feed(text,text,numeric,text)
    from public, anon, authenticated, service_role;

grant execute on function public.join_room(text,text), public.get_invite_code(), public.accept_check(uuid),
    public.arrive_check(uuid), public.resolve_check(uuid,text), public.server_time(), public.room_snapshot()
    to authenticated;
grant execute on function public.create_check(jsonb), public.report_feed(text,text,numeric,text) to service_role;

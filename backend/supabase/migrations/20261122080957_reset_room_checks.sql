create function app_private.reset_checks()
returns integer language plpgsql security definer set search_path = '' as $$
declare
    deleted integer;
begin
    if not app_private.is_member() then
        raise exception 'Join first' using errcode = '42501';
    end if;

    -- Hold writers until both tables are cleared so new alerts can start after the reset.
    lock table public.checks, public.check_events in share row exclusive mode;
    delete from public.check_events;
    delete from public.checks;
    get diagnostics deleted = row_count;
    return deleted;
end;
$$;

create function public.reset_checks()
returns integer language sql set search_path = '' as $$
    select app_private.reset_checks();
$$;

revoke all on function app_private.reset_checks(), public.reset_checks()
    from public, anon, authenticated, service_role;
grant execute on function app_private.reset_checks(), public.reset_checks() to authenticated;

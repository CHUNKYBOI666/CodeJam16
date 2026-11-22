alter table public.checks add column revision integer not null default 0;

create function app_private.bump_check_revision()
returns trigger language plpgsql set search_path = '' as $$
begin
    new.revision := old.revision + 1;
    return new;
end;
$$;

revoke execute on function app_private.bump_check_revision() from public, anon, authenticated, service_role;
create trigger bump_check_revision before update on public.checks
for each row execute function app_private.bump_check_revision();

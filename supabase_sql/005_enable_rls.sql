begin;

create or replace function public.current_user_email()
returns text
language sql
stable
as $$
    select nullif(lower(coalesce(auth.jwt() ->> 'email', '')), '');
$$;

create or replace function public.current_app_user_id()
returns uuid
language sql
stable
security definer
set search_path = public
as $$
    select u.id
    from public.users u
    where lower(u.email) = public.current_user_email()
    limit 1;
$$;

create or replace function public.current_surgery_id()
returns uuid
language sql
stable
security definer
set search_path = public
as $$
    select u.surgery_id
    from public.users u
    where lower(u.email) = public.current_user_email()
    limit 1;
$$;

create or replace function public.current_app_role()
returns text
language sql
stable
security definer
set search_path = public
as $$
    select u.role
    from public.users u
    where lower(u.email) = public.current_user_email()
    limit 1;
$$;

create or replace function public.current_pharmacist_id()
returns uuid
language sql
stable
security definer
set search_path = public
as $$
    select p.id
    from public.pharmacists p
    where lower(p.email) = public.current_user_email()
    limit 1;
$$;

create or replace function public.is_superuser()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select coalesce(public.current_app_role() = 'superuser', false);
$$;

create or replace function public.is_known_account()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select coalesce(public.current_app_user_id() is not null, false)
        or coalesce(public.current_pharmacist_id() is not null, false);
$$;

grant execute on function public.current_user_email() to authenticated;
grant execute on function public.current_app_user_id() to authenticated;
grant execute on function public.current_surgery_id() to authenticated;
grant execute on function public.current_app_role() to authenticated;
grant execute on function public.current_pharmacist_id() to authenticated;
grant execute on function public.is_superuser() to authenticated;
grant execute on function public.is_known_account() to authenticated;

alter table public.cover_requests enable row level security;
alter table public.pharmacists enable row level security;
alter table public.sessions enable row level security;
alter table public.sessions_staging enable row level security;
alter table public.surgeries enable row level security;
alter table public.users enable row level security;

drop policy if exists "cover_requests_select_own_or_superuser" on public.cover_requests;
create policy "cover_requests_select_own_or_superuser"
on public.cover_requests
for select
to authenticated
using (
    public.is_superuser()
    or requester_user_id = public.current_app_user_id()
);

drop policy if exists "cover_requests_insert_own_pending" on public.cover_requests;
create policy "cover_requests_insert_own_pending"
on public.cover_requests
for insert
to authenticated
with check (
    requester_user_id = public.current_app_user_id()
    and lower(requester_email) = public.current_user_email()
    and status = 'Pending'
);

drop policy if exists "cover_requests_update_superuser" on public.cover_requests;
create policy "cover_requests_update_superuser"
on public.cover_requests
for update
to authenticated
using (public.is_superuser())
with check (public.is_superuser());

drop policy if exists "cover_requests_delete_own_pending_or_superuser" on public.cover_requests;
create policy "cover_requests_delete_own_pending_or_superuser"
on public.cover_requests
for delete
to authenticated
using (
    public.is_superuser()
    or (
        requester_user_id = public.current_app_user_id()
        and status = 'Pending'
    )
);

drop policy if exists "pharmacists_select_self_or_superuser" on public.pharmacists;
create policy "pharmacists_select_self_or_superuser"
on public.pharmacists
for select
to authenticated
using (
    public.is_superuser()
    or lower(email) = public.current_user_email()
);

drop policy if exists "pharmacists_manage_superuser" on public.pharmacists;
create policy "pharmacists_manage_superuser"
on public.pharmacists
for all
to authenticated
using (public.is_superuser())
with check (public.is_superuser());

drop policy if exists "sessions_select_known_accounts" on public.sessions;
create policy "sessions_select_known_accounts"
on public.sessions
for select
to authenticated
using (public.is_known_account());

drop policy if exists "sessions_manage_superuser" on public.sessions;
create policy "sessions_manage_superuser"
on public.sessions
for all
to authenticated
using (public.is_superuser())
with check (public.is_superuser());

drop policy if exists "surgeries_select_own_or_superuser" on public.surgeries;
create policy "surgeries_select_own_or_superuser"
on public.surgeries
for select
to authenticated
using (
    public.is_superuser()
    or id = public.current_surgery_id()
);

drop policy if exists "surgeries_manage_superuser" on public.surgeries;
create policy "surgeries_manage_superuser"
on public.surgeries
for all
to authenticated
using (public.is_superuser())
with check (public.is_superuser());

drop policy if exists "users_select_self_or_superuser" on public.users;
create policy "users_select_self_or_superuser"
on public.users
for select
to authenticated
using (
    public.is_superuser()
    or lower(email) = public.current_user_email()
);

drop policy if exists "users_manage_superuser" on public.users;
create policy "users_manage_superuser"
on public.users
for all
to authenticated
using (public.is_superuser())
with check (public.is_superuser());

commit;

begin;

alter table public.sessions
    add column if not exists pharmacist_id uuid,
    add column if not exists booked_user_id uuid;

alter table public.cover_requests
    add column if not exists requester_user_id uuid;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'sessions_pharmacist_id_fkey'
    ) then
        alter table public.sessions
            add constraint sessions_pharmacist_id_fkey
            foreign key (pharmacist_id)
            references public.pharmacists(id)
            on update cascade
            on delete restrict;
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'sessions_booked_user_id_fkey'
    ) then
        alter table public.sessions
            add constraint sessions_booked_user_id_fkey
            foreign key (booked_user_id)
            references public.users(id)
            on update cascade
            on delete set null;
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'cover_requests_requester_user_id_fkey'
    ) then
        alter table public.cover_requests
            add constraint cover_requests_requester_user_id_fkey
            foreign key (requester_user_id)
            references public.users(id)
            on update cascade
            on delete set null;
    end if;
end
$$;

create index if not exists sessions_pharmacist_id_idx
    on public.sessions (pharmacist_id);

create index if not exists sessions_booked_user_id_idx
    on public.sessions (booked_user_id);

create index if not exists cover_requests_requester_user_id_idx
    on public.cover_requests (requester_user_id);

comment on column public.sessions.pharmacist_id is
'Stable relation to public.pharmacists. Keep pharmacist_name as a legacy display snapshot during migration.';

comment on column public.sessions.booked_user_id is
'Stable relation to public.users for the surgery user that booked the slot. Keep surgery/email as legacy snapshots during migration.';

comment on column public.cover_requests.requester_user_id is
'Stable relation to public.users for the requesting surgery user. Keep surgery/name/requester_email as legacy snapshots during migration.';

-- Seed missing surgeries from booked sessions before linking.
insert into public.surgeries (
    surgery_name
)
select distinct
    btrim(s.surgery) as surgery_name
from public.sessions s
where coalesce(btrim(s.surgery), '') <> ''
  and not exists (
      select 1
      from public.surgeries surg
      where lower(surg.surgery_name) = lower(btrim(s.surgery))
  );

-- Seed missing surgeries from cover requests before linking.
insert into public.surgeries (
    surgery_name
)
select distinct
    btrim(cr.surgery) as surgery_name
from public.cover_requests cr
where coalesce(btrim(cr.surgery), '') <> ''
  and not exists (
      select 1
      from public.surgeries surg
      where lower(surg.surgery_name) = lower(btrim(cr.surgery))
  );

-- Seed missing user rows from booked sessions before linking.
insert into public.users (
    email,
    name
    , surgery_id
    , role
)
select distinct
    btrim(s.email) as email,
    coalesce(nullif(btrim(split_part(s.email, '@', 1)), ''), 'Unknown User') as name,
    surg.id as surgery_id,
    'member' as role
from public.sessions s
join public.surgeries surg
  on lower(surg.surgery_name) = lower(btrim(s.surgery))
where coalesce(btrim(s.surgery), '') <> ''
  and coalesce(btrim(s.email), '') <> ''
  and not exists (
      select 1
      from public.users u
      where lower(u.email) = lower(btrim(s.email))
  );

-- Seed missing user rows from cover requests before linking.
insert into public.users (
    email,
    name,
    surgery_id,
    role
)
select distinct
    btrim(cr.requester_email) as email,
    coalesce(nullif(btrim(cr.name), ''), nullif(btrim(split_part(cr.requester_email, '@', 1)), ''), 'Unknown User') as name,
    surg.id as surgery_id,
    'member' as role
from public.cover_requests cr
join public.surgeries surg
  on lower(surg.surgery_name) = lower(btrim(cr.surgery))
where coalesce(btrim(cr.surgery), '') <> ''
  and coalesce(btrim(cr.requester_email), '') <> ''
  and not exists (
      select 1
      from public.users u
      where lower(u.email) = lower(btrim(cr.requester_email))
  );

-- Backfill session -> pharmacist relation from the legacy pharmacist_name field.
update public.sessions s
set pharmacist_id = p.id
from public.pharmacists p
where s.pharmacist_id is null
  and coalesce(btrim(s.pharmacist_name), '') <> ''
  and lower(btrim(s.pharmacist_name)) = lower(btrim(p.name));

-- Backfill session -> user relation from the legacy surgery/email pair.
update public.sessions s
set booked_user_id = u.id
from public.users u
join public.surgeries surg on surg.id = u.surgery_id
where s.booked_user_id is null
  and coalesce(btrim(s.surgery), '') <> ''
  and coalesce(btrim(s.email), '') <> ''
  and lower(btrim(s.surgery)) = lower(btrim(surg.surgery_name))
  and lower(btrim(s.email)) = lower(btrim(u.email));

-- Backfill cover_request -> user relation from the legacy surgery/requester email pair.
update public.cover_requests cr
set requester_user_id = u.id
from public.users u
join public.surgeries surg on surg.id = u.surgery_id
where cr.requester_user_id is null
  and coalesce(btrim(cr.surgery), '') <> ''
  and coalesce(btrim(cr.requester_email), '') <> ''
  and lower(btrim(cr.surgery)) = lower(btrim(surg.surgery_name))
  and lower(btrim(cr.requester_email)) = lower(btrim(u.email));

create or replace function public.sync_session_legacy_columns()
returns trigger
language plpgsql
as $$
declare
    linked_pharmacist_name text;
    linked_surgery text;
    linked_email text;
begin
    if new.pharmacist_id is not null then
        select p.name
        into linked_pharmacist_name
        from public.pharmacists p
        where p.id = new.pharmacist_id;

        if linked_pharmacist_name is not null then
            new.pharmacist_name = linked_pharmacist_name;
        end if;
    end if;

    if new.booked_user_id is not null then
        select surg.surgery_name, u.email
        into linked_surgery, linked_email
        from public.users u
        join public.surgeries surg on surg.id = u.surgery_id
        where u.id = new.booked_user_id;

        if linked_surgery is not null then
            new.surgery = linked_surgery;
        end if;

        if linked_email is not null then
            new.email = linked_email;
        end if;
    end if;

    if coalesce(new.booked, false) = false and new.booked_user_id is null then
        new.surgery = null;
        new.email = null;
    end if;

    return new;
end;
$$;

drop trigger if exists sync_session_legacy_columns on public.sessions;
create trigger sync_session_legacy_columns
before insert or update on public.sessions
for each row
execute function public.sync_session_legacy_columns();

create or replace function public.sync_cover_request_legacy_columns()
returns trigger
language plpgsql
as $$
declare
    linked_surgery text;
    linked_email text;
    linked_name text;
begin
    if new.requester_user_id is not null then
        select surg.surgery_name, u.email, u.name
        into linked_surgery, linked_email, linked_name
        from public.users u
        join public.surgeries surg on surg.id = u.surgery_id
        where u.id = new.requester_user_id;

        if linked_surgery is not null then
            new.surgery = linked_surgery;
        end if;

        if linked_email is not null then
            new.requester_email = linked_email;
        end if;

        if coalesce(nullif(btrim(new.name), ''), '') = '' and linked_name is not null then
            new.name = linked_name;
        end if;
    end if;

    return new;
end;
$$;

drop trigger if exists sync_cover_request_legacy_columns on public.cover_requests;
create trigger sync_cover_request_legacy_columns
before insert or update on public.cover_requests
for each row
execute function public.sync_cover_request_legacy_columns();

create or replace view public.relationship_backfill_audit as
select
    'sessions'::text as source_table,
    s.id as row_id,
    s.unique_code as legacy_key,
    s.date::text as primary_date,
    s.am_pm as slot_label,
    s.pharmacist_name as legacy_pharmacist_name,
    s.surgery as legacy_surgery,
    s.email as legacy_email,
    s.pharmacist_id is not null as pharmacist_linked,
    case
        when coalesce(btrim(s.surgery), '') = '' and coalesce(btrim(s.email), '') = '' then true
        else s.booked_user_id is not null
    end as user_linked
from public.sessions s
where s.pharmacist_id is null
   or (
       coalesce(btrim(s.surgery), '') <> ''
       and coalesce(btrim(s.email), '') <> ''
       and s.booked_user_id is null
   )

union all

select
    'cover_requests'::text as source_table,
    cr.uuid as row_id,
    null::text as legacy_key,
    cr.cover_date::text as primary_date,
    cr.session as slot_label,
    null::text as legacy_pharmacist_name,
    cr.surgery as legacy_surgery,
    cr.requester_email as legacy_email,
    true as pharmacist_linked,
    cr.requester_user_id is not null as user_linked
from public.cover_requests cr
where coalesce(btrim(cr.surgery), '') <> ''
  and coalesce(btrim(cr.requester_email), '') <> ''
  and cr.requester_user_id is null;

comment on view public.relationship_backfill_audit is
'Rows that could not be linked from legacy text fields to the new UUID-based relations.';

update public.surgeries s
set user_ids = coalesce(
    (
        select array_agg(u.id order by lower(u.email), lower(u.name), u.id)
        from public.users u
        where u.surgery_id = s.id
    ),
    '{}'::uuid[]
);

commit;

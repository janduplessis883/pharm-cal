begin;

create table if not exists public.surgeries (
    id uuid primary key default gen_random_uuid(),
    list_size integer,
    surgery_name text not null,
    user_ids uuid[] not null default '{}'::uuid[],
    created_at timestamptz not null default timezone('utc', now()),
    updated_at timestamptz not null default timezone('utc', now()),
    constraint surgeries_surgery_name_not_blank check (btrim(surgery_name) <> ''),
    constraint surgeries_list_size_non_negative check (list_size is null or list_size >= 0)
);

create unique index if not exists surgeries_surgery_name_lower_key
    on public.surgeries (lower(surgery_name));

create or replace function public.refresh_surgery_user_ids(target_surgery_id uuid)
returns void
language plpgsql
as $$
begin
    if target_surgery_id is null then
        return;
    end if;

    update public.surgeries s
    set user_ids = coalesce(
        (
            select array_agg(u.id order by lower(u.email), lower(u.name), u.id)
            from public.users u
            where u.surgery_id = target_surgery_id
        ),
        '{}'::uuid[]
    )
    where s.id = target_surgery_id;
end;
$$;

create temporary table if not exists tmp_user_dedup_map (
    duplicate_id uuid primary key,
    canonical_id uuid not null
) on commit drop;

do $$
begin
    if exists (
        select 1
        from information_schema.columns
        where table_schema = 'public'
          and table_name = 'users'
          and column_name = 'surgery'
    ) then
        insert into public.surgeries (surgery_name, list_size)
        select distinct
            btrim(u.surgery) as surgery_name,
            max(u.list_size) over (partition by lower(btrim(u.surgery))) as list_size
        from public.users u
        where coalesce(btrim(u.surgery), '') <> ''
        on conflict ((lower(surgery_name))) do update
        set list_size = coalesce(excluded.list_size, public.surgeries.list_size),
            updated_at = timezone('utc', now());

        alter table public.users
            add column if not exists surgery_id uuid,
            add column if not exists role text;

        if exists (
            select 1
            from information_schema.columns
            where table_schema = 'public'
              and table_name = 'users'
              and column_name = 'surgery'
              and is_nullable = 'NO'
        ) then
            alter table public.users
                alter column surgery drop not null;
        end if;

        update public.users u
        set surgery_id = s.id
        from public.surgeries s
        where u.surgery_id is null
          and coalesce(btrim(u.surgery), '') <> ''
          and lower(btrim(u.surgery)) = lower(btrim(s.surgery_name));

        update public.users
        set role = coalesce(nullif(btrim(role), ''), 'member');

        update public.users
        set name = coalesce(nullif(btrim(name), ''), split_part(email, '@', 1), 'Unknown User')
        where coalesce(btrim(name), '') = '';

        insert into tmp_user_dedup_map (duplicate_id, canonical_id)
        select duplicate_id, canonical_id
        from (
            select
                u.id as duplicate_id,
                first_value(u.id) over (
                    partition by lower(btrim(u.email))
                    order by
                        case when u.surgery_id is not null then 0 else 1 end,
                        u.created_at nulls last,
                        u.id
                ) as canonical_id
            from public.users u
            where coalesce(btrim(u.email), '') <> ''
        ) ranked
        where duplicate_id <> canonical_id
        on conflict (duplicate_id) do update
        set canonical_id = excluded.canonical_id;

        update public.sessions s
        set booked_user_id = m.canonical_id
        from tmp_user_dedup_map m
        where s.booked_user_id = m.duplicate_id;

        update public.cover_requests cr
        set requester_user_id = m.canonical_id
        from tmp_user_dedup_map m
        where cr.requester_user_id = m.duplicate_id;

        delete from public.users u
        using tmp_user_dedup_map m
        where u.id = m.duplicate_id;

        alter table public.users
            alter column name set not null,
            alter column surgery_id set not null,
            alter column role set not null;

        if not exists (
            select 1
            from pg_constraint
            where conname = 'users_surgery_id_fkey'
        ) then
            alter table public.users
                add constraint users_surgery_id_fkey
                foreign key (surgery_id)
                references public.surgeries(id)
                on update cascade
                on delete cascade;
        end if;

        drop index if exists public.users_surgery_email_lower_key;
        drop index if exists public.users_surgery_lower_idx;

        create unique index if not exists users_email_lower_key
            on public.users (lower(email));

        create index if not exists users_surgery_id_idx
            on public.users (surgery_id);

        create index if not exists users_role_lower_idx
            on public.users (lower(role));
    end if;
end
$$;

insert into public.surgeries (surgery_name)
select distinct
    btrim(s.surgery) as surgery_name
from public.sessions s
where coalesce(btrim(s.surgery), '') <> ''
on conflict ((lower(surgery_name))) do nothing;

insert into public.surgeries (surgery_name)
select distinct
    btrim(cr.surgery) as surgery_name
from public.cover_requests cr
where coalesce(btrim(cr.surgery), '') <> ''
on conflict ((lower(surgery_name))) do nothing;

insert into public.users (name, email, surgery_id, role)
select
    staged.name,
    staged.email,
    staged.surgery_id,
    staged.role
from (
    select
        coalesce(nullif(btrim(split_part(s.email, '@', 1)), ''), 'Unknown User') as name,
        btrim(s.email) as email,
        surg.id as surgery_id,
        'member' as role,
        row_number() over (
            partition by lower(btrim(s.email))
            order by surg.id
        ) as email_rank
    from public.sessions s
    join public.surgeries surg
        on lower(btrim(surg.surgery_name)) = lower(btrim(s.surgery))
    where coalesce(btrim(s.surgery), '') <> ''
      and coalesce(btrim(s.email), '') <> ''
) staged
where staged.email_rank = 1
  and not exists (
      select 1
      from public.users u
      where lower(u.email) = lower(staged.email)
  );

insert into public.users (name, email, surgery_id, role)
select
    staged.name,
    staged.email,
    staged.surgery_id,
    staged.role
from (
    select
        coalesce(nullif(btrim(cr.name), ''), nullif(btrim(split_part(cr.requester_email, '@', 1)), ''), 'Unknown User') as name,
        btrim(cr.requester_email) as email,
        surg.id as surgery_id,
        'member' as role,
        row_number() over (
            partition by lower(btrim(cr.requester_email))
            order by surg.id
        ) as email_rank
    from public.cover_requests cr
    join public.surgeries surg
        on lower(btrim(surg.surgery_name)) = lower(btrim(cr.surgery))
    where coalesce(btrim(cr.surgery), '') <> ''
      and coalesce(btrim(cr.requester_email), '') <> ''
) staged
where staged.email_rank = 1
  and not exists (
      select 1
      from public.users u
      where lower(u.email) = lower(staged.email)
  );

insert into tmp_user_dedup_map (duplicate_id, canonical_id)
select duplicate_id, canonical_id
from (
    select
        u.id as duplicate_id,
        first_value(u.id) over (
            partition by lower(btrim(u.email))
            order by
                u.created_at nulls last,
                u.id
        ) as canonical_id
    from public.users u
    where coalesce(btrim(u.email), '') <> ''
) ranked
where duplicate_id <> canonical_id
on conflict (duplicate_id) do update
set canonical_id = excluded.canonical_id;

update public.sessions s
set booked_user_id = m.canonical_id
from tmp_user_dedup_map m
where s.booked_user_id = m.duplicate_id;

update public.cover_requests cr
set requester_user_id = m.canonical_id
from tmp_user_dedup_map m
where cr.requester_user_id = m.duplicate_id;

delete from public.users u
using tmp_user_dedup_map m
where u.id = m.duplicate_id;

create unique index if not exists users_email_lower_key
    on public.users (lower(email));

create or replace function public.sync_surgery_user_ids_from_user()
returns trigger
language plpgsql
as $$
begin
    if tg_op = 'DELETE' then
        perform public.refresh_surgery_user_ids(old.surgery_id);
        return old;
    end if;

    perform public.refresh_surgery_user_ids(new.surgery_id);
    if tg_op = 'UPDATE' and old.surgery_id is distinct from new.surgery_id then
        perform public.refresh_surgery_user_ids(old.surgery_id);
    end if;
    return new;
end;
$$;

drop trigger if exists sync_surgery_user_ids_from_user on public.users;
create trigger sync_surgery_user_ids_from_user
after insert or update or delete on public.users
for each row
execute function public.sync_surgery_user_ids_from_user();

update public.sessions sess
set booked_user_id = u.id
from public.users u
join public.surgeries surg on surg.id = u.surgery_id
where coalesce(btrim(sess.surgery), '') <> ''
  and coalesce(btrim(sess.email), '') <> ''
  and lower(btrim(sess.surgery)) = lower(btrim(surg.surgery_name))
  and lower(btrim(sess.email)) = lower(btrim(u.email))
  and (sess.booked_user_id is null or sess.booked_user_id <> u.id);

update public.cover_requests cr
set requester_user_id = u.id
from public.users u
join public.surgeries surg on surg.id = u.surgery_id
where coalesce(btrim(cr.surgery), '') <> ''
  and coalesce(btrim(cr.requester_email), '') <> ''
  and lower(btrim(cr.surgery)) = lower(btrim(surg.surgery_name))
  and lower(btrim(cr.requester_email)) = lower(btrim(u.email))
  and (cr.requester_user_id is null or cr.requester_user_id <> u.id);

create or replace function public.sync_session_legacy_columns()
returns trigger
language plpgsql
as $$
declare
    linked_pharmacist_name text;
    linked_surgery_name text;
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
        select s.surgery_name, u.email
        into linked_surgery_name, linked_email
        from public.users u
        join public.surgeries s on s.id = u.surgery_id
        where u.id = new.booked_user_id;

        if linked_surgery_name is not null then
            new.surgery = linked_surgery_name;
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

create or replace function public.sync_cover_request_legacy_columns()
returns trigger
language plpgsql
as $$
declare
    linked_surgery_name text;
    linked_email text;
    linked_name text;
begin
    if new.requester_user_id is not null then
        select s.surgery_name, u.email, u.name
        into linked_surgery_name, linked_email, linked_name
        from public.users u
        join public.surgeries s on s.id = u.surgery_id
        where u.id = new.requester_user_id;

        if linked_surgery_name is not null then
            new.surgery = linked_surgery_name;
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

update public.surgeries s
set user_ids = coalesce(
    (
        select array_agg(u.id order by lower(u.email), lower(u.name), u.id)
        from public.users u
        where u.surgery_id = s.id
    ),
    '{}'::uuid[]
);

comment on table public.surgeries is
'Stores one row per surgery, including list size and a denormalized cache of related user ids.';

comment on table public.users is
'Stores users that belong to surgeries. Replaces the previous surgery-contact-only users model.';

comment on column public.users.surgery_id is
'Foreign key to public.surgeries. A surgery can have many users.';

comment on column public.users.role is
'Application role for the surgery user, for example member or admin.';

comment on column public.surgeries.user_ids is
'Denormalized cache of related public.users ids, maintained by trigger for convenience.';

commit;

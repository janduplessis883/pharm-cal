begin;

-- Build a stable legacy key from the original Google Sheets slot identity.
-- We intentionally avoid mutable fields such as booked/surgery/email/pharmacist_name
-- so the generated UUID stays the same across later edits to a session row.
create or replace function public.session_legacy_key(
    unique_code text,
    session_date date,
    session_am_pm text,
    session_slot_index integer
)
returns text
language sql
immutable
as $$
    select concat_ws(
        '|',
        coalesce(nullif(btrim(unique_code), ''), '__missing_unique_code__'),
        coalesce(session_date::text, '__missing_date__'),
        coalesce(lower(nullif(btrim(session_am_pm), '')), '__missing_am_pm__'),
        coalesce(session_slot_index::text, '__missing_slot_index__')
    );
$$;

comment on function public.session_legacy_key(text, date, text, integer)
is 'Returns the stable legacy identity used to derive deterministic UUIDs for session imports.';

-- Deterministic UUID helper based on an md5 hash so repeated imports of the
-- same legacy session produce the same UUID even when the CSV has no id column.
create or replace function public.deterministic_uuid(source_text text)
returns uuid
language sql
immutable
as $$
    with hash as (
        select md5(coalesce(source_text, '')) as m
    )
    select (
        substr(m, 1, 8) || '-' ||
        substr(m, 9, 4) || '-' ||
        '4' || substr(m, 14, 3) || '-' ||
        '8' || substr(m, 18, 3) || '-' ||
        substr(m, 21, 12)
    )::uuid
    from hash;
$$;

comment on function public.deterministic_uuid(text)
is 'Returns the same UUID for the same input text. Useful for backward-compatible imports.';

create or replace function public.session_legacy_uuid(
    unique_code text,
    session_date date,
    session_am_pm text,
    session_slot_index integer
)
returns uuid
language sql
immutable
as $$
    select public.deterministic_uuid(
        'session|' || public.session_legacy_key(
            unique_code,
            session_date,
            session_am_pm,
            session_slot_index
        )
    );
$$;

comment on function public.session_legacy_uuid(text, date, text, integer)
is 'Builds a deterministic session UUID from the legacy slot identity.';

-- Example import pattern:
--
-- 1. Load your CSV into a staging table first, for example:
--    create table if not exists public.sessions_staging (
--        id uuid,
--        unique_code text,
--        date date not null,
--        am_pm text not null,
--        booked boolean,
--        surgery text,
--        email text,
--        pharmacist_name text not null,
--        slot_index integer not null
--    );
--
-- 2. Insert from staging into public.sessions using a deterministic UUID.
--    This makes the import backward-compatible because the same legacy row
--    always gets the same id on every re-import.
--
--    with staged as (
--        select
--            coalesce(
--                id,
--                public.session_legacy_uuid(unique_code, date, am_pm, slot_index)
--            ) as resolved_id,
--            unique_code,
--            date,
--            lower(am_pm) as am_pm,
--            coalesce(booked, false) as booked,
--            nullif(btrim(surgery), '') as surgery,
--            nullif(btrim(email), '') as email,
--            pharmacist_name,
--            slot_index
--        from public.sessions_staging
--    )
--    insert into public.sessions as target (
--        id,
--        unique_code,
--        date,
--        am_pm,
--        booked,
--        surgery,
--        email,
--        pharmacist_name,
--        slot_index
--    )
--    select
--        resolved_id,
--        unique_code,
--        date,
--        am_pm,
--        booked,
--        surgery,
--        email,
--        pharmacist_name,
--        slot_index
--    from staged
--    on conflict (id) do update
--    set unique_code = excluded.unique_code,
--        date = excluded.date,
--        am_pm = excluded.am_pm,
--        booked = excluded.booked,
--        surgery = excluded.surgery,
--        email = excluded.email,
--        pharmacist_name = excluded.pharmacist_name,
--        slot_index = excluded.slot_index,
--        updated_at = timezone('utc', now());
--
-- 3. If another table still stores the old Google Sheets identity, backfill
--    its new session_id column by joining on the legacy slot fields.
--
--    alter table public.some_child_table add column if not exists session_id uuid;
--
--    update public.some_child_table child
--    set session_id = s.id
--    from public.sessions s
--    where child.session_id is null
--      and child.unique_code = s.unique_code
--      and child.session_date = s.date
--      and lower(child.am_pm) = s.am_pm
--      and child.slot_index = s.slot_index;
--
-- Important:
-- - If public.sessions already contains live UUIDs that other tables reference,
--   do not rewrite existing ids. Use the helper above only for new imports, or
--   update child tables in the same transaction before changing any session ids.
-- - Keeping unique_code alongside id is the backward-compatible bridge for any
--   older code paths that still know about the Google Sheets identifier.

commit;

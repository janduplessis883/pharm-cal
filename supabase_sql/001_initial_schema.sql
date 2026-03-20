begin;

create extension if not exists pgcrypto;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = timezone('utc', now());
    return new;
end;
$$;

create table if not exists public.pharmacists (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    email text not null,
    created_at timestamptz not null default timezone('utc', now()),
    updated_at timestamptz not null default timezone('utc', now()),
    constraint pharmacists_name_not_blank check (btrim(name) <> ''),
    constraint pharmacists_email_not_blank check (btrim(email) <> '')
);

create unique index if not exists pharmacists_email_lower_key
    on public.pharmacists (lower(email));

create index if not exists pharmacists_name_lower_idx
    on public.pharmacists (lower(name));

comment on table public.pharmacists is 'Imported from PharmaCal - Parmacists.csv.';

drop trigger if exists set_pharmacists_updated_at on public.pharmacists;
create trigger set_pharmacists_updated_at
before update on public.pharmacists
for each row
execute function public.set_updated_at();

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

comment on table public.surgeries is 'Imported from PharmaCal - Surgeries.csv. Stores one row per surgery plus its related user ids.';

drop trigger if exists set_surgeries_updated_at on public.surgeries;
create trigger set_surgeries_updated_at
before update on public.surgeries
for each row
execute function public.set_updated_at();

create table if not exists public.users (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    email text not null,
    surgery_id uuid not null references public.surgeries(id) on update cascade on delete cascade,
    role text not null default 'member',
    created_at timestamptz not null default timezone('utc', now()),
    updated_at timestamptz not null default timezone('utc', now()),
    constraint users_name_not_blank check (btrim(name) <> ''),
    constraint users_email_not_blank check (btrim(email) <> ''),
    constraint users_role_not_blank check (btrim(role) <> '')
);

create unique index if not exists users_email_lower_key
    on public.users (lower(email));

create index if not exists users_surgery_id_idx
    on public.users (surgery_id);

create index if not exists users_role_lower_idx
    on public.users (lower(role));

comment on table public.users is 'Stores surgery users and contacts. Each user belongs to one surgery.';

drop trigger if exists set_users_updated_at on public.users;
create trigger set_users_updated_at
before update on public.users
for each row
execute function public.set_updated_at();

create table if not exists public.sessions (
    id uuid primary key default gen_random_uuid(),
    unique_code text,
    date date not null,
    am_pm text not null,
    booked boolean not null default false,
    surgery text,
    email text,
    pharmacist_name text not null,
    slot_index integer not null,
    created_at timestamptz not null default timezone('utc', now()),
    updated_at timestamptz not null default timezone('utc', now()),
    constraint sessions_am_pm_check check (am_pm in ('am', 'pm')),
    constraint sessions_slot_index_non_negative check (slot_index >= 0),
    constraint sessions_pharmacist_name_not_blank check (btrim(pharmacist_name) <> '')
);

create index if not exists sessions_unique_code_idx
    on public.sessions (unique_code);

create index if not exists sessions_date_idx
    on public.sessions (date);

create index if not exists sessions_date_am_pm_idx
    on public.sessions (date, am_pm);

create index if not exists sessions_booked_idx
    on public.sessions (booked);

create index if not exists sessions_pharmacist_name_lower_idx
    on public.sessions (lower(pharmacist_name));

create index if not exists sessions_email_lower_idx
    on public.sessions (lower(email));

comment on table public.sessions is 'Imported from PharmaCal - Sessions.csv. Uses UUID primary keys for every session row.';
comment on column public.sessions.unique_code is 'Legacy Google Sheets slot code retained for migration only. The exported CSV contains duplicates, so this column is not enforced as unique.';

drop trigger if exists set_sessions_updated_at on public.sessions;
create trigger set_sessions_updated_at
before update on public.sessions
for each row
execute function public.set_updated_at();

create table if not exists public.cover_requests (
    uuid uuid primary key default gen_random_uuid(),
    cover_date date not null,
    surgery text not null,
    name text not null,
    session text not null,
    reason text not null,
    "desc" text,
    submission_timestamp timestamptz not null default timezone('utc', now()),
    requester_email text not null,
    status text not null default 'Pending',
    decision_timestamp timestamptz,
    created_at timestamptz not null default timezone('utc', now()),
    updated_at timestamptz not null default timezone('utc', now()),
    constraint cover_requests_surgery_not_blank check (btrim(surgery) <> ''),
    constraint cover_requests_name_not_blank check (btrim(name) <> ''),
    constraint cover_requests_session_check check (session in ('AM', 'PM', 'Full-day')),
    constraint cover_requests_reason_not_blank check (btrim(reason) <> ''),
    constraint cover_requests_requester_email_not_blank check (btrim(requester_email) <> ''),
    constraint cover_requests_status_check check (status in ('Pending', 'Approved', 'Rejected'))
);

create index if not exists cover_requests_cover_date_idx
    on public.cover_requests (cover_date);

create index if not exists cover_requests_status_idx
    on public.cover_requests (status);

create index if not exists cover_requests_requester_email_lower_idx
    on public.cover_requests (lower(requester_email));

comment on table public.cover_requests is 'Imported from PharmaCal - Cover_requests.csv.';

drop trigger if exists set_cover_requests_updated_at on public.cover_requests;
create trigger set_cover_requests_updated_at
before update on public.cover_requests
for each row
execute function public.set_updated_at();

commit;

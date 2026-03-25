-- Run this in the Supabase SQL Editor after applying 005_enable_rls.sql.
-- Replace the placeholder emails before running.
--
-- This script is designed as a smoke test:
-- 1. Verify RLS is enabled.
-- 2. Simulate a normal authenticated surgery user.
-- 3. Simulate a superuser.
--
-- Write tests are wrapped in transactions and rolled back.

-- ---------------------------------------------------------------------------
-- Step 1: Confirm RLS is enabled on the expected public tables
-- ---------------------------------------------------------------------------
select schemaname, tablename, rowsecurity
from pg_tables
where schemaname = 'public'
  and tablename in (
    'cover_requests',
    'pharmacists',
    'sessions',
    'sessions_staging',
    'surgeries',
    'users'
  )
order by tablename;

-- ---------------------------------------------------------------------------
-- Step 2: Replace these two emails with real records from your project
-- ---------------------------------------------------------------------------
-- Normal user email: a row in public.users with role <> 'superuser'
-- Superuser email: a row in public.users with role = 'superuser'

-- ---------------------------------------------------------------------------
-- Step 3: Normal authenticated user read tests
-- Expected:
-- - helper functions resolve the current user and surgery
-- - users returns only their own row
-- - surgeries returns only their own surgery
-- - cover_requests returns only their own requests
-- - sessions is readable
-- - pharmacists returns zero rows unless the same email is also a pharmacist
-- ---------------------------------------------------------------------------
begin;

set local role authenticated;
set local "request.jwt.claims" = '{
  "role": "authenticated",
  "email": "NORMAL_USER_EMAIL_HERE"
}';

select
  public.current_user_email() as current_user_email,
  public.current_app_user_id() as current_app_user_id,
  public.current_surgery_id() as current_surgery_id,
  public.current_app_role() as current_app_role,
  public.current_pharmacist_id() as current_pharmacist_id,
  public.is_superuser() as is_superuser,
  public.is_known_account() as is_known_account;

select id, email, name, role, surgery_id
from public.users
order by email;

select id, surgery_name, list_size
from public.surgeries
order by surgery_name;

select uuid, cover_date, surgery, name, session, status, requester_user_id
from public.cover_requests
order by cover_date, submission_timestamp;

select id, date, am_pm, booked, pharmacist_name, booked_user_id
from public.sessions
order by date, am_pm, slot_index
limit 20;

select id, name, email
from public.pharmacists
order by email;

rollback;

-- ---------------------------------------------------------------------------
-- Step 4: Normal authenticated user write tests
-- Expected:
-- - insert of own pending cover request succeeds
-- - update to approve that request updates zero rows
-- - delete of own pending request succeeds
-- ---------------------------------------------------------------------------
begin;

set local role authenticated;
set local "request.jwt.claims" = '{
  "role": "authenticated",
  "email": "NORMAL_USER_EMAIL_HERE"
}';

with inserted_request as (
  insert into public.cover_requests (
    cover_date,
    surgery,
    name,
    session,
    reason,
    "desc",
    requester_email,
    status,
    requester_user_id
  )
  values (
    current_date + 30,
    'RLS smoke test surgery',
    'RLS Smoke Test',
    'AM',
    'Other',
    'Inserted during RLS smoke test',
    public.current_user_email(),
    'Pending',
    public.current_app_user_id()
  )
  returning uuid
)
select *
from inserted_request;

with target_request as (
  select uuid
  from public.cover_requests
  where requester_user_id = public.current_app_user_id()
    and name = 'RLS Smoke Test'
  order by created_at desc
  limit 1
),
attempted_update as (
  update public.cover_requests cr
  set status = 'Approved'
  from target_request tr
  where cr.uuid = tr.uuid
  returning cr.uuid
)
select count(*) as normal_user_rows_updated
from attempted_update;

delete from public.cover_requests
where requester_user_id = public.current_app_user_id()
  and name = 'RLS Smoke Test'
returning uuid, status;

rollback;

-- ---------------------------------------------------------------------------
-- Step 5: Superuser read/write tests
-- Expected:
-- - helper functions show is_superuser = true
-- - users, surgeries, cover_requests, pharmacists, sessions are readable
-- - superuser can update a cover request inside the transaction
-- ---------------------------------------------------------------------------
begin;

set local role authenticated;
set local "request.jwt.claims" = '{
  "role": "authenticated",
  "email": "SUPERUSER_EMAIL_HERE"
}';

select
  public.current_user_email() as current_user_email,
  public.current_app_user_id() as current_app_user_id,
  public.current_surgery_id() as current_surgery_id,
  public.current_app_role() as current_app_role,
  public.current_pharmacist_id() as current_pharmacist_id,
  public.is_superuser() as is_superuser,
  public.is_known_account() as is_known_account;

select count(*) as users_visible from public.users;
select count(*) as surgeries_visible from public.surgeries;
select count(*) as cover_requests_visible from public.cover_requests;
select count(*) as pharmacists_visible from public.pharmacists;
select count(*) as sessions_visible from public.sessions;

with target_request as (
  select uuid
  from public.cover_requests
  where status = 'Pending'
  order by created_at desc
  limit 1
)
update public.cover_requests cr
set status = 'Approved',
    decision_timestamp = timezone('utc', now())
from target_request tr
where cr.uuid = tr.uuid
returning cr.uuid, cr.status, cr.decision_timestamp;

rollback;

-- ---------------------------------------------------------------------------
-- Step 6: Optional negative test for anonymous access
-- Expected:
-- - no rows from protected tables
-- ---------------------------------------------------------------------------
begin;

set local role anon;
set local "request.jwt.claims" = '{
  "role": "anon",
  "email": "anonymous@example.com"
}';

select count(*) as anon_sessions_visible from public.sessions;
select count(*) as anon_users_visible from public.users;
select count(*) as anon_cover_requests_visible from public.cover_requests;

rollback;

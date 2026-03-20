# Relationship Migration Notes

## Current Google Sheets Link Model

The app does not use explicit foreign keys today. It relies on a mix of:

- `sessions.unique_code` to find and update a schedule row
- `sessions.pharmacist_name` to look up the pharmacist email in the pharmacists sheet
- `sessions.surgery` + `sessions.email` to represent the booking owner
- `cover_requests.surgery` + `cover_requests.requester_email` to represent the requester

This means a rename can break lookups even when the logical relationship is still the same.

## Where The App Does This Today

- Booking writes surgery/email text directly into the session row in [core.py](/Users/janduplessis/code/janduplessis883/streamlit-projects/pharm-cal/core.py#L489)
- Cancellation resolves pharmacist email by matching `pharmacist_name` text in [core.py](/Users/janduplessis/code/janduplessis883/streamlit-projects/pharm-cal/core.py#L413)
- Availability editing uses `unique_code` as the slot row identity in [app.py](/Users/janduplessis/code/janduplessis883/streamlit-projects/pharm-cal/app.py#L998)
- Booking UI picks a surgery name, then derives the email from the surgeries sheet in [app.py](/Users/janduplessis/code/janduplessis883/streamlit-projects/pharm-cal/app.py#L1192)
- Surgery defaults are matched by surgery-name text in [app.py](/Users/janduplessis/code/janduplessis883/streamlit-projects/pharm-cal/app.py#L48)

## Target Supabase Model

Keep the legacy text columns for backward compatibility, but add stable UUID relations and separate surgeries from users:

- `sessions.id`: stable session row id
- `sessions.pharmacist_id -> pharmacists.id`
- `sessions.booked_user_id -> users.id`
- `cover_requests.requester_user_id -> users.id`
- `users.surgery_id -> surgeries.id`

The target split is:

- `surgeries`: `id`, `surgery_name`, `list_size`, `user_ids`
- `users`: `id`, `name`, `email`, `surgery_id`, `role`

So the current legacy surgery/email pair maps to a user row, and the surgery name/list size live on the parent surgery row.

## Migration Order

1. Import `pharmacists.csv` into `public.pharmacists`
2. Import surgeries into `public.surgeries`
3. Import users into `public.users`
4. Import sessions with deterministic ids from the legacy slot identity
5. Run [003_id_based_relationships.sql](/Users/janduplessis/code/janduplessis883/streamlit-projects/pharm-cal/supabase_sql/003_id_based_relationships.sql) to backfill the foreign keys
6. If migrating an existing database from the combined model, run [004_split_surgeries_and_users.sql](/Users/janduplessis/code/janduplessis883/streamlit-projects/pharm-cal/supabase_sql/004_split_surgeries_and_users.sql)
7. Check `public.relationship_backfill_audit` for unresolved rows
8. Update the app code to read/write by UUIDs first, and treat name/email text as display snapshots only

## App Refactor Steps

1. Change booking actions to pass `session.id` instead of only `unique_code`
2. Change session queries to join `sessions -> pharmacists` and `sessions -> users`
3. In booking flows, store `booked_user_id` instead of writing surgery/email text directly
4. In notification flows, fetch pharmacist and surgery contact info by joined ids
5. Keep `unique_code`, `pharmacist_name`, `surgery`, and `email` during rollout so old exports and admin screens still work
6. Once the app is fully on Supabase, stop using name/email lookups as join keys

## Important Constraint

`pharmacist_name` can be backfilled only if that pharmacist already exists in `public.pharmacists`. If names in sessions do not exactly match the imported pharmacist names, those rows will appear in `public.relationship_backfill_audit` and need cleanup.

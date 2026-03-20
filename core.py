from __future__ import annotations

import os
import time
import uuid
from datetime import date, datetime
from typing import Any, Callable

import pandas as pd
import resend
import streamlit as st
from supabase import Client, create_client

COVER_REQUEST_HEADERS = [
    "uuid",
    "cover_date",
    "surgery",
    "name",
    "session",
    "reason",
    "desc",
    "submission_timestamp",
    "requester_email",
    "status",
    "decision_timestamp",
]

SESSION_SELECT = """
id,
unique_code,
date,
am_pm,
booked,
surgery,
email,
pharmacist_name,
slot_index,
pharmacist_id,
booked_user_id,
pharmacist:pharmacists!sessions_pharmacist_id_fkey(
    id,
    name,
    email
),
booked_user:users!sessions_booked_user_id_fkey(
    id,
    email,
    name,
    role,
    surgery_id,
    surgery:surgeries!users_surgery_id_fkey(
        id,
        surgery_name,
        list_size
    )
)
"""

COVER_REQUEST_SELECT = """
uuid,
cover_date,
surgery,
name,
session,
reason,
desc,
submission_timestamp,
requester_email,
status,
decision_timestamp,
requester_user_id,
requester_user:users!cover_requests_requester_user_id_fkey(
    id,
    email,
    name,
    role,
    surgery_id,
    surgery:surgeries!users_surgery_id_fkey(
        id,
        surgery_name,
        list_size
    )
)
"""


def _get_secret(*keys: str) -> str | None:
    for key in keys:
        if key in st.secrets:
            value = st.secrets[key]
            if value is not None:
                return str(value)

    supabase_section = st.secrets.get("supabase")
    if supabase_section:
        for key in keys:
            normalized_key = str(key).strip().casefold()
            candidates = {
                normalized_key,
                normalized_key.removeprefix("supabase_"),
            }
            for candidate in candidates:
                if candidate and candidate in supabase_section:
                    value = supabase_section[candidate]
                    if value is not None:
                        return str(value)
    return None


@st.cache_resource
def get_supabase_client() -> Client:
    supabase_url = _get_secret("SUPABASE_URL", "supabase_url")
    supabase_key = _get_secret(
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_KEY",
        "supabase_key",
        "SUPABASE_ANON_KEY",
    )
    if not supabase_url or not supabase_key:
        st.error("Supabase credentials are missing. Add `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` (or `SUPABASE_KEY`) to Streamlit secrets.")
        st.stop()
    return create_client(supabase_url, supabase_key)


@st.cache_resource
def get_supabase_auth_client() -> Client:
    supabase_url = _get_secret("SUPABASE_URL", "supabase_url")
    supabase_anon_key = _get_secret(
        "SUPABASE_ANON_KEY",
        "supabase_anon_key",
        "SUPABASE_KEY",
        "supabase_key",
    )
    if not supabase_url or not supabase_anon_key:
        st.error(
            "Supabase auth credentials are missing. Add `SUPABASE_URL` and `SUPABASE_ANON_KEY` to Streamlit secrets."
        )
        st.stop()
    return create_client(supabase_url, supabase_anon_key)


supabase = get_supabase_client()


def _build_authenticated_user_payload(user: Any) -> dict[str, Any]:
    user_email = str(getattr(user, "email", "") or "").strip()
    app_user = _get_user_by_email(user_email)
    if not app_user:
        raise ValueError("This Supabase account is not linked to an application user record.")

    user_metadata = getattr(user, "user_metadata", {}) or {}
    full_name = str(user_metadata.get("full_name") or user_metadata.get("name") or "").strip()
    display_name = full_name or str(app_user.get("name") or "").strip() or user_email or "there"
    return {
        "id": str(getattr(user, "id", "") or "").strip(),
        "email": user_email,
        "display_name": display_name,
        "app_user_id": str(app_user.get("id") or "").strip(),
        "app_role": str(app_user.get("role") or "").strip(),
        "surgery_id": str(app_user.get("surgery_id") or "").strip(),
        "surgery": str(app_user.get("surgery") or "").strip(),
        "name": str(app_user.get("name") or "").strip(),
    }


def sign_in_with_email_password(email: str, password: str) -> dict[str, Any]:
    auth_client = get_supabase_auth_client()
    response = auth_client.auth.sign_in_with_password(
        {
            "email": email.strip(),
            "password": password,
        }
    )
    user = getattr(response, "user", None)
    session = getattr(response, "session", None)
    if not user or not session:
        raise ValueError("Supabase did not return an authenticated session.")
    return _build_authenticated_user_payload(user)


def exchange_auth_code_for_session(code: str) -> dict[str, Any]:
    auth_client = get_supabase_auth_client()
    response = auth_client.auth.exchange_code_for_session({"auth_code": code})
    user = getattr(response, "user", None)
    session = getattr(response, "session", None)
    if not user or not session:
        raise ValueError("Could not create a Supabase session from the invite link.")
    return _build_authenticated_user_payload(user)


def set_auth_session(access_token: str, refresh_token: str) -> dict[str, Any]:
    auth_client = get_supabase_auth_client()
    response = auth_client.auth.set_session(access_token, refresh_token)
    user = getattr(response, "user", None)
    session = getattr(response, "session", None)
    if not user or not session:
        raise ValueError("Could not restore the Supabase session from the provided tokens.")
    return _build_authenticated_user_payload(user)


def update_authenticated_user_password(password: str) -> dict[str, Any]:
    auth_client = get_supabase_auth_client()
    response = auth_client.auth.update_user({"password": password})
    user = getattr(response, "user", None)
    if not user:
        raise ValueError("Supabase did not return the updated user after setting a password.")
    return _build_authenticated_user_payload(user)


def sign_out_authenticated_user() -> None:
    auth_client = get_supabase_auth_client()
    auth_client.auth.sign_out()


def _fetch_all(builder: Callable[[int, int], Any], page_size: int = 1000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        response = builder(start, start + page_size - 1).execute()
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def _normalized(value: Any) -> str:
    return str(value or "").strip().casefold()


def _session_unique_code(session_date: date | datetime, am_pm: str, slot_index: int) -> str:
    base_date = session_date.date() if isinstance(session_date, datetime) else session_date
    midnight = datetime.combine(base_date, datetime.min.time())
    return f"{int(midnight.timestamp())}-{str(am_pm).strip().lower()}-{int(slot_index)}"


def _clear_data_caches() -> None:
    get_schedule_data.clear()
    get_cover_requests_data.clear()
    get_surgeries_data.clear()
    get_users_data.clear()
    get_pharmacists_data.clear()


def _flatten_user_row(row: dict[str, Any]) -> dict[str, Any]:
    surgery = row.get("surgery") or {}
    return {
        "id": row.get("id"),
        "name": row.get("name") or "",
        "email": row.get("email") or "",
        "role": row.get("role") or "member",
        "surgery_id": row.get("surgery_id") or surgery.get("id"),
        "surgery": surgery.get("surgery_name") or "",
        "surgery_name": surgery.get("surgery_name") or "",
        "list_size": surgery.get("list_size"),
        "created_at": row.get("created_at"),
    }


def _session_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    pharmacist = row.get("pharmacist") or {}
    booked_user = _flatten_user_row(row.get("booked_user") or {})
    session_date = row.get("date")
    slot_index = int(row.get("slot_index") or 0)
    am_pm = str(row.get("am_pm") or "").strip().lower()
    return {
        "id": row.get("id"),
        "unique_code": row.get("unique_code") or _session_unique_code(pd.to_datetime(session_date).date(), am_pm, slot_index),
        "Date": session_date,
        "am_pm": am_pm,
        "booked": bool(row.get("booked", False)),
        "surgery": booked_user.get("surgery_name") or booked_user.get("surgery") or row.get("surgery") or "",
        "email": booked_user.get("email") or row.get("email") or "",
        "pharmacist_name": pharmacist.get("name") or row.get("pharmacist_name") or "None",
        "pharmacist_email": pharmacist.get("email") or "",
        "pharmacist_id": row.get("pharmacist_id") or pharmacist.get("id"),
        "slot_index": slot_index,
        "booked_user_id": row.get("booked_user_id") or booked_user.get("id"),
    }


def _request_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    requester = _flatten_user_row(row.get("requester_user") or {})
    return {
        "uuid": row.get("uuid"),
        "cover_date": row.get("cover_date"),
        "surgery": requester.get("surgery_name") or requester.get("surgery") or row.get("surgery") or "",
        "name": row.get("name") or requester.get("name") or "",
        "session": row.get("session") or "",
        "reason": row.get("reason") or "",
        "desc": row.get("desc") or "",
        "submission_timestamp": row.get("submission_timestamp"),
        "requester_email": requester.get("email") or row.get("requester_email") or "",
        "status": row.get("status") or "",
        "decision_timestamp": row.get("decision_timestamp"),
        "requester_user_id": row.get("requester_user_id") or requester.get("id"),
    }


def _fetch_users() -> list[dict[str, Any]]:
    return _fetch_all(
        lambda start, end: supabase.table("users")
        .select(
            """
            id,
            name,
            email,
            role,
            surgery_id,
            created_at,
            surgery:surgeries!users_surgery_id_fkey(
                id,
                surgery_name,
                list_size
            )
            """
        )
        .order("email")
        .range(start, end)
    )


def _fetch_surgeries() -> list[dict[str, Any]]:
    return _fetch_all(
        lambda start, end: supabase.table("surgeries")
        .select("id, surgery_name, list_size, user_ids, created_at")
        .order("surgery_name")
        .range(start, end)
    )


def _fetch_pharmacists() -> list[dict[str, Any]]:
    return _fetch_all(
        lambda start, end: supabase.table("pharmacists")
        .select("id, name, email, created_at")
        .order("name")
        .range(start, end)
    )


def _get_user_by_id(user_id: str) -> dict[str, Any] | None:
    response = (
        supabase.table("users")
        .select(
            """
            id,
            name,
            email,
            role,
            surgery_id,
            surgery:surgeries!users_surgery_id_fkey(
                id,
                surgery_name,
                list_size
            )
            """
        )
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return _flatten_user_row(rows[0]) if rows else None


def _get_user_by_email(email: str) -> dict[str, Any] | None:
    response = (
        supabase.table("users")
        .select(
            """
            id,
            name,
            email,
            role,
            surgery_id,
            surgery:surgeries!users_surgery_id_fkey(
                id,
                surgery_name,
                list_size
            )
            """
        )
        .ilike("email", email.strip())
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return _flatten_user_row(rows[0]) if rows else None


def _get_pharmacist_by_id(pharmacist_id: str) -> dict[str, Any] | None:
    response = (
        supabase.table("pharmacists")
        .select("id, name, email")
        .eq("id", pharmacist_id)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def _find_user_by_surgery_email(surgery: str, email: str) -> dict[str, Any] | None:
    target_surgery = _normalized(surgery)
    target_email = _normalized(email)
    for row in get_users_data().to_dict("records"):
        if _normalized(row.get("surgery")) == target_surgery and _normalized(row.get("email")) == target_email:
            return row
    return None


def _find_surgery_by_name(surgery_name: str) -> dict[str, Any] | None:
    target_name = _normalized(surgery_name)
    for row in _fetch_surgeries():
        if _normalized(row.get("surgery_name")) == target_name:
            return row
    return None


def _get_surgery_by_id(surgery_id: str) -> dict[str, Any] | None:
    response = (
        supabase.table("surgeries")
        .select("id, surgery_name, list_size, user_ids")
        .eq("id", surgery_id)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def _find_pharmacist_by_name(pharmacist_name: str) -> dict[str, Any] | None:
    target_name = _normalized(pharmacist_name)
    for row in _fetch_pharmacists():
        if _normalized(row.get("name")) == target_name:
            return row
    return None


def _get_session_record(slot: dict[str, Any]) -> dict[str, Any] | None:
    session_id = str(slot.get("id") or "").strip()
    if session_id:
        response = (
            supabase.table("sessions")
            .select(SESSION_SELECT)
            .eq("id", session_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return rows[0] if rows else None

    unique_code = str(slot.get("unique_code") or "").strip()
    if unique_code:
        response = (
            supabase.table("sessions")
            .select(SESSION_SELECT)
            .eq("unique_code", unique_code)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return rows[0] if rows else None
    return None


@st.cache_data(ttl=3600)
def get_schedule_data() -> pd.DataFrame:
    try:
        rows = _fetch_all(
            lambda start, end: supabase.table("sessions")
            .select(SESSION_SELECT)
            .order("date")
            .order("am_pm")
            .order("slot_index")
            .range(start, end)
        )
        records = [_session_row_to_dict(row) for row in rows]
        df = pd.DataFrame(records)
        if df.empty:
            return pd.DataFrame(
                columns=[
                    "id",
                    "unique_code",
                    "Date",
                    "am_pm",
                    "booked",
                    "surgery",
                    "email",
                    "pharmacist_name",
                    "pharmacist_email",
                    "pharmacist_id",
                    "slot_index",
                    "booked_user_id",
                ]
            )
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        return df
    except Exception as e:
        st.error(f"An error occurred while reading schedule data from Supabase: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def get_cover_requests_data() -> pd.DataFrame:
    try:
        rows = _fetch_all(
            lambda start, end: supabase.table("cover_requests")
            .select(COVER_REQUEST_SELECT)
            .order("cover_date")
            .order("submission_timestamp")
            .range(start, end)
        )
        records = [_request_row_to_dict(row) for row in rows]
        df = pd.DataFrame(records)
        if df.empty:
            return pd.DataFrame(columns=COVER_REQUEST_HEADERS + ["requester_user_id"])

        for column in ["cover_date", "submission_timestamp", "decision_timestamp"]:
            if column in df.columns:
                df[column] = pd.to_datetime(df[column], errors="coerce")
        if "requester_email" in df.columns:
            df["requester_email"] = df["requester_email"].fillna("").astype(str).str.strip()
        if "status" in df.columns:
            df["status"] = df["status"].fillna("").astype(str).str.strip()
        for column in COVER_REQUEST_HEADERS:
            if column not in df.columns:
                df[column] = None
        return df
    except Exception as e:
        st.error(f"An error occurred while reading cover requests from Supabase: {e}")
        return pd.DataFrame(columns=COVER_REQUEST_HEADERS + ["requester_user_id"])


def add_cover_request_data(
    cover_date: date,
    requester_user_id: str,
    requester_name: str,
    session: str,
    reason: str,
    desc: str,
) -> None:
    try:
        requester = _get_user_by_id(requester_user_id)
        if not requester:
            st.error("Could not find the selected surgery contact.")
            return

        payload = {
            "uuid": str(uuid.uuid4()),
            "cover_date": cover_date.isoformat(),
            "surgery": requester["surgery"],
            "name": requester_name.strip(),
            "session": session,
            "reason": reason.strip(),
            "desc": desc.strip(),
            "requester_email": requester["email"],
            "status": "Pending",
            "requester_user_id": requester["id"],
        }
        supabase.table("cover_requests").insert(payload).execute()
        st.success("Cover request submitted successfully!")
        get_cover_requests_data.clear()
    except Exception as e:
        st.error(f"An error occurred while adding the cover request: {e}")


def delete_cover_request(request_uuid: str, requester_user_id: str) -> bool:
    try:
        request_row = _get_cover_request_by_uuid(request_uuid)
        if not request_row:
            st.error("Could not find the cover request to delete.")
            return False

        owner_id = str(request_row.get("requester_user_id") or "").strip()
        if not owner_id or owner_id != str(requester_user_id).strip():
            st.error("You can only delete requests that you created.")
            return False

        current_status = _normalized(request_row.get("status"))
        if current_status in {"approved", "rejected"}:
            st.error("Only pending requests can be deleted.")
            return False

        supabase.table("cover_requests").delete().eq("uuid", request_uuid).execute()
        get_cover_requests_data.clear()
        st.success("Future cover request deleted.")
        return True
    except Exception as e:
        st.error(f"An error occurred while deleting the cover request: {e}")
        return False


def _get_cover_request_by_uuid(request_uuid: str) -> dict[str, Any] | None:
    response = (
        supabase.table("cover_requests")
        .select(COVER_REQUEST_SELECT)
        .eq("uuid", request_uuid)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def accept_cover_request(request_uuid: str) -> bool:
    try:
        request_row = _get_cover_request_by_uuid(request_uuid)
        if not request_row:
            st.error("Could not find the cover request to accept.")
            return False

        current_status = _normalized(request_row.get("status"))
        if current_status == "approved":
            st.info("This request has already been approved.")
            return False
        if current_status == "rejected":
            st.info("This request has already been rejected.")
            return False

        supabase.table("cover_requests").update(
            {
                "status": "Approved",
                "decision_timestamp": datetime.utcnow().isoformat(),
            }
        ).eq("uuid", request_uuid).execute()

        get_cover_requests_data.clear()
        st.success("Request marked as approved.")
        return True
    except Exception as e:
        st.error(f"An error occurred while approving the cover request: {e}")
        return False


def reject_cover_request(request_uuid: str, *, send_email: bool = True) -> bool:
    try:
        request_row = _get_cover_request_by_uuid(request_uuid)
        if not request_row:
            st.error("Could not find the cover request to reject.")
            return False

        current_status = _normalized(request_row.get("status"))
        if current_status == "rejected":
            st.info("This request has already been rejected.")
            return False

        requester = request_row.get("requester_user") or {}
        requester_email = str(requester.get("email") or request_row.get("requester_email") or "").strip()
        requester_name = str(request_row.get("name") or requester.get("name") or "").strip() or "there"
        surgery_name = str(requester.get("surgery") or request_row.get("surgery") or "").strip() or "your practice"
        cover_date = pd.to_datetime(request_row.get("cover_date"), errors="coerce")
        cover_date_str = cover_date.strftime("%A, %d %B %Y") if pd.notna(cover_date) else "the requested date"

        if send_email:
            if not requester_email:
                st.error("This request does not include a requester email, so a rejection email cannot be sent.")
                return False

            rejection_html = f"""
            <p>Dear {requester_name},</p>
            <p>Thank you for your request for pharmacy support for <b>{surgery_name}</b> on <b>{cover_date_str}</b>.</p>
            <p>We are sorry to let you know that we have been unable to accommodate this request due to current scheduling constraints. Session requests are prioritised according to operational need and availability.</p>
            <p>We appreciate your understanding and apologise that we could not support this request on this occasion.</p>
            <p>Kind regards,<br>Pharma-Cal automated notifications<br>Brompton Health PCN</p>
            """
            if not send_resend_email(
                requester_email,
                f"Parmacist Cover Request Rejected - {cover_date_str}",
                rejection_html,
            ):
                return False

        supabase.table("cover_requests").update(
            {
                "status": "Rejected",
                "decision_timestamp": datetime.utcnow().isoformat(),
            }
        ).eq("uuid", request_uuid).execute()

        get_cover_requests_data.clear()
        if send_email:
            st.success(f"Rejection email sent to {requester_name} and request marked as rejected.")
        else:
            st.success("Request marked as rejected.")
        return True
    except Exception as e:
        st.error(f"An error occurred while rejecting the cover request: {e}")
        return False


@st.cache_data(ttl=3600)
def get_surgeries_data() -> pd.DataFrame:
    try:
        surgeries_rows = _fetch_surgeries()
        users_df = get_users_data()
        records: list[dict[str, Any]] = []
        for row in surgeries_rows:
            surgery_id = row.get("id")
            related_users = users_df[users_df["surgery_id"] == surgery_id] if not users_df.empty else pd.DataFrame()
            primary_user = related_users.sort_values(["email", "name"], kind="stable").iloc[0] if not related_users.empty else None
            records.append(
                {
                    "id": surgery_id,
                    "surgery": row.get("surgery_name") or "",
                    "surgery_name": row.get("surgery_name") or "",
                    "list_size": row.get("list_size"),
                    "user_ids": row.get("user_ids") or [],
                    "user_count": int(len(related_users)),
                    "primary_user_id": str(primary_user.get("id") or "") if primary_user is not None else "",
                    "primary_user_name": str(primary_user.get("name") or "") if primary_user is not None else "",
                    "primary_user_email": str(primary_user.get("email") or "") if primary_user is not None else "",
                }
            )
        df = pd.DataFrame(records)
        if df.empty:
            return pd.DataFrame(
                columns=[
                    "id",
                    "surgery",
                    "surgery_name",
                    "list_size",
                    "user_ids",
                    "user_count",
                    "primary_user_id",
                    "primary_user_name",
                    "primary_user_email",
                ]
            )
        if "list_size" in df.columns:
            df["list_size"] = pd.to_numeric(df["list_size"], errors="coerce").fillna(0)
        return df
    except Exception as e:
        st.error(f"An error occurred while reading surgeries data from Supabase: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def get_users_data() -> pd.DataFrame:
    try:
        rows = [_flatten_user_row(row) for row in _fetch_users()]
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(columns=["id", "name", "email", "role", "surgery_id", "surgery", "surgery_name", "list_size"])
        if "list_size" in df.columns:
            df["list_size"] = pd.to_numeric(df["list_size"], errors="coerce").fillna(0)
        return df
    except Exception as e:
        st.error(f"An error occurred while reading users data from Supabase: {e}")
        return pd.DataFrame()


def add_surgery_data(surgery_name: str, list_size: int) -> None:
    try:
        existing = _find_surgery_by_name(surgery_name)
        if existing:
            st.info(f"Surgery '{surgery_name}' already exists.")
            return

        supabase.table("surgeries").insert(
            {
                "surgery_name": surgery_name.strip(),
                "list_size": int(list_size),
            }
        ).execute()
        st.success(f"Surgery '{surgery_name}' added successfully!")
        _clear_data_caches()
    except Exception as e:
        st.error(f"An error occurred while adding surgery data: {e}")


def delete_surgery_data(surgery_id: str) -> None:
    try:
        existing = None
        for row in _fetch_surgeries():
            if str(row.get("id") or "").strip() == str(surgery_id).strip():
                existing = row
                break
        if not existing:
            st.error("Surgery not found.")
            return

        supabase.table("surgeries").delete().eq("id", existing["id"]).execute()
        st.success(f"Surgery '{existing.get('surgery_name', 'Unknown')}' deleted successfully!")
        _clear_data_caches()
    except Exception as e:
        st.error(f"An error occurred while deleting surgery data: {e}")


def add_user_data(name: str, email: str, surgery_id: str, role: str) -> None:
    try:
        clean_email = email.strip()
        existing_users = get_users_data()
        if not existing_users.empty and existing_users["email"].fillna("").astype(str).str.strip().str.casefold().eq(clean_email.casefold()).any():
            st.info(f"User with email '{clean_email}' already exists.")
            return

        supabase.table("users").insert(
            {
                "name": name.strip(),
                "email": clean_email,
                "surgery_id": surgery_id,
                "role": role.strip() or "member",
            }
        ).execute()
        st.success(f"User '{name}' added successfully!")
        _clear_data_caches()
    except Exception as e:
        st.error(f"An error occurred while adding user data: {e}")


def delete_user_data(user_id: str) -> None:
    try:
        supabase.table("users").delete().eq("id", user_id).execute()
        st.success("User deleted successfully!")
        _clear_data_caches()
    except Exception as e:
        st.error(f"An error occurred while deleting user data: {e}")


@st.cache_data(ttl=1200)
def get_pharmacists_data() -> pd.DataFrame:
    try:
        rows = _fetch_pharmacists()
        df = pd.DataFrame(
            [
                {
                    "id": row.get("id"),
                    "Name": row.get("name") or "",
                    "Email": row.get("email") or "",
                    "name": row.get("name") or "",
                    "email": row.get("email") or "",
                }
                for row in rows
            ]
        )
        if df.empty:
            return pd.DataFrame(columns=["id", "Name", "Email", "name", "email"])
        return df
    except Exception as e:
        st.error(f"An error occurred while reading pharmacists data from Supabase: {e}")
        return pd.DataFrame()


def add_pharmacist_data(pharmacist_name: str, pharmacist_email: str) -> None:
    try:
        existing = _find_pharmacist_by_name(pharmacist_name)
        if existing:
            st.info(f"Pharmacist '{pharmacist_name}' already exists.")
            return

        supabase.table("pharmacists").insert(
            {
                "name": pharmacist_name.strip(),
                "email": pharmacist_email.strip(),
            }
        ).execute()
        st.success(f"Pharmacist '{pharmacist_name}' added successfully!")
        get_pharmacists_data.clear()
    except Exception as e:
        st.error(f"An error occurred while adding pharmacist data: {e}")


def delete_pharmacist_data(pharmacist_name: str, email_address: str) -> None:
    try:
        pharmacist = None
        target_email = _normalized(email_address)
        for row in _fetch_pharmacists():
            if _normalized(row.get("name")) == _normalized(pharmacist_name) and _normalized(row.get("email")) == target_email:
                pharmacist = row
                break
        if not pharmacist:
            st.error(f"Pharmacist '{pharmacist_name}' not found.")
            return

        supabase.table("pharmacists").delete().eq("id", pharmacist["id"]).execute()
        st.success(f"Pharmacist '{pharmacist_name}' deleted successfully!")
        _clear_data_caches()
    except Exception as e:
        st.error(f"An error occurred while deleting pharmacist data: {e}")


def generate_ics_file(pharmacist_name: str, start_time: datetime, end_time: datetime, location: str) -> str:
    ics_content = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Pharma-Cal//EN
BEGIN:VEVENT
SUMMARY:Pharmacist Booking - {pharmacist_name}
DTSTART:{start_time.strftime('%Y%m%dT%H%M%S')}
DTEND:{end_time.strftime('%Y%m%dT%H%M%S')}
LOCATION:{location} - Remote Session
DESCRIPTION:Pharmacist: {pharmacist_name}
END:VEVENT
END:VCALENDAR"""

    file_path = f"pharmacist_booking_{start_time.strftime('%Y%m%d')}.ics"
    with open(file_path, "w", encoding="utf-8") as handle:
        handle.write(ics_content)
    return file_path


def send_resend_email(to_email: str, subject: str, html_content: str, attachment_path: str | None = None) -> bool:
    resend.api_key = _get_secret("RESEND_API_KEY")

    if attachment_path:
        with open(attachment_path, "rb") as handle:
            attachment_content = handle.read()
        attachment = {"filename": os.path.basename(attachment_path), "content": list(attachment_content)}
    else:
        attachment = None

    params: dict[str, Any] = {
        "from": "Pharma-cal <hello@attribut.me>",
        "to": to_email,
        "subject": subject,
        "html": html_content,
        "attachments": [attachment] if attachment else [],
    }

    try:
        resend.Emails.send(params)
        return True
    except Exception as e:
        st.error(f"Error sending email: {e}")
        return False


def cancel_booking(slot: dict[str, Any]) -> None:
    try:
        session_row = _get_session_record(slot)
        if not session_row:
            st.error("Could not find the slot to cancel.")
            return

        session_data = _session_row_to_dict(session_row)
        surgery_name = session_data.get("surgery", "")
        surgery_email = session_data.get("email", "")
        pharmacist_name = session_data.get("pharmacist_name", "Pharmacist")
        pharmacist_email = session_data.get("pharmacist_email", "")
        booking_date = pd.to_datetime(session_data["Date"]).strftime("%A, %d %B %Y")
        booking_time = "09:00 - 12:45" if session_data["am_pm"] == "am" else "13:15 - 17:00"

        with st.spinner("Cancelling booking and sending notifications..."):
            supabase.table("sessions").update(
                {
                    "booked": False,
                    "booked_user_id": None,
                    "surgery": None,
                    "email": None,
                }
            ).eq("id", session_data["id"]).execute()

            if surgery_email:
                surgery_html = f"""
                <h2>Booking Cancellation Notice</h2>
                <p>The booking for <b>{pharmacist_name}</b> on <b>{booking_date}</b> at <b>{booking_time}</b> has been cancelled.</p>
                <p>This slot is now available again.</p>
                """
                send_resend_email(
                    surgery_email,
                    f"Booking Cancellation - {pharmacist_name} on {booking_date}",
                    surgery_html,
                )

            if pharmacist_email:
                pharmacist_html = f"""
                <h2>Booking Cancellation Notice</h2>
                <p>Your session at <b>{surgery_name}</b> on <b>{booking_date}</b> at <b>{booking_time}</b> has been cancelled.</p>
                <p>This slot is now available again.</p>
                """
                send_resend_email(
                    pharmacist_email,
                    f"Booking Cancellation - {surgery_name} on {booking_date}",
                    pharmacist_html,
                )

        get_schedule_data.clear()
        st.success("Booking cancelled successfully and notifications sent!")
        time.sleep(1)
        st.rerun()
    except Exception as e:
        st.error(f"An error occurred while cancelling the booking: {e}")


def update_booking(slot: dict[str, Any], surgery_id: str, booked_user_id: str | None = None) -> None:
    try:
        session_row = _get_session_record(slot)
        if not session_row:
            st.error("Could not find the slot to book.")
            return

        session_data = _session_row_to_dict(session_row)
        surgery = _get_surgery_by_id(surgery_id)
        if not surgery:
            st.error("Could not find the selected surgery.")
            return

        user = _get_user_by_id(booked_user_id) if booked_user_id else None
        if booked_user_id and not user:
            st.error("Could not find the selected surgery contact.")
            return

        pharmacist_name = session_data.get("pharmacist_name", "Pharmacist")
        pharmacist_email = session_data.get("pharmacist_email", "")
        if not pharmacist_email and session_data.get("pharmacist_id"):
            pharmacist = _get_pharmacist_by_id(str(session_data["pharmacist_id"]))
            pharmacist_email = str((pharmacist or {}).get("email") or "").strip()

        if not pharmacist_email:
            st.error(f"Could not find email for pharmacist {pharmacist_name}")
            return

        with st.spinner("Updating booking..."):
            surgery_name = str(surgery.get("surgery_name") or "").strip()
            surgery_email = str((user or {}).get("email") or "").strip() or None
            supabase.table("sessions").update(
                {
                    "booked": True,
                    "booked_user_id": booked_user_id or None,
                    "surgery": surgery_name,
                    "email": surgery_email,
                }
            ).eq("id", session_data["id"]).execute()

            booking_date = pd.to_datetime(session_data["Date"])
            start_time = booking_date.replace(hour=9 if session_data["am_pm"] == "am" else 13)
            end_time = booking_date.replace(hour=12, minute=45) if session_data["am_pm"] == "am" else booking_date.replace(hour=17)
            ics_file = generate_ics_file(pharmacist_name, start_time, end_time, surgery_name)

            if surgery_email:
                surgery_html = f"""
                <h2>Pharmacist Booking Confirmation</h2>
                <p>You have booked <b>{pharmacist_name}</b> for:</p>
                <p><strong>Date:</strong> {booking_date.strftime('%A, %d %B %Y')}</p>
                <p><strong>Time:</strong> {'09:00 - 12:45' if session_data['am_pm'] == 'am' else '13:15 - 17:00'}</p>
                <p>Please find attached the calendar invite.</p>
                """
                send_resend_email(
                    surgery_email,
                    f"Pharmacist Booking Confirmation - {booking_date.strftime('%d/%m/%Y')}",
                    surgery_html,
                    ics_file,
                )

            pharmacist_html = f"""
            <h2>New Surgery Booking Notification</h2>
            <p>You have been booked for a session at:</p>
            <p><strong>Surgery:</strong> {surgery_name}</p>
            <p><strong>Date:</strong> {booking_date.strftime('%A, %d %B %Y')}</p>
            <p><strong>Time:</strong> {'09:00 - 12:45' if session_data['am_pm'] == 'am' else '13:15 - 17:00'}</p>
            <p><strong>Surgery Email:</strong> {surgery_email or 'No contact email configured'}</p>
            <p>Please find attached the calendar invite.</p>
            """
            send_resend_email(
                pharmacist_email,
                f"New Booking - {surgery_name} on {booking_date.strftime('%d/%m/%Y')}",
                pharmacist_html,
                ics_file,
            )

        get_schedule_data.clear()
    except Exception as e:
        st.error(f"An error occurred while updating the booking in Supabase: {e}")


def save_availability_change(
    slot_date: date,
    shift_type: str,
    slot_index: int,
    slot_info: dict[str, Any],
    pharmacist_name: str,
) -> None:
    try:
        session_id = str(slot_info.get("id") or "").strip()
        target_name = str(pharmacist_name or "").strip()

        if not target_name or target_name == "None":
            if session_id:
                supabase.table("sessions").delete().eq("id", session_id).execute()
            get_schedule_data.clear()
            return

        pharmacist = _find_pharmacist_by_name(target_name)
        if not pharmacist:
            raise ValueError(f"Could not find pharmacist '{target_name}'.")

        payload = {
            "date": slot_date.isoformat(),
            "am_pm": shift_type,
            "booked": False,
            "pharmacist_name": pharmacist["name"],
            "pharmacist_id": pharmacist["id"],
            "slot_index": int(slot_index),
        }

        if session_id:
            supabase.table("sessions").update(payload).eq("id", session_id).execute()
        else:
            payload["unique_code"] = slot_info.get("unique_code") or _session_unique_code(slot_date, shift_type, slot_index)
            supabase.table("sessions").insert(payload).execute()

        get_schedule_data.clear()
    except Exception as e:
        raise RuntimeError(str(e)) from e

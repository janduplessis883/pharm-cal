import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from html import escape
import time

# Local Imports
from plots import display_plot, display_normalized_sessions_plot

from core import (
    get_schedule_data,
    get_cover_requests_data,
    add_cover_request_data,
    get_surgeries_data,
    add_surgery_data,
    delete_surgery_data,
    get_users_data,
    add_user_data,
    delete_user_data,
    get_pharmacists_data,
    add_pharmacist_data,
    delete_pharmacist_data,
    cancel_booking,
    update_booking,
    accept_cover_request,
    reject_cover_request,
    delete_cover_request,
    save_availability_change,
    sign_in_with_email_password,
    sign_out_authenticated_user,
)
st.set_page_config(
    page_title="Pharm-Cal [Brompton Health PCN]",
    layout="centered",
    page_icon=":material/pill:",
    initial_sidebar_state="collapsed",
)


def _clean_string_values(df: pd.DataFrame, column: str) -> list[str]:
    if df.empty or column not in df.columns:
        return []

    cleaned = df[column].dropna().astype(str).str.strip()
    return sorted(value for value in cleaned.unique().tolist() if value)


def _normalize_column_key(value: str) -> str:
    return "".join(char for char in str(value).strip().casefold() if char.isalnum())


def _normalize_text(value: object) -> str:
    return str(value or "").strip().casefold()


def _get_matching_column(df: pd.DataFrame, aliases: list[str]) -> str | None:
    if df.empty:
        return None

    alias_keys = {_normalize_column_key(alias) for alias in aliases}
    for column in df.columns:
        if _normalize_column_key(column) in alias_keys:
            return str(column)
    return None


def _get_surgery_contact_defaults(surgeries_df: pd.DataFrame, selected_surgery: str) -> tuple[str, str]:
    if surgeries_df.empty or not selected_surgery:
        return "", ""

    surgery_column = _get_matching_column(surgeries_df, ["surgery"])
    if not surgery_column:
        return "", ""

    normalized_surgery = str(selected_surgery).strip().casefold()
    selected_rows = surgeries_df[
        surgeries_df[surgery_column].fillna("").astype(str).str.strip().str.casefold() == normalized_surgery
    ]
    if selected_rows.empty:
        return "", ""

    selected_row = selected_rows.iloc[0]
    name_column = _get_matching_column(
        surgeries_df,
        [
            "name",
            "requester_name",
            "requestor_name",
            "requested_by",
            "requester",
            "contact_name",
            "contact",
            "username",
            "user_name",
        ],
    )
    email_column = _get_matching_column(
        surgeries_df,
        [
            "email",
            "requester_email",
            "requestor_email",
            "contact_email",
            "email_address",
        ],
    )

    prefilled_name = str(selected_row.get(name_column, "") or "").strip() if name_column else ""
    prefilled_email = str(selected_row.get(email_column, "") or "").strip() if email_column else ""
    return prefilled_name, prefilled_email


def _slot_identity(slot: dict[str, object] | pd.Series) -> str:
    slot_id = str(slot.get("id", "") or "").strip()
    if slot_id:
        return slot_id
    return str(slot.get("unique_code", "") or "").strip()


def _build_surgery_options(surgeries_df: pd.DataFrame) -> list[dict[str, str]]:
    required_columns = {"id", "surgery", "email"}
    if surgeries_df.empty or not required_columns.issubset(surgeries_df.columns):
        return []

    normalized_counts = (
        surgeries_df["surgery"].fillna("").astype(str).str.strip().str.casefold().value_counts()
    )
    options: list[dict[str, str]] = []
    for _, row in surgeries_df.iterrows():
        surgery = str(row.get("surgery", "") or "").strip()
        email = str(row.get("email", "") or "").strip()
        user_id = str(row.get("id", "") or "").strip()
        contact_name = str(row.get("name", "") or "").strip()
        if not surgery or not email or not user_id:
            continue
        duplicate_count = normalized_counts.get(surgery.casefold(), 0)
        label = f"{surgery} ({email})" if duplicate_count > 1 else surgery
        options.append(
            {
                "id": user_id,
                "label": label,
                "surgery": surgery,
                "email": email,
                "name": contact_name,
            }
        )

    return sorted(options, key=lambda option: (option["surgery"].casefold(), option["email"].casefold()))


def _build_user_options(users_df: pd.DataFrame) -> list[dict[str, str]]:
    required_columns = {"id", "surgery", "email", "name"}
    if users_df.empty or not required_columns.issubset(users_df.columns):
        return []

    normalized_counts = (
        users_df["surgery"].fillna("").astype(str).str.strip().str.casefold().value_counts()
    )
    options: list[dict[str, str]] = []
    for _, row in users_df.iterrows():
        surgery = str(row.get("surgery", "") or "").strip()
        email = str(row.get("email", "") or "").strip()
        user_id = str(row.get("id", "") or "").strip()
        contact_name = str(row.get("name", "") or "").strip()
        role = str(row.get("role", "") or "").strip()
        if not surgery or not email or not user_id:
            continue
        duplicate_count = normalized_counts.get(surgery.casefold(), 0)
        if duplicate_count > 1:
            label = f"{surgery} ({contact_name or email})"
        else:
            label = surgery
        options.append(
            {
                "id": user_id,
                "label": label,
                "surgery": surgery,
                "email": email,
                "name": contact_name,
                "role": role,
            }
        )

    return sorted(options, key=lambda option: (option["surgery"].casefold(), option["email"].casefold(), option["name"].casefold()))


USER_ROLE_OPTIONS = [
    "normal",
    "superuser",
]

FULL_ACCESS_ROLES = {
    "superuser",
}


def _authenticated_user() -> dict[str, str]:
    return st.session_state.get("auth_user", {}) or {}


def _current_user_account_type() -> str:
    return str(_authenticated_user().get("account_type", "user") or "user").strip().casefold()


def _current_user_is_pharmacist() -> bool:
    return _current_user_account_type() == "pharmacist"


def _current_user_can_access_all_clinics() -> bool:
    return str(_authenticated_user().get("app_role", "") or "").strip() in FULL_ACCESS_ROLES


def _current_user_surgery_id() -> str:
    return str(_authenticated_user().get("surgery_id", "") or "").strip()


def _current_user_surgery_name() -> str:
    return str(_authenticated_user().get("surgery", "") or "").strip()


def _current_user_app_user_id() -> str:
    return str(_authenticated_user().get("app_user_id", "") or "").strip()


def _can_toggle_surgery_calendar_view() -> bool:
    return (
        not _current_user_is_pharmacist()
        and bool(_current_user_surgery_id())
        and bool(_current_user_surgery_name())
    )


def _coerce_booked_flags(values: pd.Series) -> pd.Series:
    if values.empty:
        return pd.Series(dtype="bool")

    if pd.api.types.is_bool_dtype(values):
        return values.fillna(False)

    return values.fillna(False).apply(lambda value: str(value).strip().casefold() == "true")


def _is_booked_value(value: object) -> bool:
    return str(value).strip().casefold() == "true"


def _collapse_schedule_slots_for_display(
    schedule_df: pd.DataFrame,
    preferred_surgery_name: str = "",
) -> pd.DataFrame:
    required_columns = {"Date", "am_pm", "slot_index", "booked", "surgery"}
    if schedule_df.empty or not required_columns.issubset(schedule_df.columns):
        return schedule_df.copy()

    collapsed = schedule_df.copy()
    collapsed["_booked_flag"] = _coerce_booked_flags(collapsed["booked"])
    normalized_preferred_surgery = _normalize_text(preferred_surgery_name)
    if normalized_preferred_surgery:
        collapsed["_preferred_booking_flag"] = (
            collapsed["_booked_flag"]
            & (
                collapsed["surgery"].fillna("").astype(str).str.strip().str.casefold()
                == normalized_preferred_surgery
            )
        )
    else:
        collapsed["_preferred_booking_flag"] = False

    collapsed = collapsed.sort_values(
        ["Date", "am_pm", "slot_index", "_preferred_booking_flag", "_booked_flag"],
        ascending=[True, True, True, False, False],
        kind="stable",
    )
    collapsed = collapsed.drop_duplicates(subset=["Date", "am_pm", "slot_index"], keep="first")
    return collapsed.drop(columns=["_booked_flag", "_preferred_booking_flag"], errors="ignore")


def _filter_schedule_for_surgery_view(
    schedule_df: pd.DataFrame,
    surgery_name: str,
) -> tuple[pd.DataFrame, int]:
    if schedule_df.empty or "booked" not in schedule_df.columns or "surgery" not in schedule_df.columns:
        return schedule_df.copy(), 0

    normalized_surgery_name = _normalize_text(surgery_name)
    if not normalized_surgery_name:
        return schedule_df.copy(), 0

    booked_mask = _coerce_booked_flags(schedule_df["booked"])
    owned_booking_mask = schedule_df["surgery"].fillna("").astype(str).str.strip().str.casefold() == normalized_surgery_name
    visible_mask = (~booked_mask) | owned_booking_mask
    hidden_count = int((booked_mask & ~owned_booking_mask).sum())
    return schedule_df[visible_mask].copy(), hidden_count


def _normalize_schedule_data(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    normalized = df.copy()
    if "Date" not in normalized.columns:
        return pd.DataFrame()

    normalized["Date"] = pd.to_datetime(normalized["Date"], errors="coerce")
    normalized = normalized.dropna(subset=["Date"]).copy()

    if normalized.empty:
        return normalized

    if "am_pm" not in normalized.columns:
        normalized["am_pm"] = ""
    normalized["am_pm"] = normalized["am_pm"].fillna("").astype(str).str.strip().str.lower()

    fallback_slot_index = normalized.groupby(
        [normalized["Date"].dt.date, normalized["am_pm"]]
    ).cumcount()

    slot_index = pd.Series(float("nan"), index=normalized.index, dtype="float64")
    if "slot_index" in normalized.columns:
        slot_index = pd.to_numeric(normalized["slot_index"], errors="coerce")
    elif "pharm" in normalized.columns:
        slot_index = pd.to_numeric(normalized["pharm"], errors="coerce") - 1

    normalized["slot_index"] = slot_index.where(slot_index.notna(), fallback_slot_index).astype(int)

    pharmacist_names = pd.Series("", index=normalized.index, dtype="object")
    if "pharmacist_name" in normalized.columns:
        pharmacist_names = normalized["pharmacist_name"].fillna("").astype(str)
    elif "pharm" in normalized.columns:
        pharmacist_names = normalized["pharm"].fillna("").astype(str)

    pharmacist_names = pharmacist_names.str.strip()
    normalized["pharmacist_name"] = pharmacist_names.where(pharmacist_names != "", "None")

    return normalized


def _apply_app_theme() -> None:
    st.markdown(
        """
        <style>
        :root {
            --app-ink: #1f2937;
            --app-muted: #64748b;
            --app-border: #dbe4ee;
            --app-surface: #f8fafc;
            --app-accent: #0f766e;
            --app-accent-soft: #e6fffb;
            --status-red: #ae4f4d;
            --status-orange: #ebbd5a;
            --status-yellow: #e4af6c;
            --status-green: #aec867;
            --status-blue: #659bb7;
            --status-violet: #db2777;
            --status-gray: #a3a3a3;
            --hero-navy: #102a56;
            --hero-blue: #0077b6;
            --hero-sky: #00b4d8;
            --hero-orange: #ff8500;
            --hero-amber: #ff9e00;
            --hero-sun: #ff9100;
        }

        .stApp .block-container {
            padding-top: 3rem;
            padding-bottom: 2.75rem;
        }

        [data-testid="stSidebar"] .block-container {
            padding-top: 1rem;
            padding-bottom: 1.75rem;
        }

        [data-testid="stForm"] {
            border: 1px solid var(--app-border);
            background: #ffffff;
            border-radius: 16px;
            padding: 0.85rem 0.9rem 1rem;
        }

        .stButton > button,
        .stForm button {
            border-radius: 12px;
            font-weight: 600;
        }

        .app-section {
            margin: 0.35rem 0 0.9rem;
        }

        .app-section-kicker {
            color: var(--app-accent);
            font-size: 0.74rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            margin-bottom: 0.15rem;
        }

        .app-section-title {
            color: var(--app-ink);
            font-size: 1.15rem;
            font-weight: 700;
            line-height: 1.2;
        }

        .app-section-copy {
            color: var(--app-muted);
            font-size: 0.92rem;
            margin-top: 0.2rem;
        }

        .app-hero {
            background:
                radial-gradient(circle at top right, rgba(255, 158, 0, 0.18) 0%, rgba(255, 158, 0, 0) 38%),
                radial-gradient(circle at left center, rgba(0, 180, 216, 0.16) 0%, rgba(0, 180, 216, 0) 36%),
                linear-gradient(140deg, rgba(255, 255, 255, 0.97) 0%, rgba(242, 248, 252, 0.98) 100%);
            border: 1px solid rgba(16, 42, 86, 0.16);
            border-radius: 18px;
            color: var(--hero-navy);
            margin: 0.25rem 0 1.25rem;
            overflow: hidden;
            padding: 1rem 1.1rem;
            position: relative;
            transform: translateY(0);
            transition: transform 180ms ease, box-shadow 180ms ease, border-color 180ms ease;
            box-shadow:
                0 16px 32px rgba(16, 42, 86, 0.10),
                inset 0 1px 0 rgba(255, 255, 255, 0.72);
        }

        .app-hero::before {
            background: linear-gradient(90deg, var(--hero-orange) 0%, var(--hero-amber) 22%, var(--hero-sky) 58%, var(--hero-blue) 100%);
            content: "";
            height: 5px;
            left: 0;
            opacity: 0.98;
            position: absolute;
            right: 0;
            top: 0;
        }

        .app-hero::after {
            background:
                linear-gradient(120deg, rgba(255, 255, 255, 0.22) 0%, rgba(255, 255, 255, 0) 44%),
                linear-gradient(135deg, rgba(2, 48, 113, 0.09) 0%, rgba(2, 48, 113, 0) 58%);
            content: "";
            inset: 0;
            pointer-events: none;
            position: absolute;
        }

        .app-hero > * {
            position: relative;
            z-index: 1;
        }

        .app-hero--primary {
            background:
                radial-gradient(circle at top right, rgba(255, 158, 0, 0.36) 0%, rgba(255, 158, 0, 0) 42%),
                radial-gradient(circle at left center, rgba(0, 180, 216, 0.22) 0%, rgba(0, 180, 216, 0) 36%),
                linear-gradient(135deg, #fff4e8 0%, #fffaf2 30%, #eef9fd 76%, #edf4ff 100%);
            border-color: rgba(255, 133, 0, 0.28);
            box-shadow:
                0 18px 36px rgba(255, 145, 0, 0.10),
                0 14px 32px rgba(2, 48, 94, 0.10),
                inset 0 1px 0 rgba(255, 255, 255, 0.72);
        }

        .app-hero--primary .app-hero-kicker {
            color: #136f84;
        }

        .app-hero--primary .app-hero-title {
            color: #183153;
        }

        .app-hero--primary .app-hero-copy {
            color: #556b84;
        }

        .app-hero--welcome {
            background:
                radial-gradient(circle at top left, rgba(0, 180, 216, 0.28) 0%, rgba(0, 180, 216, 0) 40%),
                radial-gradient(circle at bottom right, rgba(255, 145, 0, 0.20) 0%, rgba(255, 145, 0, 0) 34%),
                linear-gradient(135deg, #eff9ff 0%, #f6fbff 38%, #fff7ef 100%);
            border-color: rgba(0, 119, 182, 0.24);
            box-shadow:
                0 18px 34px rgba(0, 119, 182, 0.12),
                0 10px 24px rgba(16, 42, 86, 0.08),
                inset 0 1px 0 rgba(255, 255, 255, 0.74);
        }

        .app-hero--welcome::before {
            background: linear-gradient(90deg, #00b4d8 0%, #0096c7 34%, #0077b6 70%, #ff9100 100%);
        }

        .app-hero--welcome .app-hero-kicker {
            color: #0a7897;
        }

        .app-hero--welcome .app-hero-title {
            color: #1a3558;
        }

        .app-hero--welcome .app-hero-copy {
            color: #607792;
        }

        .app-hero-kicker {
            font-size: 0.74rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        .app-hero-title {
            font-size: 1.65rem;
            font-weight: 800;
            line-height: 1.15;
            margin-top: 0.25rem;
            color: var(--app-ink);
        }

        .app-hero-copy {
            color: var(--app-muted);
            font-size: 0.95rem;
            margin-top: 0.3rem;
        }

        .app-band {
            background:
                radial-gradient(circle at top right, rgba(255, 158, 0, 0.18) 0%, rgba(255, 158, 0, 0) 38%),
                radial-gradient(circle at left center, rgba(0, 180, 216, 0.18) 0%, rgba(0, 180, 216, 0) 34%),
                linear-gradient(135deg, #fff8ee 0%, #f7fbff 56%, #f1f7ff 100%);
            border: 1px solid rgba(0, 119, 182, 0.18);
            border-radius: 16px;
            box-shadow:
                0 14px 28px rgba(16, 42, 86, 0.10),
                inset 0 1px 0 rgba(255, 255, 255, 0.74);
            margin: 1.15rem 0 1rem;
            overflow: hidden;
            padding: 0.85rem 1rem 0.9rem;
            position: relative;
            transform: translateY(0);
            transition: transform 180ms ease, box-shadow 180ms ease, border-color 180ms ease;
        }

        .app-band::before {
            background: linear-gradient(90deg, var(--hero-orange) 0%, var(--hero-amber) 28%, var(--hero-sky) 66%, var(--hero-blue) 100%);
            content: "";
            height: 4px;
            left: 0;
            opacity: 0.98;
            position: absolute;
            right: 0;
            top: 0;
        }

        .app-band > * {
            position: relative;
            z-index: 1;
        }

        .app-band-kicker {
            color: #0f7a95;
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        .app-band-title {
            color: #193457;
            font-size: 1.3rem;
            font-weight: 800;
            line-height: 1.15;
            margin-top: 0.18rem;
        }

        .app-band-copy {
            color: #617994;
            font-size: 0.94rem;
            margin-top: 0.22rem;
        }

        .slot-card {
            background: linear-gradient(180deg, #f7f9fb 0%, #f1f5f7 100%);
            border: 1px solid #4f748b;
            border-radius: 16px;
            box-shadow: 0 10px 24px rgba(79, 116, 139, 0.14);
            display: flex;
            flex-direction: column;
            justify-content: flex-start;
            margin-bottom: 0.65rem;
            min-height: 7.1rem;
            padding: 0.65rem 0.85rem 0.72rem;
            transform: translateY(0);
            transition: transform 180ms ease, box-shadow 180ms ease, border-color 180ms ease;
        }

        .slot-card--available {
            background: #f7f1ee;
            border-color: #d8662a;
        }

        .slot-card-placeholder {
            margin-bottom: 0.65rem;
            min-height: 7.1rem;
        }

        .slot-card-label {
            color: #4f748b;
            font-size: 0.7rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        .slot-card-name {
            color: #e85d04;
            font-size: 1.06rem;
            font-weight: 700;
            line-height: 1.22;
            margin-top: 0.28rem;
        }

        .slot-card-name--allocated {
            color: #e85d04;
        }

        .slot-card-name--empty {
            color: #8ea4b3;
        }

        .slot-card-surgery {
            color: #4f748b;
            font-size: 0.82rem;
            font-weight: 500;
            line-height: 1.3;
            margin-top: 0.35rem;
            min-height: 1.9rem;
        }

        .slot-card-surgery-name {
            font-weight: 700;
        }

        .slot-card-surgery--empty {
            color: transparent;
        }

        .request-stat {
            background:
                radial-gradient(circle at top right, rgba(255, 158, 0, 0.14) 0%, rgba(255, 158, 0, 0) 40%),
                radial-gradient(circle at left center, rgba(0, 180, 216, 0.14) 0%, rgba(0, 180, 216, 0) 36%),
                linear-gradient(135deg, rgba(255, 255, 255, 0.98) 0%, rgba(242, 248, 252, 0.98) 100%);
            border: 1px solid rgba(16, 42, 86, 0.14);
            border-radius: 16px;
            box-shadow:
                0 14px 28px rgba(16, 42, 86, 0.10),
                inset 0 1px 0 rgba(255, 255, 255, 0.72);
            margin-bottom: 0.75rem;
            overflow: hidden;
            padding: 0.9rem 1rem;
            position: relative;
            transform: translateY(0);
            transition: transform 180ms ease, box-shadow 180ms ease, border-color 180ms ease;
        }

        .request-stat::before {
            background: linear-gradient(90deg, var(--hero-orange) 0%, var(--hero-amber) 26%, var(--hero-sky) 62%, var(--hero-blue) 100%);
            content: "";
            height: 4px;
            left: 0;
            opacity: 0.98;
            position: absolute;
            right: 0;
            top: 0;
        }

        .request-stat::after {
            background:
                linear-gradient(120deg, rgba(255, 255, 255, 0.22) 0%, rgba(255, 255, 255, 0) 46%),
                linear-gradient(135deg, rgba(2, 48, 113, 0.07) 0%, rgba(2, 48, 113, 0) 56%);
            content: "";
            inset: 0;
            pointer-events: none;
            position: absolute;
        }

        .request-stat > * {
            position: relative;
            z-index: 1;
        }

        .request-stat--warm {
            background:
                radial-gradient(circle at top right, rgba(255, 158, 0, 0.34) 0%, rgba(255, 158, 0, 0) 42%),
                radial-gradient(circle at left center, rgba(0, 180, 216, 0.18) 0%, rgba(0, 180, 216, 0) 36%),
                linear-gradient(135deg, #fff4e8 0%, #fff9f1 36%, #eef8fc 100%);
            border-color: rgba(255, 133, 0, 0.24);
            box-shadow:
                0 16px 30px rgba(255, 145, 0, 0.12),
                0 10px 22px rgba(16, 42, 86, 0.08),
                inset 0 1px 0 rgba(255, 255, 255, 0.76);
        }

        .request-stat--warm .request-stat-label {
            color: #c2601d;
        }

        .request-stat--warm .request-stat-value {
            color: #17304f;
        }

        .request-stat--warm .request-stat-copy {
            color: #6d6f86;
        }

        .request-stat--cool {
            background:
                radial-gradient(circle at top left, rgba(0, 180, 216, 0.26) 0%, rgba(0, 180, 216, 0) 40%),
                radial-gradient(circle at bottom right, rgba(2, 48, 94, 0.14) 0%, rgba(2, 48, 94, 0) 34%),
                linear-gradient(135deg, #eef9ff 0%, #f5fbff 40%, #fff8f1 100%);
            border-color: rgba(0, 119, 182, 0.24);
            box-shadow:
                0 16px 30px rgba(0, 119, 182, 0.12),
                0 10px 22px rgba(16, 42, 86, 0.08),
                inset 0 1px 0 rgba(255, 255, 255, 0.76);
        }

        .request-stat--cool::before {
            background: linear-gradient(90deg, #00b4d8 0%, #0096c7 36%, #0077b6 74%, #ff9100 100%);
        }

        .request-stat--cool .request-stat-label {
            color: #157897;
        }

        .request-stat--cool .request-stat-value {
            color: #163455;
        }

        .request-stat--cool .request-stat-copy {
            color: #627995;
        }

        .request-stat-label {
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.05em;
            text-transform: uppercase;
        }

        .request-stat-value {
            font-size: 1.6rem;
            font-weight: 800;
            line-height: 1.1;
            margin-top: 0.24rem;
        }

        .request-stat-copy {
            font-size: 0.88rem;
            margin-top: 0.22rem;
        }

        .request-day-heading {
            color: var(--app-ink);
            font-size: 1.05rem;
            font-weight: 800;
            margin: 0.9rem 0 0.2rem;
        }

        .request-day-copy {
            color: var(--app-muted);
            font-size: 0.9rem;
            margin-bottom: 0.6rem;
        }

        .request-card {
            background: #f8f8f8;
            border: 1px solid #828282;
            border-radius: 16px;
            box-shadow: 0 10px 24px rgba(79, 116, 139, 0.14);
            display: flex;
            flex-direction: column;
            justify-content: flex-start;
            margin-bottom: 0.65rem;
            min-height: 7.1rem;
            padding: 0.65rem 0.85rem 0.72rem;
            transform: translateY(0);
            transition: transform 180ms ease, box-shadow 180ms ease, border-color 180ms ease;
        }

        .request-card-top {
            align-items: flex-start;
            display: flex;
            gap: 0.75rem;
            justify-content: space-between;
        }

        .request-card-title {
            color: var(--app-ink);
            font-size: 1.06rem;
            font-weight: 800;
            line-height: 1.2;
        }

        .request-card-session {
            color: var(--app-accent);
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            margin-top: 0.2rem;
        }

        .request-status-pill {
            border-radius: 999px;
            display: inline-block;
            font-size: 0.74rem;
            font-weight: 700;
            padding: 0.28rem 0.62rem;
            white-space: nowrap;
        }

        .request-status-pill--pending {
            background: var(--status-orange);
            color: #ffffff;
        }

        .request-status-pill--approved {
            background: var(--status-green);
            color: #ffffff;
        }

        .request-status-pill--rejected {
            background: var(--status-red);
            color: #ffffff;
        }

        .request-status-pill--default {
            background: #eff6ff;
            color: #1d4ed8;
        }

        .request-meta {
            color: var(--app-muted);
            font-size: 0.9rem;
            line-height: 1.45;
            margin-top: 0.75rem;
        }

        .request-meta strong {
            color: var(--app-ink);
        }

        .request-notes {
            background: var(--app-surface);
            border-radius: 14px;
            color: var(--app-ink);
            font-size: 0.9rem;
            line-height: 1.45;
            margin-top: 0.8rem;
            padding: 0.75rem 0.85rem;
        }

        .public-request-grid {
            margin: 0.7rem 0 1rem;
        }

        .public-request-card {
            background: #f8f8f8;
            border: 1px solid #828282;
            border-radius: 16px;
            box-shadow: 0 10px 24px rgba(79, 116, 139, 0.14);
            display: flex;
            flex-direction: column;
            justify-content: flex-start;
            margin: 0.2rem 0.15rem 0.65rem;
            min-height: 7.1rem;
            padding: 0.65rem 0.85rem 0.72rem;
            transform: translateY(0);
            transition: transform 180ms ease, box-shadow 180ms ease, border-color 180ms ease;
        }

        @media (hover: hover) {
            .app-hero:hover,
            .app-band:hover,
            .request-stat:hover,
            .slot-card:hover,
            .request-card:hover,
            .public-request-card:hover {
                transform: translateY(3px);
                box-shadow: 0 4px 12px rgba(79, 116, 139, 0.12), inset 0 1px 0 rgba(255, 255, 255, 0.35);
            }

            .app-hero:hover {
                border-color: rgba(16, 42, 86, 0.26);
                box-shadow:
                    0 10px 22px rgba(16, 42, 86, 0.14),
                    inset 0 1px 0 rgba(255, 255, 255, 0.45);
            }

            .app-hero--primary:hover {
                border-color: rgba(255, 109, 0, 0.34);
                box-shadow:
                    0 12px 24px rgba(255, 109, 0, 0.14),
                    0 8px 20px rgba(2, 48, 94, 0.12),
                    inset 0 1px 0 rgba(255, 255, 255, 0.45);
            }

            .app-hero--welcome:hover {
                border-color: rgba(0, 119, 182, 0.34);
                box-shadow:
                    0 12px 24px rgba(0, 119, 182, 0.16),
                    0 8px 20px rgba(16, 42, 86, 0.11),
                    inset 0 1px 0 rgba(255, 255, 255, 0.45);
            }

            .app-band:hover {
                border-color: rgba(255, 109, 0, 0.28);
                box-shadow:
                    0 12px 24px rgba(255, 145, 0, 0.12),
                    0 8px 20px rgba(16, 42, 86, 0.10),
                    inset 0 1px 0 rgba(255, 255, 255, 0.45);
            }

            .request-stat:hover {
                border-color: rgba(16, 42, 86, 0.24);
                box-shadow:
                    0 10px 22px rgba(16, 42, 86, 0.14),
                    inset 0 1px 0 rgba(255, 255, 255, 0.45);
            }

            .request-stat--warm:hover {
                border-color: rgba(255, 109, 0, 0.34);
                box-shadow:
                    0 12px 24px rgba(255, 109, 0, 0.16),
                    0 8px 20px rgba(16, 42, 86, 0.10),
                    inset 0 1px 0 rgba(255, 255, 255, 0.45);
            }

            .request-stat--cool:hover {
                border-color: rgba(0, 119, 182, 0.34);
                box-shadow:
                    0 12px 24px rgba(0, 119, 182, 0.16),
                    0 8px 20px rgba(16, 42, 86, 0.10),
                    inset 0 1px 0 rgba(255, 255, 255, 0.45);
            }
        }

        .public-request-card-title {
            color: var(--app-ink);
            font-size: 0.98rem;
            font-weight: 800;
            line-height: 1.2;
        }

        .public-request-card-meta {
            color: var(--app-muted);
            font-size: 0.88rem;
            line-height: 1.45;
            margin-top: 0.45rem;
        }

        .public-request-card-meta strong {
            color: var(--app-ink);
        }

        .future-request-action-gap {
            height: 1.35rem;
        }

        .sidebar-signoff {
            align-items: center;
            display: flex;
            justify-content: center;
            margin-top: 4.5rem;
            padding-bottom: 0.5rem;
        }

        .sidebar-signoff-badge {
            background:
                radial-gradient(circle at top right, rgba(255, 158, 0, 0.28) 0%, rgba(255, 158, 0, 0) 42%),
                radial-gradient(circle at left center, rgba(0, 180, 216, 0.20) 0%, rgba(0, 180, 216, 0) 36%),
                linear-gradient(135deg, #fff5e9 0%, #f7fbff 58%, #eef5ff 100%);
            border: 1px solid rgba(0, 119, 182, 0.24);
            border-radius: 999px;
            box-shadow:
                0 12px 24px rgba(16, 42, 86, 0.12),
                inset 0 1px 0 rgba(255, 255, 255, 0.72);
            color: #17385f;
            display: inline-flex;
            font-size: 0.92rem;
            font-weight: 800;
            letter-spacing: 0.02em;
            line-height: 1;
            padding: 0.68rem 1.05rem;
            text-decoration: none;
        }

        .sidebar-signoff-badge:hover {
            border-color: rgba(255, 109, 0, 0.34);
            box-shadow:
                0 14px 26px rgba(255, 109, 0, 0.16),
                0 8px 18px rgba(16, 42, 86, 0.12),
                inset 0 1px 0 rgba(255, 255, 255, 0.45);
            color: #c75d16;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_section_header(title: str, eyebrow: str | None = None, copy: str | None = None, *, sidebar: bool = False) -> None:
    target = st.sidebar if sidebar else st
    eyebrow_html = f"<div class='app-section-kicker'>{eyebrow}</div>" if eyebrow else ""
    copy_html = f"<div class='app-section-copy'>{copy}</div>" if copy else ""
    target.markdown(
        f"<div class='app-section'>{eyebrow_html}<div class='app-section-title'>{title}</div>{copy_html}</div>",
        unsafe_allow_html=True,
    )


def _render_section_band(title: str, eyebrow: str | None = None, copy: str | None = None) -> None:
    eyebrow_html = f"<div class='app-band-kicker'>{eyebrow}</div>" if eyebrow else ""
    copy_html = f"<div class='app-band-copy'>{copy}</div>" if copy else ""
    st.markdown(
        f"<div class='app-band'>{eyebrow_html}<div class='app-band-title'>{title}</div>{copy_html}</div>",
        unsafe_allow_html=True,
    )


def _render_authenticated_greeting(user: dict[str, str]) -> None:
    display_name = str(user.get("display_name", "") or user.get("email", "") or "there").strip()
    email = str(user.get("email", "") or "").strip()
    role = str(user.get("app_role", "") or "").strip()
    surgery = str(user.get("surgery", "") or "").strip()
    summary_parts = [part for part in [email, role, surgery] if part]
    email_line = f"<div class='app-hero-copy'>{escape(' · '.join(summary_parts))}</div>" if summary_parts else ""
    st.markdown(
        f"""
        <div class="app-hero app-hero--welcome">
            <div class="app-hero-kicker">Welcome</div>
            <div class="app-hero-title">Hello, {escape(display_name)}</div>
            {email_line}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_login_screen() -> bool:
    _apply_app_theme()
    st.sidebar.empty()
    st.markdown(
        """
        <div class="app-hero app-hero--primary">
            <div class="app-hero-kicker">Brompton Health PCN</div>
            <div class="app-hero-title">Sign in to Pharm-Cal</div>
            <div class="app-hero-copy">Use your Supabase email and password to open the booking calendar. Surgery users and pharmacists can both sign in here.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.form("supabase_login_form", clear_on_submit=False, border=False):
        email = st.text_input("Email", placeholder="name@example.com")
        password = st.text_input("Password", type="password", placeholder="Password")
        submitted = st.form_submit_button("Sign in", type="primary", width="stretch")

    if submitted:
        if not email.strip() or not password:
            st.error("Enter both your email address and password.")
            return False
        try:
            st.session_state.auth_user = sign_in_with_email_password(email, password)
            st.session_state["sidebar_collapsed_after_login"] = False
            st.success("Signed in successfully.")
            time.sleep(0.2)
            st.rerun()
        except Exception as exc:
            st.error(f"Unable to sign in with Supabase: {exc}")

    return False


def _require_authenticated_user() -> dict[str, str] | None:
    auth_user = st.session_state.get("auth_user")
    if auth_user:
        return auth_user

    _render_login_screen()
    return None


def _collapse_sidebar_on_authenticated_entry() -> None:
    if st.session_state.get("sidebar_collapsed_after_login", False):
        return

    st.html(
        """
        <script>
        (() => {
            const collapseSidebar = () => {
                const doc = window.parent.document;
                const sidebar = doc.querySelector('[data-testid="stSidebar"]');
                if (!sidebar || sidebar.getAttribute('aria-expanded') !== 'true') {
                    return;
                }

                const toggleButton =
                    doc.querySelector('button[aria-label="Close sidebar"]') ||
                    doc.querySelector('button[kind="header"][aria-expanded="true"]');

                if (toggleButton) {
                    toggleButton.click();
                }
            };

            window.setTimeout(collapseSidebar, 0);
        })();
        </script>
        """
    )
    st.session_state["sidebar_collapsed_after_login"] = True


def _render_slot_card(
    pharmacist_name: str | None,
    *,
    surgery_name: str | None = None,
    available_slot: bool,
    is_booked: bool = False,
) -> None:
    cleaned_name = str(pharmacist_name or "").strip()
    cleaned_surgery = str(surgery_name or "").strip()

    if not available_slot:
        st.markdown("<div class='slot-card-placeholder'></div>", unsafe_allow_html=True)
        return

    if cleaned_name and cleaned_name != "None":
        name = cleaned_name
        name_class = "slot-card-name slot-card-name--allocated"
        card_class = "slot-card" if is_booked else "slot-card slot-card--available"
    elif available_slot:
        name = "Open slot"
        name_class = "slot-card-name"
        card_class = "slot-card slot-card--available"

    surgery_html = (
        f"<div class='slot-card-surgery'>Surgery: <span class='slot-card-surgery-name'>{escape(cleaned_surgery)}</span></div>"
        if cleaned_surgery
        else "<div class='slot-card-surgery slot-card-surgery--empty'>Surgery:</div>"
    )

    st.markdown(
        f"""
        <div class="{card_class}">
            <div class="slot-card-label">Pharmacist</div>
            <div class="{name_class}">{escape(name)}</div>
            {surgery_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _format_datetime(value, fmt: str, fallback: str = "N/A") -> str:
    if pd.isna(value):
        return fallback

    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return fallback

    return parsed.strftime(fmt)


def _normalize_date_range_value(
    value,
    *,
    default_start,
    default_end,
):
    if isinstance(value, tuple) and len(value) == 2:
        start, end = value
    elif isinstance(value, list) and len(value) == 2:
        start, end = value
    elif isinstance(value, datetime):
        start = end = value.date()
    elif hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        start = end = value
    else:
        start, end = default_start, default_end

    if isinstance(start, datetime):
        start = start.date()
    if isinstance(end, datetime):
        end = end.date()

    if start > end:
        start, end = end, start

    return start, end


def _render_request_stat(label: str, value: str, copy: str, tone: str = "warm") -> None:
    st.markdown(
        f"""
        <div class="request-stat request-stat--{escape(tone)}">
            <div class="request-stat-label">{escape(label)}</div>
            <div class="request-stat-value">{escape(value)}</div>
            <div class="request-stat-copy">{escape(copy)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _toggle_sidebar_request_expanders() -> None:
    expander_keys = st.session_state.get("sidebar_request_expander_keys", [])
    if not expander_keys:
        return

    should_open = not all(st.session_state.get(key, False) for key in expander_keys)
    for key in expander_keys:
        st.session_state[key] = should_open

    st.session_state.sidebar_request_expanders_all_open = should_open


def _sync_sidebar_request_expanders() -> None:
    expander_keys = st.session_state.get("sidebar_request_expander_keys", [])
    if not expander_keys:
        st.session_state.sidebar_request_expanders_all_open = False
        return

    st.session_state.sidebar_request_expanders_all_open = all(
        st.session_state.get(key, False) for key in expander_keys
    )


def _request_status_badge(status: str | None) -> str:
    normalized = (status or "Pending").strip() or "Pending"
    tone = normalized.casefold()
    if tone == "pending":
        badge_class = "request-status-pill request-status-pill--pending"
    elif tone == "approved":
        badge_class = "request-status-pill request-status-pill--approved"
    elif tone == "rejected":
        badge_class = "request-status-pill request-status-pill--rejected"
    else:
        badge_class = "request-status-pill request-status-pill--default"

    return f"<span class='{badge_class}'>{escape(normalized.title())}</span>"


def _prepare_future_requests_for_display(future_requests: pd.DataFrame) -> pd.DataFrame:
    requests = future_requests.copy()
    requests["cover_date"] = pd.to_datetime(requests["cover_date"], errors="coerce")
    requests["submission_timestamp"] = pd.to_datetime(requests["submission_timestamp"], errors="coerce")
    if "created_at" in requests.columns:
        requests["created_at"] = pd.to_datetime(requests["created_at"], errors="coerce")
        requests["submitted_at_display"] = requests["submission_timestamp"].where(
            requests["submission_timestamp"].notna(),
            requests["created_at"],
        )
    else:
        requests["submitted_at_display"] = requests["submission_timestamp"]
    requests["status"] = (
        requests["status"]
        .fillna("Pending")
        .astype(str)
        .str.strip()
        .replace("", "Pending")
    )
    requests = requests.dropna(subset=["cover_date"]).sort_values(
        by=["cover_date", "submitted_at_display"],
        kind="stable",
    )

    return requests


def _request_submitted_at_value(request: pd.Series) -> object:
    for field in ["submitted_at_display", "submission_timestamp", "created_at"]:
        value = request.get(field)
        if not pd.isna(value):
            return value
    return None


def _future_request_card_markup(request: pd.Series) -> str:
    notes = str(request.get("desc", "") or "").strip()
    if notes and notes.casefold() == str(request.get("reason", "") or "").strip().casefold():
        notes = ""

    requester = escape(str(request.get("name", "") or "Unknown requester").strip())
    requester_email = str(request.get("requester_email", "") or "").strip()
    email_line = f"<br><strong>Email:</strong> {escape(requester_email)}" if requester_email else ""
    surgery = escape(str(request.get("surgery", "") or "Unknown surgery").strip())
    session = escape(str(request.get("session", "") or "Session not set").strip())
    reason = escape(str(request.get("reason", "") or "Not provided").strip())
    submitted_at = escape(_format_datetime(_request_submitted_at_value(request), "%d %b %Y %H:%M"))
    notes_html = (
        f"<div class='request-notes'><strong>Notes</strong><br>{escape(notes)}</div>"
        if notes
        else ""
    )

    return f"""
        <div class="request-card">
            <div class="request-card-top">
                <div>
                    <div class="request-card-title">{surgery}</div>
                    <div class="request-card-session">{session}</div>
                </div>
                {_request_status_badge(request.get("status"))}
            </div>
            <div class="request-meta">
                <strong>Requested by:</strong> {requester}{email_line}<br>
                <strong>Reason:</strong> {reason}<br>
                <strong>Submitted:</strong> {submitted_at}
            </div>
            {notes_html}
        </div>
    """


def _future_request_public_card_markup(request: pd.Series) -> str:
    surgery = escape(str(request.get("surgery", "") or "Unknown surgery").strip())
    requester = escape(str(request.get("name", "") or "Unknown requester").strip())
    submitted_at = escape(_format_datetime(_request_submitted_at_value(request), "%d %b %Y %H:%M"))

    return f"""
        <div class="public-request-card">
            <div class="public-request-card-title">{surgery}</div>
            <div class="public-request-card-meta">
                <strong>Requester:</strong> {requester}<br>
                <strong>Requested:</strong> {submitted_at}
            </div>
        </div>
    """


def _render_sidebar_request_action(request: pd.Series, key_prefix: str) -> None:
    request_uuid = str(request.get("uuid", "") or "").strip()
    request_status = str(request.get("status", "") or "Pending").strip()
    requester_email = str(request.get("requester_email", "") or "").strip()
    requester_user_id = str(request.get("requester_user_id", "") or "").strip()
    current_user_id = str(_authenticated_user().get("app_user_id", "") or "").strip()
    can_delete_own_request = (
        bool(current_user_id)
        and requester_user_id == current_user_id
        and request_status.casefold() == "pending"
    )
    send_rejection_emails = bool(st.session_state.get("sidebar_send_rejection_emails", False))

    if request_status.casefold() == "approved":
        st.caption("Approved")
        return

    if request_status.casefold() == "rejected":
        st.caption("Rejected")
        return

    if not requester_email and not can_delete_own_request:
        st.caption("Requester email missing")
        return

    if not request_uuid:
        st.caption("Request ID missing")
        return

    if can_delete_own_request and not _current_user_can_access_all_clinics():
        if st.button(
            "Delete Request",
            key=f"{key_prefix}_delete_{request_uuid}",
            type="secondary",
            icon=":material/delete:",
            width="stretch",
            help="Delete a pending request that you created.",
        ):
            if delete_cover_request(request_uuid, current_user_id):
                time.sleep(0.3)
                st.rerun()
        return

    reject_col, accept_col = st.columns(2)
    if accept_col.button(
        "Accept Request",
        key=f"{key_prefix}_accept_{request_uuid}",
        type="secondary",
        icon=":material/check_circle:",
        width="stretch",
        help="Approve this future cover request before making the booking.",
    ):
        if accept_cover_request(request_uuid):
            time.sleep(0.3)
            st.rerun()

    if reject_col.button(
        "Reject Request",
        key=f"{key_prefix}_reject_{request_uuid}",
        type="secondary",
        icon=":material/cancel:",
        width="stretch",
        help="Reject this future cover request and optionally notify the requester, based on the sidebar toggle.",
    ):
        if reject_cover_request(request_uuid, send_email=send_rejection_emails):
            time.sleep(0.3)
            st.rerun()


def _render_future_requests_board(future_requests: pd.DataFrame, *, sidebar: bool = False) -> None:
    requests = _prepare_future_requests_for_display(future_requests)

    today = datetime.today().date()
    next_week = today + timedelta(days=7)
    total_requests = len(requests)
    pending_requests = int(requests["status"].str.casefold().eq("pending").sum())
    upcoming_this_week = int(requests["cover_date"].dt.date.le(next_week).sum())
    surgery_names = requests["surgery"].fillna("").astype(str).str.strip()
    surgeries_covered = int(surgery_names[surgery_names != ""].nunique())

    if sidebar:
        stat_row_one = st.sidebar.columns(2)
        stat_row_two = st.sidebar.columns(2)
        with stat_row_one[0]:
            _render_request_stat("Total", str(total_requests), "Future requests", tone="warm")
        with stat_row_one[1]:
            _render_request_stat("Pending", str(pending_requests), "Awaiting review", tone="cool")
        with stat_row_two[0]:
            _render_request_stat("7 days", str(upcoming_this_week), "Due soon", tone="warm")
        with stat_row_two[1]:
            _render_request_stat("Surgeries", str(surgeries_covered), "Distinct sites", tone="cool")

        st.sidebar.caption("Cross-check these requests against the live rota in the main calendar, then book the matching slot.")

        grouped_requests = list(requests.groupby(requests["cover_date"].dt.date, sort=True))
        expander_keys = [f"sidebar_future_request_{cover_date.isoformat()}" for cover_date, _ in grouped_requests]
        st.session_state.sidebar_request_expander_keys = expander_keys

        for index, key in enumerate(expander_keys):
            if key not in st.session_state:
                st.session_state[key] = index < 2

        _sync_sidebar_request_expanders()
        toggle_label = (
            "Collapse all request days"
            if st.session_state.get("sidebar_request_expanders_all_open", False)
            else "Expand all request days"
        )
        st.sidebar.button(
            toggle_label,
            key="sidebar_toggle_request_expanders",
            on_click=_toggle_sidebar_request_expanders,
            type="secondary",
            icon=":material/expand_content:",
        )
        st.sidebar.toggle(
            "Send rejection emails",
            key="sidebar_send_rejection_emails",
            value=st.session_state.get("sidebar_send_rejection_emails", True),
            help="When on, rejecting a future request sends an email to the requester. When off, the request is only marked as rejected.",
        )

        for index, (cover_date, daily_requests) in enumerate(grouped_requests):
            request_count = len(daily_requests)
            pending_today = int(daily_requests["status"].str.casefold().eq("pending").sum())
            expander_key = expander_keys[index]
            with st.sidebar.expander(
                f"{cover_date.strftime('%a %d %b')} · {request_count} request{'s' if request_count != 1 else ''}",
                expanded=st.session_state.get(expander_key, index < 2),
                key=expander_key,
                on_change=_sync_sidebar_request_expanders,
                icon=":material/schedule:"
            ):
                st.caption(f"{pending_today} pending review")
                for _, request in daily_requests.iterrows():
                    st.markdown(_future_request_card_markup(request), unsafe_allow_html=True)
                    _render_sidebar_request_action(request, key_prefix="sidebar_reject_cover_request")
        return

    stat_columns = st.columns(4)
    with stat_columns[0]:
        _render_request_stat("Total requests", str(total_requests), "All future requests on the board.")
    with stat_columns[1]:
        _render_request_stat("Pending review", str(pending_requests), "Still waiting for a decision.")
    with stat_columns[2]:
        _render_request_stat("Next 7 days", str(upcoming_this_week), "Requests landing within the next week.")
    with stat_columns[3]:
        _render_request_stat("Surgeries", str(surgeries_covered), "Distinct surgeries asking for cover.")

    for cover_date, daily_requests in requests.groupby(requests["cover_date"].dt.date, sort=True):
        request_count = len(daily_requests)
        pending_today = int(daily_requests["status"].str.casefold().eq("pending").sum())
        st.markdown(
            f"<div class='request-day-heading'>{escape(cover_date.strftime('%A, %d %B %Y'))}</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div class='request-day-copy'>{request_count} request{'s' if request_count != 1 else ''} scheduled, {pending_today} pending review.</div>",
            unsafe_allow_html=True,
        )

        card_columns = st.columns(2)
        for idx, (_, request) in enumerate(daily_requests.iterrows()):
            card_columns[idx % 2].markdown(
                _future_request_card_markup(request),
                unsafe_allow_html=True,
            )


def show_admin_panel(df):
    df = _normalize_schedule_data(df)
    unbook_mode = False  # Default value
    _render_section_header("Admin Options", eyebrow="Workspace", copy="Manage scheduling, directory data, and analytics.", sidebar=True)
    admin_tab = st.sidebar.radio("Admin Options", [":material/event_available: Manage Availability", ":material/schedule: View Future Requests", "Manage Surgeries", "Manage Users", "Manage Pharmacists", "Surgery Session Plots"], key="admin_options_radio", width="stretch")

    if admin_tab == "Surgery Session Plots":
        st.session_state.view = 'plot'
    else:
        st.session_state.view = 'calendar'

    if admin_tab == ":material/event_available: Manage Availability":
        _render_section_header("Manage Availability", eyebrow="Scheduling", copy="Assign pharmacist availability and protect booked slots.", sidebar=True)
        unbook_mode = st.sidebar.toggle(":material/person_cancel: **Cancel Bookings**", value=False, width="stretch")

        today = datetime.today().date()
        three_months_later = today + timedelta(days=90)

        availability_range = st.sidebar.slider(
            "Select date range for availability",
            min_value=today,
            max_value=three_months_later,
            value=(today, today + timedelta(weeks=4)),
            format="ddd, D MMM YYYY",
            width="stretch"
        )

        pharmacists_df = get_pharmacists_data()
        pharmacist_names = ["None", *_clean_string_values(pharmacists_df, "Name")]

        with st.sidebar.form("availability_form"):
            st.caption("Select the dates and pharmacist slots you want to publish.")

            start_date, end_date = availability_range
            dates_to_show = []
            current_date = start_date
            while current_date <= end_date:
                # convert to datetime before appending
                dates_to_show.append(datetime.combine(current_date, datetime.min.time()))
                current_date += timedelta(days=1)

            current_availability = {}
            if not df.empty:
                df_copy = df.copy()
                df_copy['Date'] = df_copy['Date'].dt.date
                for _, row in df_copy.iterrows():
                    key = (row['Date'], int(row['slot_index']), row['am_pm'])
                    current_availability[key] = {
                        'id': row.get('id', ''),
                        'booked': str(row.get('booked', 'FALSE')).upper() == "TRUE",
                        'surgery': row.get('surgery', ''),
                        'email': row.get('email', ''),
                        'unique_code': row.get('unique_code', ''),
                        'pharmacist_name': row.get('pharmacist_name', 'None'),
                        'pharmacist_id': row.get('pharmacist_id', ''),
                    }

            for date in dates_to_show:
                is_weekend = date.weekday() >= 5
                date_str = date.strftime('%A, %d %B')

                if is_weekend:
                    st.caption(f":gray[{date_str} (Weekend)]")
                    st.divider()
                else:
                    st.markdown(f"**{date_str}**")

                    st.markdown("AM")
                    cols_am = st.columns(3)
                    for i, col in enumerate(cols_am):
                        with col:
                            shift_type = 'am'
                            slot_key = f"avail_{date.strftime('%Y%m%d')}_{shift_type}_{i}"

                            lookup_key = (date.date(), i, shift_type)
                            slot_info = current_availability.get(lookup_key, {'booked': False, 'pharmacist_name': 'None'})
                            is_booked = slot_info['booked']

                            default_pharmacist = slot_info.get('pharmacist_name', 'None')

                            current_options = list(pharmacist_names)
                            if is_booked and default_pharmacist not in current_options:
                                current_options.append(default_pharmacist)

                            if default_pharmacist not in current_options:
                                default_pharmacist = "None"

                            selected_pharmacist = st.selectbox(
                                "Pharmacist",
                                current_options,
                                index=current_options.index(default_pharmacist),
                                key=slot_key,
                                label_visibility="collapsed",
                                disabled=is_weekend or is_booked
                            )

                    st.markdown("PM")
                    cols_pm = st.columns(3)
                    for i, col in enumerate(cols_pm):
                        with col:
                            shift_type = 'pm'
                            slot_key = f"avail_{date.strftime('%Y%m%d')}_{shift_type}_{i}"

                            lookup_key = (date.date(), i, shift_type)
                            slot_info = current_availability.get(lookup_key, {'booked': False, 'pharmacist_name': 'None'})
                            is_booked = slot_info['booked']

                            default_pharmacist = slot_info.get('pharmacist_name', 'None')

                            current_options = list(pharmacist_names)
                            if is_booked and default_pharmacist not in current_options:
                                current_options.append(default_pharmacist)

                            if default_pharmacist not in current_options:
                                default_pharmacist = "None"

                            selected_pharmacist = st.selectbox(
                                "Pharmacist",
                                current_options,
                                index=current_options.index(default_pharmacist),
                                key=slot_key,
                                label_visibility="collapsed",
                                disabled=is_weekend or is_booked
                            )

            submitted = st.form_submit_button("Update Availability", type="primary", icon=":material/save:", width="stretch")
            if submitted:
                try:
                    with st.spinner("Updating availability... This may take a moment."):
                        for date in dates_to_show:
                            if date.weekday() >= 5:
                                continue

                            for i in range(3):
                                for shift_type in ['am', 'pm']:
                                    slot_key = f"avail_{date.strftime('%Y%m%d')}_{shift_type}_{i}"

                                    lookup_key = (date.date(), i, shift_type)
                                    slot_info = current_availability.get(lookup_key, {})
                                    original_pharmacist = slot_info.get('pharmacist_name', 'None')
                                    new_pharmacist = st.session_state.get(slot_key, 'None')

                                    if slot_info.get('booked', False):
                                        continue

                                    if new_pharmacist != original_pharmacist:
                                        save_availability_change(
                                            slot_date=date.date(),
                                            shift_type=shift_type,
                                            slot_index=i,
                                            slot_info=slot_info,
                                            pharmacist_name=new_pharmacist,
                                        )

                    st.success("Availability updated successfully!")
                    time.sleep(1)
                    st.rerun()

                except Exception as e:
                    st.error(f"An error occurred while updating availability: {e}")

    elif admin_tab == "Manage Surgeries":
        _render_section_header("Manage Surgeries", eyebrow="Directory", copy="Keep the surgery directory and list sizes up to date.", sidebar=True)
        with st.sidebar.form("add_surgery_form", clear_on_submit=True):
            new_surgery_name = st.text_input("Surgery Name")
            new_list_size = st.number_input("List Size", min_value=0, step=1)
            add_surgery_submitted = st.form_submit_button("Add Surgery", type="primary", icon=":material/add:", width="stretch")

            if add_surgery_submitted:
                if new_surgery_name:
                    add_surgery_data(new_surgery_name, new_list_size)
                else:
                    st.error("Surgery name is required.")

        st.sidebar.caption("Existing surgeries")
        surgeries_df = get_surgeries_data()
        if not surgeries_df.empty and {'id', 'surgery', 'list_size'}.issubset(surgeries_df.columns):
            surgeries_df = surgeries_df.assign(
                surgery_sort=surgeries_df["surgery"].fillna("").astype(str).str.strip().str.casefold()
            ).sort_values("surgery_sort", kind="stable")
            for idx, row in surgeries_df.iterrows():
                col1, col2 = st.sidebar.columns([0.8, 0.2])
                with col1:
                    user_count = int(row.get("user_count", 0) or 0)
                    st.markdown(
                        f"**{row['surgery']}**<br>List size: {int(row.get('list_size', 0) or 0)}<br>Users: {user_count}",
                        unsafe_allow_html=True,
                    )
                with col2:
                    if st.button(":material/delete:", key=f"delete_surgery_{idx}", type="tertiary", width="stretch"):
                        delete_surgery_data(str(row['id']))
                        st.rerun()
        else:
            st.sidebar.info("No surgeries saved yet.")

    elif admin_tab == "Manage Users":
        _render_section_header("Manage Users", eyebrow="Directory", copy="Add and remove user contacts under each surgery.", sidebar=True)
        surgeries_df = get_surgeries_data()
        surgery_choices = {
            str(row["surgery"]): str(row["id"])
            for _, row in surgeries_df.iterrows()
            if str(row.get("id", "") or "").strip() and str(row.get("surgery", "") or "").strip()
        }

        with st.sidebar.form("add_user_form", clear_on_submit=True):
            new_user_name = st.text_input("User Name")
            new_user_email = st.text_input("User Email")
            selected_surgery_label = st.selectbox("Surgery", [""] + list(surgery_choices.keys()))
            selected_role = st.selectbox("Role", USER_ROLE_OPTIONS)
            add_user_submitted = st.form_submit_button("Add User", type="primary", icon=":material/person_add:", width="stretch")

            if add_user_submitted:
                selected_surgery_id = surgery_choices.get(selected_surgery_label, "")
                if new_user_name and new_user_email and selected_surgery_id:
                    add_user_data(new_user_name, new_user_email, selected_surgery_id, selected_role)
                else:
                    st.error("Name, email, and surgery are required.")

        st.sidebar.caption("Existing users")
        users_df = get_users_data()
        if not users_df.empty and {'id', 'name', 'email', 'surgery', 'role'}.issubset(users_df.columns):
            users_df = users_df.assign(
                surgery_sort=users_df["surgery"].fillna("").astype(str).str.strip().str.casefold(),
                email_sort=users_df["email"].fillna("").astype(str).str.strip().str.casefold(),
            ).sort_values(["surgery_sort", "email_sort"], kind="stable")
            for idx, row in users_df.iterrows():
                col1, col2 = st.sidebar.columns([0.8, 0.2])
                with col1:
                    st.markdown(
                        f"**{row['name']}**<br>{row['email']}<br>{row['surgery']} · {row['role']}",
                        unsafe_allow_html=True,
                    )
                with col2:
                    if st.button(":material/delete:", key=f"delete_user_{idx}", type="tertiary", width="stretch"):
                        delete_user_data(str(row['id']))
                        st.rerun()
        else:
            st.sidebar.info("No users saved yet.")

    elif admin_tab == "Manage Pharmacists":
        _render_section_header("Manage Pharmacists", eyebrow="Directory", copy="Maintain the pharmacist list used across bookings and emails.", sidebar=True)
        with st.sidebar.form("add_pharmacist_form", clear_on_submit=True):
            new_pharmacist_name = st.text_input("Pharmacist Name")
            new_pharmacist_email = st.text_input("Pharmacist Email")
            add_pharmacist_submitted = st.form_submit_button("Add Pharmacist", type="primary", icon=":material/add:", width="stretch")

            if add_pharmacist_submitted:
                if new_pharmacist_name and new_pharmacist_email:
                    add_pharmacist_data(new_pharmacist_name, new_pharmacist_email)
                else:
                    st.error("Pharmacist name is required.")

        st.sidebar.caption("Existing pharmacists")
        pharmacists_df = get_pharmacists_data()
        if not pharmacists_df.empty and {'Name', 'Email'}.issubset(pharmacists_df.columns):
            pharmacists_df = pharmacists_df.assign(
                pharmacist_sort=pharmacists_df["Name"].fillna("").astype(str).str.strip().str.casefold()
            ).sort_values("pharmacist_sort", kind="stable")
            for idx, row in pharmacists_df.iterrows():
                col1, col2 = st.sidebar.columns([0.8, 0.2])
                with col1:
                    st.markdown(f"**{row['Name']}**<br>{row['Email']}", unsafe_allow_html=True)
                with col2:
                    if st.button(":material/delete:", key=f"delete_pharmacist_{idx}", type="tertiary", width="stretch"):
                        delete_pharmacist_data(row['Name'], row['Email'])
                        st.rerun()
        else:
            st.sidebar.info("No pharmacists saved yet.")
    elif admin_tab == "Surgery Session Plots":
        _render_section_header("Surgery Session Plots", eyebrow="Analytics", copy="Switch between activity views using a single control.", sidebar=True)
        st.session_state.plot_type = st.sidebar.radio(
            "Select Plot Type",
            [
                "Absolute Session Plot",
                "Normalized Sessions per 1000 pts",
                "Monthly Session Share (%)",
                "Future Request Approval/Rejection Rates",
            ],
            width="stretch",
        )
    elif admin_tab == ":material/schedule: View Future Requests":
        _render_section_header("Future Cover Requests", eyebrow="Requests", copy="Keep requests visible here while booking against the live calendar.", sidebar=True)
        get_cover_requests_data.clear()
        cover_requests_df = get_cover_requests_data()

        required_columns = {'cover_date', 'surgery', 'name', 'session', 'reason', 'desc', 'submission_timestamp', 'status'}
        if not cover_requests_df.empty and required_columns.issubset(cover_requests_df.columns):
            selected_range = st.session_state.get("date_range")
            if isinstance(selected_range, tuple) and len(selected_range) == 2:
                range_start, range_end = selected_range
            else:
                fallback_today = datetime.today().date()
                range_start = fallback_today
                range_end = fallback_today + timedelta(days=90)

            future_requests = cover_requests_df[
                (cover_requests_df['cover_date'].dt.date >= range_start)
                & (cover_requests_df['cover_date'].dt.date <= range_end)
            ].sort_values(by='submission_timestamp')

            st.sidebar.caption(f"Showing requests from {range_start.strftime('%d %b %Y')} to {range_end.strftime('%d %b %Y')}.")
            if not future_requests.empty:
                _render_future_requests_board(future_requests, sidebar=True)
            else:
                st.sidebar.info("No future cover requests found in the selected date range.")
        else:
            st.sidebar.info("No cover requests submitted yet.")

    return unbook_mode

@st.dialog("Booking Details")
def show_booking_dialog(slot):
    if _current_user_is_pharmacist():
        st.info("Pharmacist accounts have view-only access and cannot create surgery bookings.")
        return

    shift = slot['am_pm'].upper()
    pharmacist_name = slot.get('pharmacist_name', 'Pharmacist') # Default to 'Pharmacist' if name is not available
    slot_key = _slot_identity(slot)

    st.markdown(f"**Booking: {pharmacist_name} — {shift} on {pd.to_datetime(slot['Date']).strftime('%Y-%m-%d')}**")

    surgeries_df = get_surgeries_data()
    if surgeries_df.empty or "id" not in surgeries_df.columns or "surgery" not in surgeries_df.columns:
        st.warning("No surgeries are configured yet. Add a surgery in the Admin Panel before booking.")
        return

    allow_all_clinics = _current_user_can_access_all_clinics()
    current_surgery_id = _current_user_surgery_id()
    current_user = _authenticated_user()
    current_user_app_user_id = str(current_user.get("app_user_id", "") or "").strip()
    current_user_name = str(current_user.get("name", "") or "").strip()
    current_user_email = str(current_user.get("email", "") or "").strip()
    if not allow_all_clinics and current_surgery_id:
        surgeries_df = surgeries_df[surgeries_df["id"].astype(str) == current_surgery_id].copy()

    surgery_records = []
    for _, row in surgeries_df.iterrows():
        surgery_id = str(row.get("id", "") or "").strip()
        surgery_name = str(row.get("surgery", "") or "").strip()
        if not surgery_id or not surgery_name:
            continue
        selected_user_id = str(row.get("primary_user_id", "") or "").strip()
        selected_user_name = str(row.get("primary_user_name", "") or "").strip()
        selected_user_email = str(row.get("primary_user_email", "") or "").strip()

        if (
            not allow_all_clinics
            and surgery_id == current_surgery_id
            and current_user_app_user_id
        ):
            selected_user_id = current_user_app_user_id
            selected_user_name = current_user_name
            selected_user_email = current_user_email

        if allow_all_clinics:
            label = (
                f"{surgery_name} ({selected_user_email})"
                if selected_user_email
                else f"{surgery_name} (No linked user)"
            )
        else:
            label = surgery_name
        surgery_records.append(
            {
                "id": surgery_id,
                "label": label,
                "surgery": surgery_name,
                "primary_user_id": selected_user_id,
                "primary_user_name": selected_user_name,
                "primary_user_email": selected_user_email,
            }
        )

    if not surgery_records:
        st.warning("No clinics are available for your account.")
        return

    option_labels = [option["label"] for option in surgery_records]
    selected_surgery_option = st.selectbox(
        "Select Surgery",
        option_labels,
        key=f"select_surgery_{slot_key}"
    )
    selected_option = next(
        option for option in surgery_records if option["label"] == selected_surgery_option
    )

    st.text_input("Surgery Name", value=selected_option["surgery"], disabled=True, key=f"display_surgery_{slot_key}_{selected_option['id']}")
    if selected_option["primary_user_email"]:
        st.text_input("Contact Email", value=selected_option["primary_user_email"], disabled=True, key=f"display_email_{slot_key}_{selected_option['id']}")
        st.text_input("Primary Contact", value=selected_option["primary_user_name"], disabled=True, key=f"display_contact_{slot_key}_{selected_option['id']}")
    else:
        st.info("This clinic has no assigned user contact. The booking will still be saved without a surgery email.")

    action_left, action_right = st.columns(2)
    cancel_button = action_left.button("Cancel", type="secondary", width="stretch", key=f"cancel_booking_dialog_{slot_key}")
    submitted = action_right.button("Submit Booking", type="primary", icon=":material/check_circle:", width="stretch", key=f"submit_booking_dialog_{slot_key}")

    if submitted:
        if not selected_option["id"]:
            st.error("All fields are required.")
        else:
            update_booking(
                slot,
                selected_option["id"],
                selected_option["primary_user_id"] or None,
            )
            st.success("Booking saved successfully!")
            time.sleep(1.5)
            st.rerun() # Rerun to close dialog and refresh main app

    if cancel_button:
        st.rerun() # Rerun to close dialog

@st.dialog("Request Cover")
def show_cover_request_dialog(cover_date):
    if _current_user_is_pharmacist():
        st.info("Future cover requests can only be submitted by surgery contacts.")
        return

    st.markdown(f"Requesting cover for: **{cover_date.strftime('%A, %d %B %Y')}**")

    users_df = get_users_data()
    allow_all_clinics = _current_user_can_access_all_clinics()
    current_surgery_id = _current_user_surgery_id()
    current_user_app_user_id = _current_user_app_user_id()
    if not allow_all_clinics and current_surgery_id:
        users_df = users_df[users_df["surgery_id"].astype(str) == current_surgery_id].copy()
    surgery_options = _build_user_options(users_df)
    if not surgery_options:
        st.warning("No clinic contacts are available for your account.")
        return

    date_key = cover_date.strftime('%Y%m%d')
    surgery_key = f"cover_surgery_{date_key}"
    name_key = f"cover_name_{date_key}"
    email_key = f"cover_email_{date_key}"
    prefill_source_key = f"cover_prefill_source_{date_key}"

    available_labels = [option["label"] for option in surgery_options]
    selected_option_index = 0
    if current_user_app_user_id:
        matched_index = next(
            (
                index
                for index, option in enumerate(surgery_options)
                if str(option.get("id", "") or "").strip() == current_user_app_user_id
            ),
            None,
        )
        if matched_index is not None:
            selected_option_index = matched_index

    if not allow_all_clinics and available_labels:
        selected_surgery = st.selectbox(
            "Select Surgery",
            available_labels,
            key=surgery_key,
            index=selected_option_index,
            disabled=True,
        )
    else:
        selected_surgery = st.selectbox(
            "Select Surgery",
            [""] + available_labels,
            key=surgery_key,
            index=selected_option_index + 1 if available_labels else 0,
        )
    selected_option = next((option for option in surgery_options if option["label"] == selected_surgery), None)
    prefilled_name = selected_option["name"] if selected_option else ""
    prefilled_email = selected_option["email"] if selected_option else ""

    if st.session_state.get(prefill_source_key) != selected_surgery:
        st.session_state[name_key] = prefilled_name
        st.session_state[email_key] = prefilled_email
        st.session_state[prefill_source_key] = selected_surgery

    with st.form(key=f"form_cover_request_{date_key}"):
        requested_by_name = st.text_input(
            "Requested by (Your Name)",
            key=name_key
        )
        requested_by_email = st.text_input(
            "Requester Email",
            key=email_key,
            disabled=True,
        )

        selected_session = st.selectbox(
            "Session",
            ['AM', 'PM', 'Full-day'],
            key=f"cover_session_{date_key}"
        )

        reason_options = ['Annual Leave', 'Study Leave', 'Other']
        selected_reason = st.selectbox(
            "Reason",
            reason_options,
            key=f"cover_reason_{date_key}"
        )

        other_reason_text = ""
        if selected_reason == "Other":
            other_reason_text = st.text_input(
                "Please specify other reason",
                key=f"other_reason_text_{date_key}"
            )

        action_left, action_right = st.columns(2)
        cancel_button = action_left.form_submit_button("Cancel", type="secondary", width="stretch")
        submitted = action_right.form_submit_button("Submit Request", type="primary", icon=":material/send:", width="stretch")

        if submitted:
            requested_by_name = requested_by_name.strip()
            requested_by_email = requested_by_email.strip()
            other_reason_text = other_reason_text.strip()
            final_reason = selected_reason
            final_description = "" # Initialize final_description

            if selected_reason == "Other":
                if not other_reason_text:
                    st.error("Please specify the other reason.")
                    st.stop()
                final_description = other_reason_text
                final_reason = other_reason_text # Store the specific reason if "Other" is selected
            else:
                final_description = selected_reason # Use the selected reason as description if not "Other"

            if not selected_option or not requested_by_name or not requested_by_email or not selected_session or not final_reason:
                st.error("All fields are required.")
            else:
                add_cover_request_data(
                    cover_date,
                    selected_option["id"],
                    requested_by_name,
                    selected_session,
                    final_reason,
                    final_description,
                )
                time.sleep(0.2)
                st.rerun() # Rerun to close dialog and refresh main app

        if cancel_button:
            st.rerun() # Rerun to close dialog

def display_calendar(auth_user: dict[str, str], unbook_mode: bool = False):
    _apply_app_theme()
    _collapse_sidebar_on_authenticated_entry()
    raw_schedule_data = get_schedule_data()
    df = _normalize_schedule_data(raw_schedule_data)
    is_pharmacist_account = str(auth_user.get("account_type", "") or "").strip().casefold() == "pharmacist"
    can_book_slots = not is_pharmacist_account
    can_submit_future_requests = not is_pharmacist_account

    def _render_top_plot(plot_type: str, key_prefix: str) -> None:
        previous_plot_type = st.session_state.get("plot_type", "Absolute Session Plot")
        st.session_state.plot_type = plot_type
        try:
            display_plot(df, get_surgeries_data, get_cover_requests_data, heading=None, key_prefix=key_prefix)
        finally:
            st.session_state.plot_type = previous_plot_type

    c1, c2, c3 = st.columns([0.25, 0.25, 2], gap="small")
    with c1:
        with st.popover(':material/info:'):
            with st.container(width=700):
                _render_top_plot("Future Request Approval/Rejection Rates", "popover_future")
    with c2:
        with st.popover(':material/event:'):
            with st.container(width=700):
                _render_top_plot("Monthly Session Share (%)", "popover_monthly")
    with c3:
        with st.popover(':material/bar_chart:'):
            with st.container(width=700):
                if 'surgery' in raw_schedule_data.columns:
                    display_normalized_sessions_plot(lambda: raw_schedule_data, get_surgeries_data, key_prefix="popover_normalized")
                else:
                    st.warning("No surgery data to display.")





    st.logo('images/logo223.png', size="large")
    with st.sidebar:
        st.caption(f"Signed in as {auth_user.get('email', '')}")
        if st.button("Log out", icon=":material/logout:", width="stretch"):
            try:
                sign_out_authenticated_user()
            except Exception:
                pass
            st.session_state.pop("auth_user", None)
            st.session_state.pop("sidebar_collapsed_after_login", None)
            st.rerun()

    is_superuser = _current_user_can_access_all_clinics()
    if not is_superuser:
        st.sidebar.image('images/logo22.png')

    today = datetime.today().date()
    yesterday = today - timedelta(days=1)
    min_data_date = df['Date'].min().date() if not df.empty else yesterday
    slider_min_date = min(min_data_date, yesterday)
    slider_max_date = (pd.Timestamp(yesterday) + pd.DateOffset(months=6)).date()
    default_start_date = yesterday
    default_end_date = (pd.Timestamp(yesterday) + pd.DateOffset(months=3)).date()
    default_date_range = (default_start_date, min(default_end_date, slider_max_date))
    current_day_key = today.isoformat()

    # Initialize view state if not already set
    if 'view' not in st.session_state:
        st.session_state.view = 'calendar'
    if 'plot_type' not in st.session_state:
        st.session_state.plot_type = "Absolute Session Plot" # Default plot type
    if st.session_state.get('date_range_initialized_for_day') != current_day_key:
        st.session_state.date_range = default_date_range
        st.session_state.date_range_initialized_for_day = current_day_key

    st.session_state.date_range = _normalize_date_range_value(
        st.session_state.get("date_range"),
        default_start=default_start_date,
        default_end=min(default_end_date, slider_max_date),
    )

    if is_superuser:
        unbook_mode = show_admin_panel(df)
    else:
        st.session_state.view = 'calendar'

    # Display content based on the selected view
    if st.session_state.view == 'plot':
        display_plot(df, get_surgeries_data, get_cover_requests_data, key_prefix="main") # Pass supporting data getters for analytics
        return

    # --- Main Calendar Display ---
    st.markdown(
        """
        <div class="app-hero app-hero--primary">
            <div class="app-hero-kicker">Brompton Health PCN</div>
            <div class="app-hero-title">Request a Pharmacist Session</div>
            <div class="app-hero-copy">Browse advertised sessions, book available slots, and submit requests beyond the current schedule.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _render_authenticated_greeting(auth_user)
    if is_pharmacist_account:
        st.info("You are signed in as a pharmacist. This view is read-only, so you can review the rota without creating bookings or cover requests.")

    if is_superuser and st.session_state.get("admin_options_radio") == "View Future Requests":
        _render_section_band(
            "Review And Book",
            eyebrow="Admin Workflow",
            copy="Use the sidebar request board to cross-check dates, then book the matching pharmacist slot here.",
        )

    if df.empty:
        st.info("No pharmacist shifts have been scheduled yet. Contact admin.")
        return

    current_user_surgery_name = _current_user_surgery_name()

    # Collapse duplicate rows for the same slot so booked sessions never surface as bookable alternatives.
    df_sorted = _collapse_schedule_slots_for_display(
        df.sort_values(['Date', 'am_pm', 'slot_index'], kind="stable"),
        preferred_surgery_name=current_user_surgery_name,
    )

    # For 'future requests', we still need to know the last advertised date from today onwards.
    upcoming = df[df['Date'] >= datetime.today()].sort_values(['Date', 'am_pm', 'slot_index'])

    if upcoming.empty:
        st.info("No upcoming shifts available.")
        last_advertised_date = datetime.today().date() # If no upcoming, start from today
    else:
        last_advertised_date = upcoming['Date'].max().date()

    _render_section_header("Available Sessions", eyebrow="Schedule", copy="Filter the live rota and book an available pharmacist slot.")
    selected_range = st.slider(
        "Select a date range to view",
        min_value=slider_min_date,
        max_value=slider_max_date,
        format="ddd, D MMM YYYY",
        width="stretch",
        key="date_range",
    )
    selected_range = _normalize_date_range_value(
        selected_range,
        default_start=default_start_date,
        default_end=min(default_end_date, slider_max_date),
    )
    show_surgery_toggle = _can_toggle_surgery_calendar_view()
    surgery_view_only = False
    if show_surgery_toggle:
        toggle_key = f"calendar_surgery_only_{_current_user_surgery_id()}"
        surgery_view_only = st.toggle(
            f":material/visibility: View **{current_user_surgery_name}** sessions only",
            key=toggle_key,
            value=bool(st.session_state.get(toggle_key, False)),
        )

    # Filter schedule based on the selected date range
    schedule_filtered = df_sorted[
        (df_sorted['Date'].dt.date >= selected_range[0]) &
        (df_sorted['Date'].dt.date <= selected_range[1])
    ]
    hidden_booked_sessions = 0
    if surgery_view_only:
        schedule_filtered, hidden_booked_sessions = _filter_schedule_for_surgery_view(
            schedule_filtered,
            current_user_surgery_name,
        )
        displayed_surgery_sessions = int(
            (
                schedule_filtered["surgery"].fillna("").astype(str).str.strip().str.casefold()
                == current_user_surgery_name.strip().casefold()
            ).sum()
        )
        if hidden_booked_sessions or displayed_surgery_sessions:
            st.caption(
                f"Showing available sessions plus bookings for {current_user_surgery_name}. "
                f"**{displayed_surgery_sessions} session(s)** for **{current_user_surgery_name}** are currently displayed, "
                f"and {hidden_booked_sessions} booked session(s) for other surgeries are hidden."
            )

    # Display existing pharmacist schedule
    if schedule_filtered.empty:
        if surgery_view_only:
            st.info(f"No available or {current_user_surgery_name} sessions found in the selected date range.")
        else:
            st.info("No shifts available in the selected date range.")

    for date, daily in schedule_filtered.groupby(schedule_filtered['Date'].dt.date):
        if date.weekday() >= 5: # Skip weekends for advertised dates
            continue

        st.subheader(f"{date.strftime('%A, %d %B %Y')}")

        # AM Shift
        st.markdown("**AM**")
        am_slots = daily[daily['am_pm'] == 'am']
        am_cols = st.columns(3)
        for i in range(3):
            with am_cols[i]:
                slot_data = am_slots[am_slots['slot_index'] == i]
                if not slot_data.empty:
                    row = slot_data.iloc[0]
                    pharmacist_name = row['pharmacist_name']
                    booked = _is_booked_value(row['booked'])
                    surgery_name = row['surgery'] if booked else None
                    _render_slot_card(
                        pharmacist_name,
                        surgery_name=surgery_name,
                        available_slot=True,
                        is_booked=booked,
                    )
                    btn_label = "09:00 - 12:45"
                    slot_identity = _slot_identity(row)
                    unique_key = f"{slot_identity}_{pharmacist_name}_{i}_am"

                    if unbook_mode:
                        if booked:
                            if st.button(btn_label + " (Cancel)", key=unique_key, type="secondary", width="stretch"):
                                cancel_booking(row.to_dict())
                        else:
                            st.button(btn_label, key=unique_key, disabled=True, width="stretch")
                    else:
                        if booked:
                            st.button(btn_label + " (Booked)", key=unique_key, disabled=True, width="stretch")
                        else:
                            if st.button(btn_label, key=unique_key, type="primary", width="stretch", disabled=not can_book_slots):
                                show_booking_dialog(row.to_dict())
                else:
                    _render_slot_card(None, available_slot=False)
                    st.button("Not Available", disabled=True, key=f"empty_{date.strftime('%Y%m%d')}_am_{i}", width="stretch")

        # PM Shift
        st.markdown("**PM**")
        pm_slots = daily[daily['am_pm'] == 'pm']
        pm_cols = st.columns(3)
        for i in range(3):
            with pm_cols[i]:
                slot_data = pm_slots[pm_slots['slot_index'] == i]
                if not slot_data.empty:
                    row = slot_data.iloc[0]
                    pharmacist_name = row['pharmacist_name']
                    booked = _is_booked_value(row['booked'])
                    surgery_name = row['surgery'] if booked else None
                    _render_slot_card(
                        pharmacist_name,
                        surgery_name=surgery_name,
                        available_slot=True,
                        is_booked=booked,
                    )
                    btn_label = "13:15 - 17:00"
                    slot_identity = _slot_identity(row)
                    unique_key = f"{slot_identity}_{pharmacist_name}_{i}_pm"

                    if unbook_mode:
                        if booked:
                            if st.button(btn_label + " (Cancel)", key=unique_key, type="secondary", width="stretch"):
                                cancel_booking(row.to_dict())
                        else:
                            st.button(btn_label, key=unique_key, disabled=True, width="stretch")
                    else:
                        if booked:
                            st.button(btn_label + " (Booked)", key=unique_key, disabled=True, width="stretch")
                        else:
                            if st.button(btn_label, key=unique_key, type="primary", width="stretch", disabled=not can_book_slots):
                                show_booking_dialog(row.to_dict())
                else:
                    _render_slot_card(None, available_slot=False)
                    st.button("Not Available", disabled=True, key=f"empty_{date.strftime('%Y%m%d')}_pm_{i}", width="stretch")

        st.divider()

    # Add functionality for Practice Managers to submit booking requests beyond the advertised date
    _render_section_band("Submit Future Requests", eyebrow="Beyond Advertised Dates", copy="Request support for sessions that are not yet on the rota.")
    start_date_beyond = max(last_advertised_date + timedelta(days=1), selected_range[0])
    end_date_beyond = selected_range[1]

    get_cover_requests_data.clear()
    cover_requests_df = get_cover_requests_data()
    if "status" in cover_requests_df.columns:
        visible_cover_requests_df = cover_requests_df[
            cover_requests_df["status"].fillna("").astype(str).str.strip().str.casefold() != "rejected"
        ].copy()
    else:
        visible_cover_requests_df = cover_requests_df
    visible_cover_requests_df = _prepare_future_requests_for_display(visible_cover_requests_df)

    if end_date_beyond < start_date_beyond:
        st.info("Move the date range further ahead to view or submit future cover requests.")
        return

    current_date_beyond = start_date_beyond
    while current_date_beyond <= end_date_beyond:
        if current_date_beyond.weekday() < 5: # Only show weekdays
            st.markdown(f"**{current_date_beyond.strftime('%A, %d %B %Y')}**")

            # Display existing cover requests for this date
            if 'cover_date' in visible_cover_requests_df.columns:
                daily_cover_requests = visible_cover_requests_df[
                    visible_cover_requests_df['cover_date'].dt.date == current_date_beyond
                ].sort_values(by='submitted_at_display')
            else:
                daily_cover_requests = pd.DataFrame()

            if not daily_cover_requests.empty:
                public_request_columns = st.columns(2)
                for idx, (_, req_row) in enumerate(daily_cover_requests.iterrows()):
                    with public_request_columns[idx % 2]:
                        st.markdown(
                            _future_request_public_card_markup(req_row),
                            unsafe_allow_html=True,
                        )
                        request_uuid = str(req_row.get("uuid", "") or "").strip()
                        requester_user_id = str(req_row.get("requester_user_id", "") or "").strip()
                        current_user_id = str(_authenticated_user().get("app_user_id", "") or "").strip()
                        request_status = str(req_row.get("status", "") or "Pending").strip().casefold()
                        if (
                            request_uuid
                            and current_user_id
                            and requester_user_id == current_user_id
                            and request_status == "pending"
                        ):
                            if st.button(
                                "Delete Request",
                                key=f"delete_public_request_{request_uuid}",
                                type="secondary",
                                icon=":material/delete:",
                                width="stretch",
                            ):
                                if delete_cover_request(request_uuid, current_user_id):
                                    time.sleep(0.2)
                                    st.rerun()

            st.markdown("<div class='future-request-action-gap'></div>", unsafe_allow_html=True)
            if st.button(
                "Request Cover",
                key=f"interest_{current_date_beyond.strftime('%Y%m%d')}",
                icon=":material/event_upcoming:",
                type="primary",
                width="stretch",
                disabled=not can_submit_future_requests,
            ):
                show_cover_request_dialog(current_date_beyond)
            st.divider()
        current_date_beyond += timedelta(days=1)

if __name__ == "__main__":
    authenticated_user = _require_authenticated_user()
    if authenticated_user:
        display_calendar(authenticated_user)

    st.sidebar.html(
        """
        <div class="sidebar-signoff">
            <a
                class="sidebar-signoff-badge"
                href="https://github.com/janduplessis883"
                target="_blank"
                rel="noopener noreferrer"
            >
                janduplessis883
            </a>
        </div>
        """
    )

"""agentRx Streamlit UI: 1 page - table + transcripts + reminder status.

Run:  ~/myvenv/bin/python -m streamlit run app.py
"""

from __future__ import annotations

import datetime as dt

import streamlit as st

from agent.orchestrator import DISCLAIMER, run_search
from services import reminders as reminder_svc

st.set_page_config(page_title="agentRx", layout="wide")
st.title("agentRx - nearby pharmacy stock + cash price check")
st.caption(DISCLAIMER)

with st.form("search"):
    c1, c2, c3 = st.columns(3)
    drug = c1.text_input("Drug", value="atorvastatin")
    strength = c2.text_input("Strength", value="20mg")
    qty = c3.number_input("Qty (tabs)", min_value=1, value=30, step=1)
    c4, c5 = st.columns(2)
    lat = c4.number_input("Latitude", value=40.7128, format="%.6f")
    lng = c5.number_input("Longitude", value=-74.0060, format="%.6f")
    go = st.form_submit_button("Check 3 pharmacies")

if go:
    with st.spinner("Finding pharmacies and calling (parallel)..."):
        outcome = run_search(drug, strength, int(qty), float(lat), float(lng))
    reminder_svc.save_run(drug, strength, int(qty), float(lat), float(lng), outcome.to_dict())
    st.session_state["outcome"] = outcome

outcome = st.session_state.get("outcome")
if outcome:
    if outcome.needs_human:
        st.warning(f"Handoff to human pharmacist: {outcome.handoff_reason}")
    rows = [
        {
            "Pharmacy": r.pharmacy.name,
            "Address": r.pharmacy.address,
            "Rating": f"{r.pharmacy.rating} ({r.pharmacy.user_ratings_total})",
            "Dist (km)": r.pharmacy.distance_km,
            "In stock": "YES" if r.in_stock else "NO",
            "Cash price ($)": r.price if r.price is not None else "-",
            "Pickup": r.pickup_time or "-",
            "Call": r.call_status,
        }
        for r in outcome.results
    ]
    st.subheader("Comparison")
    st.table(rows)
    with st.expander("Call transcripts / details"):
        for r in outcome.results:
            st.markdown(
                f"**{r.pharmacy.name}** - `{r.call_id}` - "
                f"[transcript]({r.transcript_url}) - {r.raw_notes}"
            )
    st.caption(DISCLAIMER)

st.divider()
st.subheader("Refill reminder")
with st.form("reminder"):
    user_id = st.text_input("User ID", value="demo-user")
    rdrug = st.text_input("Drug for reminder", value="atorvastatin")
    days_supply = st.number_input("Days supply", min_value=1, value=30)
    last_date = st.date_input("Last purchase date", value=dt.date.today())
    set_r = st.form_submit_button("Set reminder")
    if set_r:
        rem = reminder_svc.set_reminder(
            user_id, rdrug, int(days_supply), last_date.isoformat()
        )
        st.success(f"Reminder set: {rem.drug}, remind on {rem.remind_date}")

st.subheader("Reminder status")
rems = reminder_svc.list_reminders()
if rems:
    st.table([r.to_dict() for r in rems])
    del_id = st.number_input("Delete reminder ID", min_value=1, step=1)
    if st.button("Delete reminder"):
        st.success("Deleted." if reminder_svc.delete_reminder(int(del_id)) else "Not found.")
    if st.button("Delete ALL my data"):
        n = reminder_svc.clear_user_data(user_id)
        st.success(f"Deleted {n} reminder(s).")
else:
    st.info("No reminders yet.")

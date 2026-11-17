"""Date-only invoice generation in the authenticated dashboard."""

import logging

import streamlit as st

from modules import database, invoice

log = logging.getLogger(__name__)


def _new_invoice() -> None:
    st.session_state.pop("invoice_result", None)
    st.session_state["invoice_date"] = invoice.current_invoice_date()
    st.session_state["invoice_append_date"] = False


def render_invoice_workspace(email: str) -> None:
    st.subheader("Invoice generator")
    st.caption("Create an Oyoshi invoice. The billing and payment details are filled in for you.")
    with st.container(border=True):
        st.markdown(f"**Bill to:** {invoice.CUSTOMER}")
        st.markdown(f"**Description:** {invoice.DESCRIPTION}")
        st.metric(f"Total ({invoice.CURRENCY})", f"${invoice.AMOUNT_CENTS / 100:.2f}")
        st.caption("WEB WIZARDS SG · UEN 53490172K · DBS Bank · PayNow 82872532")

    if "invoice_date" not in st.session_state:
        st.session_state["invoice_date"] = invoice.current_invoice_date()
    selected_date = st.date_input("Invoice date", key="invoice_date", format="DD/MM/YYYY",
                                  help="Defaults to today's date in Singapore.")
    append_date = st.checkbox("Append date to invoice number", key="invoice_append_date",
                              help="Adds the selected date as YYYYMMDD, for example A1B2C-20261117.")
    st.caption("A unique five-character invoice number is assigned when you generate the PDF.")

    result = st.session_state.get("invoice_result")
    if result and (result["date"] != (selected_date.isoformat() if selected_date else "")
                   or result["append_date"] != append_date):
        st.session_state.pop("invoice_result", None)
        result = None

    if not result and st.button("Generate invoice", key="invoice_generate", type="primary",
                                disabled=selected_date is None):
        try:
            with st.spinner("Creating invoice…"):
                result = invoice.create_invoice(database.init_db(), email, selected_date, append_date)
        except Exception:
            log.exception("Invoice generation failed")
            st.error("The invoice could not be created. Please try again.")
        else:
            st.session_state["invoice_result"] = result
            st.rerun()

    if result:
        st.success(f"Invoice {result['number']} is ready.")
        st.download_button("Download invoice PDF", result["pdf"], f"invoice-{result['number']}.pdf",
                           "application/pdf", key="invoice_download", type="primary")
        st.button("New invoice", key="invoice_new", on_click=_new_invoice)

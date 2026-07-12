import os

BASE_URL = "https://api.siperb.com"
TRIAL_HOURS = 1
TRIAL_MINUTES = 15
EXPIRING_SOON_DAYS = 3
REGISTRAR_PORT = 5060
TRANSPORT_TYPE = "udp"
SBC_HOST = "eu-west-1-sbc-1.siperb.com"
PBX_PASSWORD = "Office@11"

# All secrets loaded from st.secrets (cloud) or env vars (local)
# DO NOT hardcode secrets here — they're in the public repo
SIPERB_PAT = None
SIPERB_PAT_ENCODED = None
RESEND_API_KEY = None
SENDER_EMAIL = None
CONNEXCS_USERNAME = None
CONNEXCS_PASSWORD = None

def load_secrets():
    global SIPERB_PAT, SIPERB_PAT_ENCODED, RESEND_API_KEY, SENDER_EMAIL, CONNEXCS_USERNAME, CONNEXCS_PASSWORD
    try:
        import streamlit as st
        SIPERB_PAT = st.secrets.get("SIPERB_PAT")
        SIPERB_PAT_ENCODED = st.secrets.get("SIPERB_PAT_ENCODED")
        RESEND_API_KEY = st.secrets.get("RESEND_API_KEY")
        SENDER_EMAIL = st.secrets.get("SENDER_EMAIL")
        CONNEXCS_USERNAME = st.secrets.get("CONNEXCS_USERNAME")
        CONNEXCS_PASSWORD = st.secrets.get("CONNEXCS_PASSWORD")
    except Exception:
        pass
    SIPERB_PAT = SIPERB_PAT or os.environ.get("SIPERB_PAT")
    SIPERB_PAT_ENCODED = SIPERB_PAT_ENCODED or os.environ.get("SIPERB_PAT_ENCODED")
    RESEND_API_KEY = RESEND_API_KEY or os.environ.get("RESEND_API_KEY")
    SENDER_EMAIL = SENDER_EMAIL or os.environ.get("SENDER_EMAIL")
    CONNEXCS_USERNAME = CONNEXCS_USERNAME or os.environ.get("CONNEXCS_USERNAME")
    CONNEXCS_PASSWORD = CONNEXCS_PASSWORD or os.environ.get("CONNEXCS_PASSWORD")

load_secrets()
